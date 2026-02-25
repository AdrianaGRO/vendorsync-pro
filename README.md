# VendorSync Pro

Automates vendor price comparison and publishes a unified price matrix to Google Sheets.

---

## Business Problem

Small business owners and resellers who source from multiple vendors typically spend hours each morning doing the same manual work: downloading price files per vendor, opening each file, and cross-referencing prices in a spreadsheet to find the best deal.

This process has several compounding problems:

- **Time cost.** Comparing prices across three or more vendors manually can take 1–3 hours per day, every day.
- **Human error.** Copying values across spreadsheets introduces mistakes. A misread price or missed row has a direct financial impact.
- **No scalability.** Adding a new vendor means more files, more columns, more manual steps. The process does not scale.
- **Delayed decisions.** By the time the comparison is finished, stock availability may have already changed.

| Metric                     | Before VendorSync        | After VendorSync       | Impact                      |
| -------------------------- | ------------------------ | ---------------------- | --------------------------- |
| **Daily Analysis Time**    | 2–3 hours                | ~5 minutes             | Automates repetitive work   |
| **Human Error Risk**       | Manual mistakes likely   | Automated calculations | Reduces errors              |
| **Vendor Scalability**     | Limited by manual effort | Unlimited              | Handles more vendors easily |
| **Operational Efficiency** | Manual workflow          | Automated pipeline     | Saves time and effort       |

VendorSync solves this by automating the entire workflow — from file retrieval to a published, formatted comparison sheet — with a single command.

---

## Solution Overview

VendorSync reads vendor Excel and CSV files from a shared Google Drive folder, normalizes product identifiers (UPC/EAN), merges all vendor data into a single price matrix using an outer join, and writes the result to a Google Sheet. Lowest prices per product are highlighted automatically.

Adding a new vendor requires only a new entry in `config.json`. No code changes.

---

## How It Works

VendorSync runs a linear four-step pipeline: **Files → Normalize → Merge → Publish.**

Download vendor files from Drive, normalize and clean the data, merge into a single price matrix, then publish to Google Sheets.

```
Google Drive folder
    │
    │  download all .xlsx / .csv vendor files
    ▼
Parse & normalize
    - Force barcodes to string (prevents scientific notation from Excel)
    - Pad UPC to 13 digits for consistent merging with EAN-13
    - Strip whitespace, convert prices to float
    │
    ▼
Outer join merge on BARCODE
    - Every product appears once, even if missing from some vendors
    - Adds LOWEST_PRICE and BEST_VENDOR columns
    │
    ▼
Publish to Google Sheets
    - Clear previous data
    - Upload merged table
    - Apply conditional formatting (lowest price highlighted in green)
    - Freeze header row, auto-resize columns
```

---

## Folder Structure

```
vendorSync/
├── main.py               # Single entry point
├── config.json           # Your configuration (gitignored)
├── config.json.example   # Template with placeholder values
├── google_credentials.json  # Google service account key (gitignored)
├── example_input.csv     # Sample vendor file format
├── requirements.txt
├── LICENSE
└── README.md
```

---

## Configuration

Copy the example file and fill in your values:

```bash
cp config.json.example config.json
```

`config.json` structure:

```json
{
  "drive_folder_id": "YOUR_GOOGLE_DRIVE_FOLDER_ID",
  "output_sheet_id": "YOUR_GOOGLE_SHEET_ID",
  "vendors": [
    {
      "name": "VendorA",
      "upc_column": "UPC",
      "ean_column": "EAN",
      "product_name_column": "PRODUCT_NAME",
      "price_column": "PRICE",
      "qty_column": "QTY"
    },
    {
      "name": "VendorB",
      "upc_column": "product_upc",
      "ean_column": "ean13",
      "product_name_column": "product_name",
      "price_column": "wholesale_price",
      "qty_column": "stock_qty"
    }
  ]
}
```

Each vendor entry maps that vendor's column names to a standard schema. Column names are case-sensitive and must match the headers in the vendor's actual file.

**Finding your IDs:**

- Drive folder ID: the string after `/folders/` in the folder's URL
- Sheet ID: the string between `/d/` and `/edit` in the spreadsheet's URL

---

## Installation

**Requirements:** Python 3.8+, a Google Cloud service account with Drive and Sheets API access.

```bash
git clone https://github.com/yourusername/vendorsync.git
cd vendorsync
python -m venv venv && source venv/bin/activate  # recommended: keeps dependencies isolated from your system Python
pip install -r requirements.txt
```

Place your `google_credentials.json` (downloaded from Google Cloud Console) in the project root. Both `config.json` and `google_credentials.json` are listed in `.gitignore` and will not be committed.

For Google Cloud setup: enable the Drive API and Sheets API, create a service account, and share both your Drive folder (Viewer) and your output Sheet (Editor) with the service account email.

---

## How to Run

```bash
python3 main.py
```

On startup, the script runs a preflight check that verifies `config.json`, `google_credentials.json`, and Python version before touching any external service. If anything is missing, it exits with a clear error message.

A successful run ends with:

```
Done in 20.3 seconds
```

---

## Performance

End-to-end timing (Drive download → clean → merge → Sheets publish):

| Vendors | Products | File Size | Processing Time |
|---------|----------|-----------|-----------------|
| 3       | 5,000    | 0.2 MB    | ~20 seconds     |

*Processing time varies based on network speed and Google API response time.*

---

## Design Decisions

**Single entry point.** Everything runs from `main.py`. There are no helper scripts to run first or separately. The preflight check, pipeline, and error handling are all in one place.

**Config-driven vendor schema.** Vendor column names vary widely in practice. Rather than hardcoding or guessing column names, the config file maps each vendor's actual headers to a standard internal schema. This makes the tool usable without modifying any code.

**Barcode normalization to GTIN-13.** UPC-A (12 digits) is a subset of EAN-13 (13 digits). To enable consistent cross-vendor merging regardless of whether a vendor supplies UPC or EAN, both are normalized to a 13-digit key. This prevents duplicate rows for the same physical product.

**Scientific notation handling.** Excel silently converts long numeric strings like barcodes to scientific notation when saved. The parser forces string dtype on all barcode columns before any numeric conversion happens, which prevents silent data loss during merging.

**Outer join merge.** A product that exists in only one vendor's file still appears in the final output. This is intentional — knowing that only one vendor carries a product is itself useful information.

---

## Future Improvements

- **Email notification on completion or failure.** Currently the only output channel is the terminal and a log file. An email summary after each run would remove the need to manually check for errors, making the tool safe for fully unattended scheduling.
- **Price history tracking.** Each pipeline run overwrites the previous output. Storing a timestamped snapshot per run would allow trend analysis and price-change alerts, giving buyers earlier visibility into cost fluctuations.
- **Scheduled automation.** The tool currently runs on demand. Wrapping it in a cron job or a cloud scheduler (e.g. Google Cloud Scheduler + Cloud Run) would eliminate the manual trigger entirely — the comparison is ready before the workday starts.
- **Log retention.** Logging is already in place. Adding log rotation (e.g. `RotatingFileHandler`) and a configurable retention period would prevent unbounded log growth and make past runs auditable without manual cleanup.

---

## License

MIT License. See [LICENSE](LICENSE) for details.
