"""
PayLedger — local ledger storage
CSV-backed for now. Every function here is the seam to swap in a real
database later without touching the API layer in app.py.
"""

import uuid
from pathlib import Path

import pandas as pd

LEDGER = Path(__file__).parent / "ledger.csv"

COLUMNS = ["id", "file", "app", "amount", "date_time", "transaction_id", "party",
           "direction", "status", "ocr_confidence", "needs_review",
           "review_reasons", "notes", "file_path", "processed_at"]


def _load_df() -> pd.DataFrame:
    if not LEDGER.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(LEDGER, dtype=str).fillna("")
    df["needs_review"] = df["needs_review"].astype(str).str.lower() == "true"
    if "id" not in df.columns:
        df["id"] = ""
    if "notes" not in df.columns:
        df["notes"] = ""
    missing = df["id"] == ""
    if missing.any():
        df.loc[missing, "id"] = [uuid.uuid4().hex for _ in range(missing.sum())]
        _save_df(df)
    return df


def _save_df(df: pd.DataFrame) -> None:
    df.to_csv(LEDGER, index=False)


def load_all() -> list[dict]:
    return _load_df().to_dict(orient="records")


def get(payment_id: str) -> dict | None:
    df = _load_df()
    row = df[df["id"] == payment_id]
    return row.iloc[0].to_dict() if not row.empty else None


def update(payment_id: str, fields: dict) -> bool:
    df = _load_df()
    mask = df["id"] == payment_id
    if not mask.any():
        return False
    for key, value in fields.items():
        df.loc[mask, key] = value
    _save_df(df)
    return True


def append(row: dict) -> dict:
    row = {c: row.get(c, "") for c in COLUMNS}
    row["id"] = uuid.uuid4().hex
    df = _load_df()
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _save_df(df)
    return row


def known_transaction_ids() -> set:
    df = _load_df()
    return set(df["transaction_id"]) - {""}
