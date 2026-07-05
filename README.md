# SuchiScan

Payment screenshots in. Clean, verifiable ledger out. Runs 100% on your machine — no cloud, no API keys, no cost.

## One-time setup

**1. Install Tesseract OCR (the free OCR engine)**

- **Windows:** download the installer from https://github.com/UB-Mannheim/tesseract/wiki and run it. `extractor.py` auto-detects the default install location, so no PATH setup needed.
- **Mac:** `brew install tesseract`
- **Linux:** `sudo apt install tesseract-ocr`

**2. Install Python packages** (Python 3.10+ required)

```
pip install -r requirements.txt
```

## Running it

```
python app.py
```

Your browser opens a local page (usually http://127.0.0.1:5000). It only exists on your machine.

## Daily use

1. Drag screenshots into the upload area (or drop files into the `screenshots/` folder and click "Process folder")
2. Extracted payments appear in the list — filter by app or status, search by name or txn ID, and see each one's OCR accuracy %
3. Click any payment to open it: screenshot on the right, extracted fields on the left. Click **Edit** to fix anything the OCR got wrong, then **Save**.
4. Click **Download Excel** for a spreadsheet with a clickable link to every original screenshot

## How it stays trustworthy

- Every row carries an OCR confidence score
- Rows with low confidence, missing amounts, odd transaction IDs, or missing dates are flagged — never silently trusted
- Duplicate transaction IDs are skipped automatically
- Processed screenshots are renamed like `GPay_1500_618827345901.png` and filed into `archive/YYYY-MM/`, so links never break and the folder stays organized

## Files

- `app.py` — local web server (Flask) + API
- `static/` — the UI (plain HTML/CSS/JS, no build step)
- `extractor.py` — OCR + field extraction + validation
- `ledger_store.py` — reads/writes `ledger.csv`; the seam for swapping in a real database later
- `ledger.csv` — your running ledger (created automatically)
- `screenshots/` — inbox for new screenshots
- `archive/` — processed screenshots, organized by month

## Adding a real database later

Storage is deliberately isolated behind `ledger_store.py` so it can be swapped
out without touching the API or UI. See [`DATABASE.md`](DATABASE.md) for the
concrete plan — recommended path, schema, and step-by-step migration.
