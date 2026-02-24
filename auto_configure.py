#!/usr/bin/env python3
"""
VendorSync Pro - Automatic Configuration Generator
====================================================

This script automatically:
1. Scans your Google Drive folder for vendor files
2. Analyzes the column names in each file
3. Maps columns to UPC/EAN/QTY/PRICE
4. Generates a complete config.json file

Usage:
    python3 auto_configure.py --folder-id YOUR_FOLDER_ID --sheet-id YOUR_SHEET_ID
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional
import io

try:
    import pandas as pd
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
except ImportError:
    print("❌ Missing required packages. Please run: pip install -r requirements.txt")
    sys.exit(1)


class VendorConfigGenerator:
    """Automatically generates vendor configuration by analyzing files."""

    # Common column name patterns for auto-detection
    UPC_PATTERNS = ['upc', 'upc_code', 'upccode', 'product_upc', 'universal_product_code']
    EAN_PATTERNS = ['ean', 'ean13', 'ean_code', 'eancode', 'product_ean', 'gtin']
    QTY_PATTERNS = ['qty', 'quantity', 'stock', 'stock_qty', 'available', 'inventory', 'on_hand', 'available_qty']
    PRICE_PATTERNS = ['price', 'unit_price', 'cost', 'wholesale_price', 'retail_price', 'msrp', 'amount']

    def __init__(self, credentials_path: str = 'google_credentials.json'):
        """Initialize with Google credentials."""
        print("🔐 Loading credentials...")
        self.credentials = Credentials.from_service_account_file(
            credentials_path,
            scopes=[
                'https://www.googleapis.com/auth/drive.readonly'
            ]
        )
        self.drive_service = build('drive', 'v3', credentials=self.credentials)
        print("✅ Credentials loaded\n")

    def scan_drive_folder(self, folder_id: str) -> List[Dict]:
        """Scan Google Drive folder for vendor files."""
        print(f"📁 Scanning Drive folder: {folder_id}")

        query = f"'{folder_id}' in parents and (mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' or mimeType='text/csv' or mimeType='application/vnd.ms-excel')"

        try:
            results = self.drive_service.files().list(
                q=query,
                fields="files(id, name, mimeType)"
            ).execute()

            files = results.get('files', [])
            print(f"✅ Found {len(files)} vendor files:\n")

            for i, file in enumerate(files, 1):
                file_type = "CSV" if 'csv' in file['mimeType'] else "Excel"
                print(f"   {i}. {file['name']} ({file_type})")

            print()
            return files

        except Exception as e:
            print(f"❌ Error accessing Drive folder: {e}")
            print("\n💡 Make sure you've shared the folder with your service account!")
            print(f"   Service account email should be in google_credentials.json")
            sys.exit(1)

    def download_file(self, file_id: str, mime_type: str) -> pd.DataFrame:
        """Download and parse a file into a DataFrame."""
        try:
            request = self.drive_service.files().get_media(fileId=file_id)
            file_content = io.BytesIO()
            downloader = MediaIoBaseDownload(file_content, request)

            done = False
            while not done:
                status, done = downloader.next_chunk()

            file_content.seek(0)

            # Parse based on file type
            if 'csv' in mime_type:
                df = pd.read_csv(file_content, dtype=str, nrows=5)  # Only read first 5 rows
            else:
                df = pd.read_excel(file_content, dtype=str, nrows=5)

            return df

        except Exception as e:
            print(f"   ⚠️  Could not read file: {e}")
            return None

    def find_best_match(self, columns: List[str], patterns: List[str]) -> Optional[str]:
        """Find the best matching column name for a given pattern list."""
        columns_lower = [col.lower().strip() for col in columns]

        for pattern in patterns:
            for i, col_lower in enumerate(columns_lower):
                if pattern in col_lower or col_lower in pattern:
                    return columns[i]  # Return original case

        return None

    def analyze_file(self, file_info: Dict) -> Optional[Dict]:
        """Analyze a single file and generate vendor config."""
        print(f"📊 Analyzing: {file_info['name']}")

        # Extract vendor name from filename (remove extension and common suffixes)
        vendor_name = file_info['name'].rsplit('.', 1)[0]
        for suffix in ['_prices', '_inventory', '_stock', '_daily', '_weekly', 'prices', 'inventory']:
            if suffix.lower() in vendor_name.lower():
                vendor_name = vendor_name.replace(suffix, '').replace('_', '').strip()
                break

        print(f"   Vendor name: {vendor_name}")

        # Download and parse file
        df = self.download_file(file_info['id'], file_info['mimeType'])

        if df is None:
            return None

        columns = df.columns.tolist()
        print(f"   Columns found: {', '.join(columns)}")

        # Auto-detect column mappings
        upc_col = self.find_best_match(columns, self.UPC_PATTERNS)
        ean_col = self.find_best_match(columns, self.EAN_PATTERNS)
        qty_col = self.find_best_match(columns, self.QTY_PATTERNS)
        price_col = self.find_best_match(columns, self.PRICE_PATTERNS)

        # Validation
        if not upc_col and not ean_col:
            print(f"   ⚠️  WARNING: No UPC or EAN column detected!")
            print(f"   📋 Available columns: {', '.join(columns)}")
            upc_col = input(f"   Enter UPC column name (or press Enter to skip): ").strip()
            ean_col = input(f"   Enter EAN column name (or press Enter to skip): ").strip()
            if not upc_col and not ean_col:
                print("   ❌ Skipping this file - no barcode column\n")
                return None

        if not price_col:
            print(f"   ⚠️  WARNING: No PRICE column detected!")
            print(f"   📋 Available columns: {', '.join(columns)}")
            price_col = input(f"   Enter PRICE column name: ").strip()
            if not price_col:
                print("   ❌ Skipping this file - no price column\n")
                return None

        # Show detected mappings
        print(f"   ✅ Mapped columns:")
        print(f"      UPC: {upc_col or '(none)'}")
        print(f"      EAN: {ean_col or '(none)'}")
        print(f"      QTY: {qty_col or '(none)'}")
        print(f"      PRICE: {price_col}")
        print()

        return {
            "name": vendor_name,
            "upc_column": upc_col or "",
            "ean_column": ean_col or "",
            "qty_column": qty_col or "",
            "price_column": price_col
        }

    def generate_config(self, folder_id: str, sheet_id: str) -> Dict:
        """Generate complete config.json."""
        # Scan folder
        files = self.scan_drive_folder(folder_id)

        if not files:
            print("❌ No files found in the folder!")
            sys.exit(1)

        print("="*60)
        print("Starting file analysis...\n")

        # Analyze each file
        vendors = []
        for file_info in files:
            vendor_config = self.analyze_file(file_info)
            if vendor_config:
                vendors.append(vendor_config)

        if not vendors:
            print("❌ Could not generate configuration for any files!")
            sys.exit(1)

        # Build final config
        config = {
            "drive_folder_id": folder_id,
            "output_sheet_id": sheet_id,
            "vendors": vendors
        }

        return config

    def save_config(self, config: Dict, output_path: str = 'config.json'):
        """Save configuration to file."""
        # Backup existing config if it exists
        if Path(output_path).exists():
            backup_path = f"{output_path}.backup"
            print(f"💾 Backing up existing config to: {backup_path}")
            import shutil
            shutil.copy(output_path, backup_path)

        # Save new config
        with open(output_path, 'w') as f:
            json.dump(config, f, indent=2)

        print(f"✅ Configuration saved to: {output_path}\n")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Auto-generate VendorSync Pro configuration by analyzing vendor files'
    )
    parser.add_argument(
        '--folder-id',
        required=True,
        help='Google Drive folder ID containing vendor files'
    )
    parser.add_argument(
        '--sheet-id',
        required=True,
        help='Google Sheets ID for output dashboard'
    )
    parser.add_argument(
        '--credentials',
        default='google_credentials.json',
        help='Path to Google credentials JSON file (default: google_credentials.json)'
    )
    parser.add_argument(
        '--output',
        default='config.json',
        help='Output config file path (default: config.json)'
    )

    args = parser.parse_args()

    print("""
    ╔══════════════════════════════════════════════════════════╗
    ║    VendorSync Pro - Auto Configuration Generator         ║
    ╚══════════════════════════════════════════════════════════╝
    """)

    # Check credentials exist
    if not Path(args.credentials).exists():
        print(f"❌ Credentials file not found: {args.credentials}")
        print("   Download it from Google Cloud Console")
        sys.exit(1)

    try:
        # Initialize generator
        generator = VendorConfigGenerator(args.credentials)

        # Generate config
        config = generator.generate_config(args.folder_id, args.sheet_id)

        # Save config
        generator.save_config(config, args.output)

        # Display summary
        print("="*60)
        print("🎉 SUCCESS! Configuration generated successfully!")
        print("="*60)
        print(f"\n📋 Configuration Summary:")
        print(f"   Drive Folder ID: {config['drive_folder_id']}")
        print(f"   Output Sheet ID: {config['output_sheet_id']}")
        print(f"   Vendors configured: {len(config['vendors'])}")
        print()

        for i, vendor in enumerate(config['vendors'], 1):
            print(f"   {i}. {vendor['name']}")
            print(f"      - UPC: {vendor['upc_column'] or '(none)'}")
            print(f"      - EAN: {vendor['ean_column'] or '(none)'}")
            print(f"      - QTY: {vendor['qty_column'] or '(none)'}")
            print(f"      - PRICE: {vendor['price_column']}")
            print()

        print("✅ Next steps:")
        print("   1. Review the generated config.json")
        print("   2. Make any manual adjustments if needed")
        print("   3. Run: python3 validate_setup.py")
        print("   4. Run: python3 vendorsync_pro.py\n")

    except KeyboardInterrupt:
        print("\n\n⚠️  Configuration cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
