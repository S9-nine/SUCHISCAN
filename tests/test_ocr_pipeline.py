import extractor
from tests.conftest import draw_screenshot, requires_tesseract


# ---------- escalation control flow (mocked, no real OCR needed) ----------
#
# These pin down the exact behavior that fixed the "amount not read" bug: PSM 3
# (Tesseract's default) can return 0 confidence on cluttered/photographed
# screenshots, so ocr_with_confidence should escalate through PSM 6 then 11 —
# but only when PSM 3 actually struggles, so clean screenshots stay on the fast,
# single-pass path.

def test_no_escalation_when_default_psm_finds_amount(tiny_image, monkeypatch):
    calls = []

    def fake_attempt(img, psm):
        calls.append(psm)
        return "₹500 paid successfully", 85.0

    monkeypatch.setattr(extractor, "_ocr_attempt", fake_attempt)
    text, conf = extractor.ocr_with_confidence(tiny_image)

    assert calls == [3]
    assert conf == 85.0
    assert extractor.extract_amount(text) == "500"


def test_escalates_and_stops_once_amount_found(tiny_image, monkeypatch):
    calls = []
    responses = {
        3: ("garbled nonsense", 0.0),
        6: ("50,000 received successfully", 60.0),
        11: ("should never be reached", 99.0),
    }

    def fake_attempt(img, psm):
        calls.append(psm)
        return responses[psm]

    monkeypatch.setattr(extractor, "_ocr_attempt", fake_attempt)
    text, conf = extractor.ocr_with_confidence(tiny_image)

    assert calls == [3, 6]  # psm 11 skipped once psm 6 found an amount
    assert conf == 60.0
    assert extractor.extract_amount(text) == "50000"


def test_escalates_through_all_psms_when_none_find_amount(tiny_image, monkeypatch):
    calls = []
    responses = {
        3: ("", 0.0),
        6: ("still nothing useful", 20.0),
        11: ("slightly better nonsense", 35.0),
    }

    def fake_attempt(img, psm):
        calls.append(psm)
        return responses[psm]

    monkeypatch.setattr(extractor, "_ocr_attempt", fake_attempt)
    text, conf = extractor.ocr_with_confidence(tiny_image)

    assert calls == [3, 6, 11]
    assert conf == 35.0  # picks the highest-confidence attempt when no amount was ever found


def test_no_escalation_when_default_confidence_is_adequate_even_without_amount(tiny_image, monkeypatch):
    """A legitimately amount-less screenshot (e.g. a failed-payment screen) shouldn't
    trigger three OCR passes just because extract_amount() came back empty."""
    calls = []

    def fake_attempt(img, psm):
        calls.append(psm)
        return "Payment failed. Please try again.", 75.0

    monkeypatch.setattr(extractor, "_ocr_attempt", fake_attempt)
    extractor.ocr_with_confidence(tiny_image)

    assert calls == [3]


# ---------- end-to-end with real Tesseract (synthetic images, skipped if unavailable) ----------

@requires_tesseract
def test_process_image_extracts_amount_from_clean_synthetic_screenshot(tmp_path):
    path = draw_screenshot(
        tmp_path / "clean.png",
        ["Paid successfully!", "Rs. 1,500", "To Test Merchant", "Transaction ID: TESTTXN1234567"],
    )
    payment = extractor.process_image(path)
    assert payment.amount == "1500"
    assert payment.transaction_id == "TESTTXN1234567"


@requires_tesseract
def test_process_image_extracts_amount_when_currency_symbol_is_dropped(tmp_path):
    path = draw_screenshot(
        tmp_path / "no_symbol.png",
        ["Your Seat is Reserved!", "50,000 received successfully", "Payment ID: pay_TESTID123"],
    )
    payment = extractor.process_image(path)
    assert payment.amount == "50000"
