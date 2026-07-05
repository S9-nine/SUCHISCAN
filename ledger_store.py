"""
PayLedger — local ledger storage
CSV-backed for now. Every function here is the seam to swap in a real
database later without touching the API layer in app.py.
"""

import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
LEDGER = ROOT / "ledger.csv"
BACKUP_DIR = ROOT / "backups"
MAX_BACKUPS = 20

COLUMNS = ["id", "file", "app", "amount", "date_time", "transaction_id", "party",
           "direction", "status", "ocr_confidence", "needs_review",
           "review_reasons", "notes", "deleted", "file_path", "processed_at"]


def _load_df(include_deleted: bool = True) -> pd.DataFrame:
    if not LEDGER.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(LEDGER, dtype=str).fillna("")
    df["needs_review"] = df["needs_review"].astype(str).str.lower() == "true"
    if "id" not in df.columns:
        df["id"] = ""
    if "notes" not in df.columns:
        df["notes"] = ""
    if "deleted" not in df.columns:
        df["deleted"] = "False"
    df["deleted"] = df["deleted"].astype(str).str.lower() == "true"
    missing = df["id"] == ""
    if missing.any():
        df.loc[missing, "id"] = [uuid.uuid4().hex for _ in range(missing.sum())]
        _save_df(df)
    if not include_deleted:
        df = df[~df["deleted"]]
    return df


def _backup() -> None:
    if not LEDGER.exists():
        return
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    (BACKUP_DIR / f"ledger_{stamp}.csv").write_bytes(LEDGER.read_bytes())
    backups = sorted(BACKUP_DIR.glob("ledger_*.csv"))
    for stale in backups[:-MAX_BACKUPS]:
        stale.unlink(missing_ok=True)


def _save_df(df: pd.DataFrame) -> None:
    _backup()
    df.to_csv(LEDGER, index=False)


def load_all(include_deleted: bool = False) -> list[dict]:
    return _load_df(include_deleted=include_deleted).to_dict(orient="records")


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


def soft_delete(payment_id: str) -> bool:
    return update(payment_id, {"deleted": True})


def restore(payment_id: str) -> bool:
    return update(payment_id, {"deleted": False})


def purge(payment_id: str) -> dict | None:
    df = _load_df()
    mask = df["id"] == payment_id
    if not mask.any():
        return None
    row = df[mask].iloc[0].to_dict()
    df = df[~mask]
    _save_df(df)
    return row


def list_trash() -> list[dict]:
    df = _load_df()
    return df[df["deleted"]].to_dict(orient="records")
