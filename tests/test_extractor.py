import pytest

from extractor import (
    Payment,
    detect_app,
    extract_amount,
    extract_datetime,
    extract_party_and_direction,
    extract_status,
    extract_txn_id,
    extract_utr,
    validate,
)


# ---------- extract_amount ----------

@pytest.mark.parametrize("text,expected", [
    ("Paid successfully!\n₹ 50,000\nTo CHANCELLOR SSAHE", "50000"),
    ("Rs. 1,234.50 received", "1234.50"),
    ("INR 2,000 debited", "2000"),
    ("z50,000\nTo Someone", "50000"),  # OCR misreads ₹ as a lone z
    ("Z 1,200 credited", "1200"),
    ("Your Seat is Reserved!\n50,000 received successfully", "50000"),  # currency symbol dropped entirely
    ("Transaction ID: 1234567890123", ""),  # plain digit run must not be mistaken for an amount
    ("no amount here at all", ""),
])
def test_extract_amount(text, expected):
    assert extract_amount(text) == expected


def test_extract_amount_prefers_explicit_currency_pattern_over_fallback():
    text = "₹ 500 paid\nRef 12,345,678 for order"
    assert extract_amount(text) == "500"


# ---------- extract_txn_id ----------

@pytest.mark.parametrize("text,expected", [
    ("UPI transaction Ref No: ABCDEF1234567", "ABCDEF1234567"),
    ("Transaction ID: 1234567890ABCDE", "1234567890ABCDE"),
    ("Tr. ID:  110155317288", "110155317288"),
    ("Order ID: ORD98765432", "ORD98765432"),
    ("no transaction info here", ""),
    ("UTR No 123456789012", ""),  # UTR is a distinct field now, not a transaction ID
])
def test_extract_txn_id(text, expected):
    assert extract_txn_id(text) == expected


# ---------- extract_utr ----------

@pytest.mark.parametrize("text,expected", [
    ("UTR No 123456789012", "123456789012"),
    ("UTR: ABCDEF123456", "ABCDEF123456"),
    ("UTR Number 987654321012", "987654321012"),
    ("Transaction ID: 1234567890ABCDE", ""),  # a generic transaction ID is not a UTR
    ("no reference info here", ""),
])
def test_extract_utr(text, expected):
    assert extract_utr(text) == expected


def test_extract_utr_and_txn_id_both_captured_when_both_present():
    text = "Transaction ID: TXNAPPINTERNAL99\nUTR No: 110155317288"
    assert extract_utr(text) == "110155317288"
    assert extract_txn_id(text) == "TXNAPPINTERNAL99"


# ---------- extract_datetime ----------

@pytest.mark.parametrize("text,expected_substring", [
    ("Paid at 05:47PM, 05 Jul '26 IST\nTr. ID: 1", "05:47PM, 05 Jul '26 IST"),
    ("05/07/2026, 10:30 AM", "05/07/2026, 10:30 AM"),
    ("July 5, 2026", "July 5, 2026"),
])
def test_extract_datetime(text, expected_substring):
    assert extract_datetime(text) == expected_substring


def test_extract_datetime_no_match():
    assert extract_datetime("nothing resembling a date") == ""


# ---------- extract_party_and_direction ----------

def test_extract_party_paid_to():
    party, direction = extract_party_and_direction("Paid to John Doe\nUPI Ref: 123")
    assert party == "John Doe"
    assert direction == "Paid"


def test_extract_party_to_only():
    party, direction = extract_party_and_direction("To CHANCELLOR SSAHE\n255805832387027@cnrb")
    assert party == "CHANCELLOR SSAHE"
    assert direction == "Paid"


def test_extract_party_received_from():
    party, direction = extract_party_and_direction("Received from Jane Roe\nUPI Ref: 456")
    assert party == "Jane Roe"
    assert direction == "Received"


def test_extract_party_from_only():
    party, direction = extract_party_and_direction("From KUSHAHAR PRASAD\n7905076724.etb@icici")
    assert party == "KUSHAHAR PRASAD"
    assert direction == "Received"


def test_extract_party_no_match():
    assert extract_party_and_direction("nothing relevant") == ("", "")


# ---------- detect_app ----------

@pytest.mark.parametrize("text,expected", [
    ("Sent via PhonePe", "PhonePe"),
    ("Paid with Google Pay", "GPay"),
    ("GPay transaction", "GPay"),
    ("via Paytm wallet", "Paytm"),
    ("Amazon Pay balance used", "Amazon Pay"),
    ("NEFT transfer to ICICI Bank", "Bank transfer"),
    ("UPI transaction successful", "UPI (app unknown)"),
    ("some random receipt text", "Unknown"),
])
def test_detect_app(text, expected):
    assert detect_app(text) == expected


# ---------- extract_status ----------

@pytest.mark.parametrize("text,expected", [
    ("Payment Failed", "Failed"),
    ("Transaction declined", "Failed"),
    ("Payment Pending", "Pending"),
    ("Processing your request", "Pending"),
    ("Paid successfully!", "Success"),
    ("Amount received", "Success"),
    ("no status keyword", ""),
])
def test_extract_status(text, expected):
    assert extract_status(text) == expected


# ---------- validate ----------

def make_payment(**overrides) -> Payment:
    defaults = dict(
        file="f.png", file_path="/f.png", app="GPay", amount="500",
        date_time="05 Jul '26", transaction_id="ABCDEFGHIJ1234", ocr_confidence=90.0,
    )
    defaults.update(overrides)
    return Payment(**defaults)


def test_validate_clean_payment_not_flagged():
    p = make_payment()
    validate(p)
    assert not p.needs_review
    assert p.review_reasons == []


def test_validate_flags_low_confidence():
    p = make_payment(ocr_confidence=40.0)
    validate(p)
    assert p.needs_review
    assert any("confidence" in r.lower() for r in p.review_reasons)


def test_validate_flags_missing_amount():
    p = make_payment(amount="")
    validate(p)
    assert "Amount not found" in p.review_reasons


def test_validate_flags_implausible_amount():
    p = make_payment(amount="50000000")
    validate(p)
    assert any("implausible" in r for r in p.review_reasons)


def test_validate_flags_non_numeric_amount():
    p = make_payment(amount="not-a-number")
    validate(p)
    assert any("valid number" in r for r in p.review_reasons)


def test_validate_flags_missing_reference():
    p = make_payment(transaction_id="", utr="")
    validate(p)
    assert "No UTR or transaction ID found" in p.review_reasons


def test_validate_does_not_flag_missing_transaction_id_when_utr_present():
    p = make_payment(transaction_id="", utr="110155317288")
    validate(p)
    assert "No UTR or transaction ID found" not in p.review_reasons


def test_validate_flags_unusual_reference_format():
    p = make_payment(app="GPay", transaction_id="!!!", utr="")
    validate(p)
    assert any("unusual" in r for r in p.review_reasons)


def test_validate_checks_utr_format_when_utr_is_the_reference():
    p = make_payment(app="GPay", transaction_id="", utr="!!!")
    validate(p)
    assert any("unusual" in r for r in p.review_reasons)


def test_validate_flags_missing_date():
    p = make_payment(date_time="")
    validate(p)
    assert "Date not found" in p.review_reasons
