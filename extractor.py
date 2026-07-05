"""
PayLedger — core extraction engine
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
    transaction_id: str = ""
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


def ocr_with_confidence(path: Path) -> tuple[str, float]:
    """Returns (text, mean word confidence 0-100)."""
    img = preprocess(Image.open(path))
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    words, confs = [], []
    for word, conf in zip(data["text"], data["conf"]):
        c = float(conf)
        if word.strip() and c >= 0:
            words.append(word)
            confs.append(c)
    text = pytesseract.image_to_string(img)
    mean_conf = sum(confs) / len(confs) if confs else 0.0
    return text, round(mean_conf, 1)


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
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(1).replace(",", "")
    return ""


def extract_txn_id(text: str) -> str:
    patterns = [
        r"UPI\s*(?:transaction\s*)?(?:Ref(?:erence)?\.?\s*(?:No\.?|ID)?|ID)\s*[:\-]?\s*([A-Za-z0-9]{8,25})",
        r"Transaction\s*ID\s*[:\-]?\s*([A-Za-z0-9]{8,25})",
        r"Txn\s*(?:ID|No)\.?\s*[:\-]?\s*([A-Za-z0-9]{8,25})",
        r"Tr\.?\s*ID\s*[:\-]?\s*([A-Za-z0-9]{8,25})",
        r"Order\s*ID\s*[:\-]?\s*([A-Za-z0-9]{8,25})",
        r"Reference\s*(?:No\.?|Number)\s*[:\-]?\s*([A-Za-z0-9]{8,25})",
        r"UTR\s*(?:No\.?|Number)?\s*[:\-]?\s*([A-Za-z0-9]{8,25})",
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
    if not p.transaction_id:
        p.review_reasons.append("Transaction ID not found")
    elif p.app in ("GPay", "PhonePe", "Paytm", "UPI (app unknown)") and not re.fullmatch(
        r"[A-Za-z0-9]{10,25}", p.transaction_id
    ):
        p.review_reasons.append("Transaction ID format unusual")
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
    p.transaction_id = extract_txn_id(text)
    p.date_time = extract_datetime(text)
    p.party, p.direction = extract_party_and_direction(text)
    p.status = extract_status(text)
    validate(p)
    return p


def archive_screenshot(p: Payment, archive_root: Path) -> Path:
    """Move screenshot to archive/YYYY-MM/App_Amount_TxnID.ext and update path."""
    month = datetime.now().strftime("%Y-%m")
    dest_dir = archive_root / month
    dest_dir.mkdir(parents=True, exist_ok=True)
    src = Path(p.file_path)
    safe = lambda s: re.sub(r"[^A-Za-z0-9._-]", "", s) or "NA"
    new_name = f"{safe(p.app)}_{safe(p.amount) or 'NA'}_{safe(p.transaction_id) or 'NA'}{src.suffix}"
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
