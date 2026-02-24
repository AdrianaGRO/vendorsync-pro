#!/usr/bin/env python3
"""
VendorSync Pro - Performance Benchmark Script
=============================================
Runs the full pipeline (download → clean → merge → publish) against
3 real test datasets and reports accurate timing metrics.
"""

import time
import json
import logging
import sys
import io
from pathlib import Path

import pandas as pd
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import gspread

# Suppress noisy logs during benchmark
logging.basicConfig(level=logging.WARNING)

CREDENTIALS_PATH = 'google_credentials.json'
OUTPUT_SHEET_ID = '1B-NtT0bzyID0FVhNIGFLmCkHTWYPhRPNsKFUtaMhp70'

# ── Vendor column configs per test scenario ─────────────────────────────────

VENDOR_CONFIGS = {
    'small_3vendors_5k': {
        'folder_id': '1c0GCjZ4cNBtAGrFzZqFmyiBb2kJcRjil',
        'vendors': [
            {'name': 'VENDORA', 'upc_column': 'UPC',             'ean_column': 'EAN',                       'price_column': 'PRICE',          'qty_column': 'QTY'},
            {'name': 'VENDORB', 'upc_column': 'product_upc',     'ean_column': 'ean13',                     'price_column': 'wholesale_price', 'qty_column': 'stock_qty'},
            {'name': 'VENDORC', 'upc_column': 'upc_code',        'ean_column': 'ean_code',                  'price_column': 'unit_price',      'qty_column': 'available_qty'},
        ]
    },
    'medium_5vendors_15k': {
        'folder_id': '1-JPFZSnhp8BrPAc9a6yRv9yjZ8tOSmvC',
        'vendors': [
            {'name': 'VENDORA', 'upc_column': 'UPC',             'ean_column': 'EAN',                       'price_column': 'PRICE',           'qty_column': 'QTY'},
            {'name': 'VENDORB', 'upc_column': 'product_upc',     'ean_column': 'ean13',                     'price_column': 'wholesale_price',  'qty_column': 'stock_qty'},
            {'name': 'VENDORC', 'upc_column': 'upc_code',        'ean_column': 'ean_code',                  'price_column': 'unit_price',       'qty_column': 'available_qty'},
            {'name': 'VENDORD', 'upc_column': 'barcode_upc',     'ean_column': 'barcode_ean',               'price_column': 'cost',             'qty_column': 'inventory_count'},
            {'name': 'VENDORE', 'upc_column': 'upc_12',          'ean_column': 'ean_13',                    'price_column': 'price_per_unit',   'qty_column': 'quantity_available'},
        ]
    },
    'large_10vendors_50k': {
        'folder_id': '1ZtGUUNbE3-4l8PncPZsF4vFdySITxApt',
        'vendors': [
            {'name': 'VENDORA', 'upc_column': 'UPC',                      'ean_column': 'EAN',                        'price_column': 'PRICE',           'qty_column': 'QTY'},
            {'name': 'VENDORB', 'upc_column': 'product_upc',              'ean_column': 'ean13',                      'price_column': 'wholesale_price',  'qty_column': 'stock_qty'},
            {'name': 'VENDORC', 'upc_column': 'upc_code',                 'ean_column': 'ean_code',                   'price_column': 'unit_price',       'qty_column': 'available_qty'},
            {'name': 'VENDORD', 'upc_column': 'barcode_upc',              'ean_column': 'barcode_ean',                'price_column': 'cost',             'qty_column': 'inventory_count'},
            {'name': 'VENDORE', 'upc_column': 'upc_12',                   'ean_column': 'ean_13',                     'price_column': 'price_per_unit',   'qty_column': 'quantity_available'},
            {'name': 'VENDORF', 'upc_column': 'UPC_CODE',                 'ean_column': 'EAN_CODE',                   'price_column': 'WHOLESALE_COST',   'qty_column': 'STOCK_LEVEL'},
            {'name': 'VENDORG', 'upc_column': 'universal_product_code',   'ean_column': 'european_article_number',    'price_column': 'pricing',          'qty_column': 'quantity_in_stock'},
            {'name': 'VENDORH', 'upc_column': 'upc',                      'ean_column': 'ean',                        'price_column': 'unit_cost',        'qty_column': 'qty_on_hand'},
            {'name': 'VENDORI', 'upc_column': 'product_barcode_upc',      'ean_column': 'product_barcode_ean',        'price_column': 'dealer_price',     'qty_column': 'available_stock'},
            {'name': 'VENDORJ', 'upc_column': 'item_upc',                 'ean_column': 'item_ean',                   'price_column': 'vendor_price',     'qty_column': 'inventory_qty'},
        ]
    },
}


def init_services():
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive.readonly'
    ]
    creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=scopes)
    drive  = build('drive', 'v3', credentials=creds)
    sheets = gspread.authorize(creds)
    return drive, sheets


def download_files(drive, folder_id, vendor_configs):
    """Download all vendor files from a Drive folder. Returns {vendor_name: raw_bytes, mime}."""
    query = (
        f"'{folder_id}' in parents and ("
        "mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' "
        "or mimeType='text/csv')"
    )
    results = drive.files().list(q=query, fields='files(id, name, mimeType)').execute()
    files   = results.get('files', [])

    vendor_name_map = {v['name'].upper(): v for v in vendor_configs}
    downloaded = {}

    for f in files:
        stem = Path(f['name']).stem.upper()          # e.g. VENDORA_INVENTORY → VENDORA
        vendor_key = next((k for k in vendor_name_map if stem.startswith(k)), None)
        if not vendor_key:
            continue

        req     = drive.files().get_media(fileId=f['id'])
        buf     = io.BytesIO()
        dl      = MediaIoBaseDownload(buf, req)
        done    = False
        while not done:
            _, done = dl.next_chunk()
        buf.seek(0)
        downloaded[vendor_key] = {'bytes': buf, 'mime': f['mimeType'], 'name': f['name']}

    return downloaded, vendor_name_map


def parse_and_clean(downloaded, vendor_name_map):
    """Parse raw bytes → DataFrames, clean barcodes & prices."""
    cleaned = {}
    for vendor_key, info in downloaded.items():
        vcfg = vendor_name_map[vendor_key]
        info['bytes'].seek(0)

        if 'csv' in info['mime']:
            df = pd.read_csv(info['bytes'], dtype=str)
        else:
            df = pd.read_excel(info['bytes'], dtype=str)

        # Rename columns to standard names
        col_map = {
            vcfg['upc_column']:   'UPC',
            vcfg['ean_column']:   'EAN',
            vcfg['price_column']: 'PRICE',
            vcfg['qty_column']:   'QTY',
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        # Clean barcodes
        for bc in ['UPC', 'EAN']:
            if bc in df.columns:
                df[bc] = df[bc].astype(str).str.strip().str.replace(r'[^\w]', '', regex=True)
                df[bc] = df[bc].replace('nan', pd.NA)

        # Unified BARCODE column
        if 'UPC' in df.columns and 'EAN' in df.columns:
            df['BARCODE'] = df['UPC'].fillna(df['EAN'])
        elif 'UPC' in df.columns:
            df['BARCODE'] = df['UPC']
        else:
            df['BARCODE'] = df['EAN']

        # Clean price & qty
        if 'PRICE' in df.columns:
            df['PRICE'] = pd.to_numeric(
                df['PRICE'].str.replace(',', '.').str.replace(r'[^\d.]', '', regex=True),
                errors='coerce'
            )
        if 'QTY' in df.columns:
            df['QTY'] = pd.to_numeric(df['QTY'], errors='coerce')

        df = df.dropna(subset=['BARCODE'])[['BARCODE', 'PRICE', 'QTY']].copy()
        df = df.rename(columns={'PRICE': f'{vendor_key}_PRICE', 'QTY': f'{vendor_key}_QTY'})
        cleaned[vendor_key] = df

    return cleaned


def merge(cleaned):
    """Outer-join all vendor DataFrames on BARCODE."""
    vendor_list = list(cleaned.keys())
    merged = cleaned[vendor_list[0]].copy()
    for v in vendor_list[1:]:
        merged = merged.merge(cleaned[v], on='BARCODE', how='outer')

    price_cols = [c for c in merged.columns if c.endswith('_PRICE')]
    merged['LOWEST_PRICE'] = merged[price_cols].min(axis=1)
    merged['BEST_VENDOR']  = merged[price_cols].idxmin(axis=1).str.replace('_PRICE', '')

    col_order = ['BARCODE', 'LOWEST_PRICE', 'BEST_VENDOR'] + \
                [c for c in merged.columns if c not in ['BARCODE', 'LOWEST_PRICE', 'BEST_VENDOR']]
    return merged[col_order]


def publish(sheets, merged):
    """Push merged data to Google Sheets (first sheet)."""
    spreadsheet = sheets.open_by_key(OUTPUT_SHEET_ID)
    ws = spreadsheet.sheet1
    ws.clear()
    data = [merged.columns.tolist()] + merged.fillna('').values.tolist()
    ws.update(range_name='A1', values=data, value_input_option='USER_ENTERED')


def run_benchmark(scenario_name, config, drive, sheets):
    print(f"\n{'='*60}")
    print(f"  Running: {scenario_name}")
    print(f"{'='*60}")

    total_start = time.perf_counter()

    # ── Step 1: Download ────────────────────────────────────────
    print("  [1/4] Downloading files from Google Drive...", end=' ', flush=True)
    t0 = time.perf_counter()
    downloaded, vendor_name_map = download_files(drive, config['folder_id'], config['vendors'])
    download_time = time.perf_counter() - t0
    total_file_size = sum(len(v['bytes'].getvalue()) for v in downloaded.values())
    print(f"done ({download_time:.1f}s) — {len(downloaded)} files, {total_file_size/1024/1024:.1f} MB")

    # ── Step 2: Parse & Clean ───────────────────────────────────
    print("  [2/4] Parsing & cleaning data...", end=' ', flush=True)
    t0 = time.perf_counter()
    cleaned = parse_and_clean(downloaded, vendor_name_map)
    clean_time = time.perf_counter() - t0
    total_rows = sum(len(df) for df in cleaned.values())
    print(f"done ({clean_time:.1f}s) — {total_rows:,} rows cleaned")

    # ── Step 3: Merge ───────────────────────────────────────────
    print("  [3/4] Merging vendor data...", end=' ', flush=True)
    t0 = time.perf_counter()
    merged = merge(cleaned)
    merge_time = time.perf_counter() - t0
    print(f"done ({merge_time:.1f}s) — {len(merged):,} unique products")

    # ── Step 4: Publish ─────────────────────────────────────────
    print("  [4/4] Publishing to Google Sheets...", end=' ', flush=True)
    t0 = time.perf_counter()
    publish(sheets, merged)
    publish_time = time.perf_counter() - t0
    print(f"done ({publish_time:.1f}s)")

    total_time = time.perf_counter() - total_start

    return {
        'scenario':      scenario_name,
        'vendors':       len(config['vendors']),
        'products':      len(merged),
        'file_size_mb':  round(total_file_size / 1024 / 1024, 1),
        'download_s':    round(download_time, 1),
        'clean_s':       round(clean_time, 1),
        'merge_s':       round(merge_time, 1),
        'publish_s':     round(publish_time, 1),
        'total_s':       round(total_time, 1),
    }


def format_time(seconds):
    if seconds < 60:
        return f"{seconds:.0f} seconds"
    return f"{seconds/60:.1f} minutes"


def main():
    print("\n🚀 VendorSync Pro — Performance Benchmark")
    print("==========================================")

    drive, sheets = init_services()
    results = []

    for scenario_name, config in VENDOR_CONFIGS.items():
        result = run_benchmark(scenario_name, config, drive, sheets)
        results.append(result)

    # ── Summary Table ────────────────────────────────────────────
    print(f"\n\n{'='*60}")
    print("  BENCHMARK RESULTS SUMMARY")
    print(f"{'='*60}")
    print(f"  {'Scenario':<25} {'Vendors':>7} {'Products':>10} {'Size':>8} {'Total':>12}")
    print(f"  {'-'*25} {'-'*7} {'-'*10} {'-'*8} {'-'*12}")
    for r in results:
        print(f"  {r['scenario']:<25} {r['vendors']:>7} {r['products']:>10,} {r['file_size_mb']:>7.1f}MB {format_time(r['total_s']):>12}")

    print(f"\n  Detailed breakdown (seconds):")
    print(f"  {'Scenario':<25} {'Download':>10} {'Clean':>8} {'Merge':>8} {'Publish':>10} {'TOTAL':>10}")
    print(f"  {'-'*25} {'-'*10} {'-'*8} {'-'*8} {'-'*10} {'-'*10}")
    for r in results:
        print(f"  {r['scenario']:<25} {r['download_s']:>10.1f} {r['clean_s']:>8.1f} {r['merge_s']:>8.1f} {r['publish_s']:>10.1f} {r['total_s']:>10.1f}")

    # ── README-ready table ───────────────────────────────────────
    print(f"\n\n  📋 README-ready table:")
    print(f"  {'─'*65}")
    print(f"  | Vendors | Products | File Size | Processing Time |")
    print(f"  |---------|----------|-----------|-----------------|")
    for r in results:
        print(f"  | {r['vendors']:<7} | {r['products']:>7,}  | {r['file_size_mb']:>5.1f} MB   | {format_time(r['total_s']):<15} |")
    print(f"  {'─'*65}")
    print(f"\n  **Tested on: MacBook Air, 100 Mbps connection**\n")


if __name__ == '__main__':
    main()
