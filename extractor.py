"""
S9SCAN — core extraction engine
Reads payment screenshots, OCRs them, extracts structured fields,
scores confidence, flags rows that need manual review.
"""

import os
import re
import shutil
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

import pytesseract
from PIL import Image, ImageFilter, ImageOps

# Windows installs of Tesseract don't add themselves to PATH. Only fall back
# to the default install location when the binary isn't already resolvable,
# so this stays a no-op on Mac/Linux/anywhere it's properly on PATH.
if shutil.which("tesseract") is None:
    for _candidate in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ):
        if os.path.exists(_candidate):
            pytesseract.pytesseract.tesseract_cmd = _candidate
            break

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
CONFIDENCE_THRESHOLD = 70  # below this, row is flagged for review


@dataclass
class Payment:
    file: str
    file_path: str
    app: str = "Unknown"
    amount: str = ""
    date_time: str = ""
    utr: str = ""             # bank-settlement reference (NEFT/RTGS/IMPS/UPI) — the main reference when present
    transaction_id: str = ""  # app-internal transaction/order ID; secondary to UTR
    party: str = ""
    direction: str = ""          # Paid / Received
    status: str = ""
    ocr_confidence: float = 0.0  # mean word confidence, 0-100
    needs_review: bool = False
    review_reasons: list = field(default_factory=list)
    raw_text: str = ""

    def to_row(self) -> dict:
        d = asdict(self)
        d["review_reasons"] = "; ".join(self.review_reasons)
        return d


# ---------- OCR ----------

def preprocess(img: Image.Image) -> Image.Image:
    """Boost OCR accuracy on low-quality screenshots."""
    if img.width < 900:
        ratio = 900 / img.width
        img = img.resize((900, int(img.height * ratio)), Image.LANCZOS)
    img = img.convert("L")
    img = ImageOps.autocontrast(img)
    img = img.filter(ImageFilter.SHARPEN)
    return img


ESCALATION_CONFIDENCE = 50  # below this (with no amount found), retry with alternate page segmentation


def _ocr_attempt(img: Image.Image, psm: int) -> tuple[str, float]:
    cfg = f"--psm {psm}"
    data = pytesseract.image_to_data(img, config=cfg, output_type=pytesseract.Output.DICT)
    words, confs = [], []
    for word, conf in zip(data["text"], data["conf"]):
        c = float(conf)
        if word.strip() and c >= 0:
            words.append(word)
            confs.append(c)
    text = pytesseract.image_to_string(img, config=cfg)
    mean_conf = sum(confs) / len(confs) if confs else 0.0
    return text, round(mean_conf, 1)


def ocr_with_confidence(path: Path) -> tuple[str, float]:
    """Returns (text, mean word confidence 0-100).

    Tesseract's default page segmentation (PSM 3, "fully automatic") assumes a fairly
    clean page layout and can completely fail (0 words) on cluttered or photographed
    screenshots — busy backgrounds, monitor bezels, decorative UI chrome confuse its
    layout analysis. When that happens, retry with segmentation modes built for
    scattered/sparse text blocks (PSM 6, 11), which recover real text from exactly
    that kind of image. Clean screenshots already score well under the default and
    skip the extra passes.
    """
    img = preprocess(Image.open(path))
    text, conf = _ocr_attempt(img, psm=3)
    if not extract_amount(text) and conf < ESCALATION_CONFIDENCE:
        for psm in (6, 11):
            alt_text, alt_conf = _ocr_attempt(img, psm=psm)
            if alt_conf > conf or (extract_amount(alt_text) and not extract_amount(text)):
                text, conf = alt_text, alt_conf
            if extract_amount(text):
                break
    return text, conf


# ---------- Field extraction ----------

def detect_app(text: str) -> str:
    t = text.lower()
    if "phonepe" in t:
        return "PhonePe"
    if "google pay" in t or "gpay" in t or "g pay" in t:
        return "GPay"
    if "paytm" in t:
        return "Paytm"
    if "amazon pay" in t:
        return "Amazon Pay"
    if any(k in t for k in ("neft", "imps", "rtgs", "a/c no", "account number",
                             "icici", "hdfc", "axis bank", "kotak", " sbi ")):
        return "Bank transfer"
    if "upi" in t:
        return "UPI (app unknown)"
    return "Unknown"


def extract_amount(text: str) -> str:
    patterns = [
        r"[₹]\s*([\d,]+(?:\.\d{1,2})?)",
        r"\bRs\.?\s*([\d,]+(?:\.\d{1,2})?)",
        r"\bINR\s*([\d,]+(?:\.\d{1,2})?)",
        r"(?<![A-Za-z0-9])[zZ]\s?([\d,]{3,}(?:\.\d{1,2})?)",  # OCR often misreads ₹ as a lone z/Z
        r"\b(\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?)\b",  # last resort: currency symbol dropped entirely, but a comma-grouped number is still a strong signal in a payment screenshot
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(1).replace(",", "")
    return ""


def extract_utr(text: str) -> str:
    """UTR (Unique Transaction Reference) — the bank-settlement-level reference for
    NEFT/RTGS/IMPS/UPI transfers. Kept separate from extract_txn_id() because a
    screenshot can show both: an app-internal "Transaction ID" alongside a distinct
    "UTR" used for bank reconciliation. UTR is the main reference when both exist."""
    m = re.search(r"UTR\s*(?:No\.?|Number)?\s*[:\-]?\s*([A-Za-z0-9]{8,25})", text, re.IGNORECASE)
    return m.group(1) if m else ""


def extract_txn_id(text: str) -> str:
    patterns = [
        r"UPI\s*(?:transaction\s*)?(?:Ref(?:erence)?\.?\s*(?:No\.?|ID)?|ID)\s*[:\-]?\s*([A-Za-z0-9]{8,25})",
        r"Transaction\s*ID\s*[:\-]?\s*([A-Za-z0-9]{8,25})",
        r"Txn\s*(?:ID|No)\.?\s*[:\-]?\s*([A-Za-z0-9]{8,25})",
        r"Tr\.?\s*ID\s*[:\-]?\s*([A-Za-z0-9]{8,25})",
        r"Order\s*ID\s*[:\-]?\s*([A-Za-z0-9]{8,25})",
        r"Reference\s*(?:No\.?|Number)\s*[:\-]?\s*([A-Za-z0-9]{8,25})",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1)
    return ""


def extract_datetime(text: str) -> str:
    patterns = [
        r"\d{1,2}:\d{2}\s*(?:am|pm|AM|PM)?,?\s*\d{1,2}\s+[A-Za-z]{3,9}\s+'?\d{2,4}(?:\s+[A-Za-z]{2,4})?",
        r"\d{1,2}\s+[A-Za-z]{3,9}\s+'?\d{2,4}(?:[,\s]+\d{1,2}:\d{2}\s*(?:am|pm|AM|PM)?)?",
        r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}(?:[,\s]+\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm|AM|PM)?)?",
        r"[A-Za-z]{3,9}\s+\d{1,2},?\s+'?\d{2,4}(?:[,\s]+\d{1,2}:\d{2}\s*(?:am|pm|AM|PM)?)?",
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(0).strip()
    return ""


def extract_party_and_direction(text: str) -> tuple[str, str]:
    m = re.search(r"(?:Paid\s+to|To)\s*[:\-]?\s*([A-Za-z][A-Za-z .&']{2,40})", text, re.IGNORECASE)
    if m:
        return _clean_name(m.group(1)), "Paid"
    m = re.search(r"(?:Received\s+from|From)\s*[:\-]?\s*([A-Za-z][A-Za-z .&']{2,40})", text, re.IGNORECASE)
    if m:
        return _clean_name(m.group(1)), "Received"
    return "", ""


def _clean_name(name: str) -> str:
    return re.split(r"\s{2,}|\n", name.strip())[0]


def extract_status(text: str) -> str:
    t = text.lower()
    if "failed" in t or "declined" in t:
        return "Failed"
    if "pending" in t or "processing" in t:
        return "Pending"
    if any(k in t for k in ("success", "completed", "paid", "received")):
        return "Success"
    return ""


# ---------- Validation ----------

def validate(p: Payment) -> None:
    """Flag anything suspicious so the user reviews it instead of trusting silently."""
    if p.ocr_confidence < CONFIDENCE_THRESHOLD:
        p.review_reasons.append(f"Low OCR confidence ({p.ocr_confidence}%)")
    if not p.amount:
        p.review_reasons.append("Amount not found")
    else:
        try:
            val = float(p.amount)
            if val <= 0 or val > 10_000_000:
                p.review_reasons.append("Amount looks implausible")
        except ValueError:
            p.review_reasons.append("Amount not a valid number")
    reference = p.utr or p.transaction_id
    if not reference:
        p.review_reasons.append("No UTR or transaction ID found")
    elif p.app in ("GPay", "PhonePe", "Paytm", "UPI (app unknown)") and not re.fullmatch(
        r"[A-Za-z0-9]{10,25}", reference
    ):
        p.review_reasons.append("Reference number format unusual")
    if not p.date_time:
        p.review_reasons.append("Date not found")
    p.needs_review = bool(p.review_reasons)


# ---------- Pipeline ----------

def process_image(path: Path) -> Payment:
    text, conf = ocr_with_confidence(path)
    p = Payment(file=path.name, file_path=str(path.resolve()),
                ocr_confidence=conf, raw_text=text)
    p.app = detect_app(text)
    p.amount = extract_amount(text)
    p.utr = extract_utr(text)
    p.transaction_id = extract_txn_id(text)
    p.date_time = extract_datetime(text)
    p.party, p.direction = extract_party_and_direction(text)
    p.status = extract_status(text)
    validate(p)
    return p


def archive_screenshot(p: Payment, archive_root: Path) -> Path:
    """Move screenshot to archive/YYYY-MM/App_Amount_Reference.ext and update path."""
    month = datetime.now().strftime("%Y-%m")
    dest_dir = archive_root / month
    dest_dir.mkdir(parents=True, exist_ok=True)
    src = Path(p.file_path)
    safe = lambda s: re.sub(r"[^A-Za-z0-9._-]", "", s) or "NA"
    reference = p.utr or p.transaction_id
    new_name = f"{safe(p.app)}_{safe(p.amount) or 'NA'}_{safe(reference) or 'NA'}{src.suffix}"
    dest = dest_dir / new_name
    i = 1
    while dest.exists():
        dest = dest_dir / f"{dest.stem}_{i}{src.suffix}"
        i += 1
    shutil.move(str(src), str(dest))
    p.file_path = str(dest.resolve())
    p.file = dest.name
    return dest


def process_folder(folder: Path) -> list[Payment]:
    images = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    return [process_image(img) for img in images]


# ---------- debug CLI ----------
#
# When a scan silently mangles a field, this shows exactly what OCR saw and how
# each field extractor parsed it, without going through the Flask app or ledger.
#
#   python extractor.py debug path/to/screenshot.png

def _debug(path: Path) -> None:
    p = process_image(path)
    print(f"file:            {p.file}")
    print(f"ocr_confidence:  {p.ocr_confidence}%")
    print(f"app:             {p.app!r}")
    print(f"amount:          {p.amount!r}")
    print(f"utr:             {p.utr!r}")
    print(f"transaction_id:  {p.transaction_id!r}")
    print(f"date_time:       {p.date_time!r}")
    print(f"party:           {p.party!r}")
    print(f"direction:       {p.direction!r}")
    print(f"status:          {p.status!r}")
    print(f"needs_review:    {p.needs_review}")
    print(f"review_reasons:  {p.review_reasons}")
    print("--- raw OCR text ---")
    print(p.raw_text)


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3 or sys.argv[1] != "debug":
        print("Usage: python extractor.py debug <path/to/screenshot>")
        raise SystemExit(1)
    _debug(Path(sys.argv[2]))
