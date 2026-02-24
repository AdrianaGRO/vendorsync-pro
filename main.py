#!/usr/bin/env python3
"""
VendorSync - Vendor Price Comparison Automation
===============================================

Automatically downloads vendor price files from Google Drive,
normalizes barcodes (UPC/EAN), merges into a unified price matrix,
and publishes the result to Google Sheets with lowest-price highlighting.

Entry point: python main.py
Config:      config.json
Credentials: google_credentials.json

License: MIT
"""

import json
import logging
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io

# Configure logging for production monitoring
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('vendorsync.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class VendorSync:
    """
    Orchestrates the full vendor price comparison pipeline:
    download → clean → merge → publish.
    """

    def __init__(self, config_path: str, credentials_path: str):
        """
        Initialize VendorSync Pro with configuration and credentials.

        Args:
            config_path: Path to vendor configuration JSON file
            credentials_path: Path to Google service account credentials JSON
        """
        self.config = self._load_config(config_path)
        self.credentials = self._initialize_credentials(credentials_path)
        self.drive_service = build('drive', 'v3', credentials=self.credentials)
        self.sheets_client = gspread.authorize(self.credentials)

        logger.info("VendorSync initialized")

    def _load_config(self, config_path: str) -> Dict:
        """Load and validate vendor configuration from JSON file."""
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)

            # Validate required config fields
            required_fields = ['drive_folder_id', 'output_sheet_id', 'vendors']
            for field in required_fields:
                if field not in config:
                    raise ValueError(f"Missing required config field: {field}")

            logger.info(f"Configuration loaded: {len(config['vendors'])} vendors configured")
            return config

        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            raise

    def _initialize_credentials(self, credentials_path: str) -> Credentials:
        """Initialize Google API credentials with required scopes."""
        try:
            scopes = [
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive.readonly'
            ]
            credentials = Credentials.from_service_account_file(
                credentials_path,
                scopes=scopes
            )
            logger.info("Google API credentials initialized")
            return credentials

        except Exception as e:
            logger.error(f"Failed to initialize credentials: {e}")
            raise

    def download_vendor_files(self) -> Dict[str, pd.DataFrame]:
        """
        Download all vendor Excel files from Google Drive folder.

        Returns:
            Dictionary mapping vendor names to their DataFrames
        """
        vendor_dataframes = {}
        folder_id = self.config['drive_folder_id']

        logger.info(f"Scanning Google Drive folder: {folder_id}")

        # Query for Excel/CSV files in the specified folder
        query = f"'{folder_id}' in parents and (mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' or mimeType='text/csv')"

        try:
            results = self.drive_service.files().list(
                q=query,
                fields="files(id, name, mimeType)"
            ).execute()

            files = results.get('files', [])
            logger.info(f"Found {len(files)} vendor files")

            for file in files:
                vendor_name = self._extract_vendor_name(file['name'])
                if vendor_name:
                    df = self._download_and_parse_file(file)
                    if df is not None:
                        vendor_dataframes[vendor_name] = df
                        logger.info(f"Loaded {len(df)} products from {vendor_name}")

            return vendor_dataframes

        except Exception as e:
            logger.error(f"Failed to download vendor files: {e}")
            raise

    def _extract_vendor_name(self, filename: str) -> Optional[str]:
        """
        Extract vendor name from filename based on configuration.

        Args:
            filename: Name of the vendor file

        Returns:
            Vendor name if found in config, None otherwise
        """
        filename_lower = filename.lower()

        for vendor in self.config['vendors']:
            vendor_name = vendor['name']
            # Check if vendor name appears in filename (case-insensitive)
            if vendor_name.lower() in filename_lower:
                return vendor_name

        logger.warning(f"Could not match filename '{filename}' to any configured vendor")
        return None

    def _download_and_parse_file(self, file_info: Dict) -> Optional[pd.DataFrame]:
        """
        Download a single file from Drive and parse it into a DataFrame.

        Args:
            file_info: Dictionary containing file id, name, and mimeType

        Returns:
            Parsed DataFrame or None if parsing fails
        """
        try:
            # Download file content
            request = self.drive_service.files().get_media(fileId=file_info['id'])
            file_content = io.BytesIO()
            downloader = MediaIoBaseDownload(file_content, request)

            done = False
            while not done:
                status, done = downloader.next_chunk()

            file_content.seek(0)

            # Parse based on file type
            if 'csv' in file_info['mimeType']:
                # Read raw text to detect separator and skip any title rows
                raw = file_content.read().decode('utf-8', errors='replace')
                file_content.seek(0)
                # Detect separator: semicolon or comma
                first_data_line = next(
                    (line for line in raw.splitlines() if ';' in line or ',' in line), ''
                )
                sep = ';' if first_data_line.count(';') >= first_data_line.count(',') else ','
                # Count how many leading rows to skip before the real header
                lines = raw.splitlines()
                skip = next(
                    (i for i, line in enumerate(lines) if sep in line), 0
                )
                import io as _io
                df = pd.read_csv(_io.StringIO(raw), dtype=str, sep=sep, skiprows=skip)
            else:
                df = pd.read_excel(file_content, dtype=str)

            return df

        except Exception as e:
            logger.error(f"Failed to parse file {file_info['name']}: {e}")
            return None

    def clean_and_normalize_data(self, vendor_dataframes: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
        """
        Clean and normalize vendor data to prepare for merging.

        Critical Steps:
        1. Ensure barcodes are strings (prevent scientific notation)
        2. Strip whitespace and special characters
        3. Standardize column names
        4. Handle missing values

        Args:
            vendor_dataframes: Raw vendor DataFrames

        Returns:
            Cleaned and normalized DataFrames
        """
        cleaned_dataframes = {}

        for vendor_name, df in vendor_dataframes.items():
            try:
                vendor_config = self._get_vendor_config(vendor_name)

                # Create a copy to avoid modifying original
                cleaned_df = df.copy()

                # Map vendor-specific column names to standard names
                column_mapping = {
                    vendor_config['upc_column']: 'UPC',
                    vendor_config['ean_column']: 'EAN',
                    vendor_config['qty_column']: 'QTY',
                    vendor_config['price_column']: 'PRICE'
                }

                # Add product name column if specified in config
                if 'product_name_column' in vendor_config:
                    column_mapping[vendor_config['product_name_column']] = 'PRODUCT_NAME'

                # Only rename columns that exist
                existing_columns = {k: v for k, v in column_mapping.items() if k in cleaned_df.columns}
                cleaned_df = cleaned_df.rename(columns=existing_columns)

                # CRITICAL: Clean barcode data to prevent scientific notation
                # Standards: UPC-A = exactly 12 digits, EAN-13 = exactly 13 digits
                # UPC-A is a subset of EAN-13: a 12-digit UPC zero-padded to 13 digits IS a valid EAN-13
                # So we normalize both to GTIN-13 (13 digits, zero-padded) for consistent merging
                barcode_exact_digits = {'UPC': 12, 'EAN': 13}
                for barcode_col in ['UPC', 'EAN']:
                    if barcode_col in cleaned_df.columns:
                        cleaned_df[barcode_col] = cleaned_df[barcode_col].astype(str).str.strip()
                        # Extract only the leading numeric part (stops at first space or letter)
                        cleaned_df[barcode_col] = cleaned_df[barcode_col].str.extract(r'^(\d+)', expand=False)
                        # Truncate to the standard max length for this barcode type
                        exact_len = barcode_exact_digits[barcode_col]
                        cleaned_df[barcode_col] = cleaned_df[barcode_col].str[:exact_len]
                        # Zero-pad short values up to the required length (preserves leading zeros)
                        cleaned_df[barcode_col] = cleaned_df[barcode_col].str.zfill(exact_len)
                        # Replace empty strings and 'nan' with actual NaN
                        cleaned_df[barcode_col] = cleaned_df[barcode_col].replace(
                            {'': pd.NA, 'nan': pd.NA, '0' * exact_len: pd.NA}
                        )

                # Create unified BARCODE merge key normalised to GTIN-13:
                # - If EAN present → use as-is (already 13 digits)
                # - If only UPC present → zero-pad to 13 digits so it matches EAN-13 representations
                # - This ensures the same physical product merges to one row regardless of which
                #   barcode type different vendors happen to supply
                def _to_gtin13(series, digits):
                    """Pad a barcode Series to 13 digits for use as merge key."""
                    return series.str.zfill(13)

                if 'UPC' in cleaned_df.columns and 'EAN' in cleaned_df.columns:
                    upc_as_gtin13 = _to_gtin13(cleaned_df['UPC'].dropna(), 12)
                    cleaned_df['BARCODE'] = cleaned_df['EAN'].fillna(
                        cleaned_df['UPC'].map(lambda v: v.zfill(13) if pd.notna(v) else pd.NA)
                    )
                elif 'UPC' in cleaned_df.columns:
                    cleaned_df['BARCODE'] = cleaned_df['UPC'].map(
                        lambda v: v.zfill(13) if pd.notna(v) else pd.NA
                    )
                elif 'EAN' in cleaned_df.columns:
                    cleaned_df['BARCODE'] = cleaned_df['EAN']
                else:
                    logger.error(f"No barcode columns found for vendor {vendor_name}")
                    continue

                # Clean price data - convert to float
                if 'PRICE' in cleaned_df.columns:
                    # Step 1: Replace comma with dot for European formats
                    cleaned_df['PRICE'] = cleaned_df['PRICE'].str.replace(',', '.')
                    # Step 2: Remove everything except digits and decimal point
                    cleaned_df['PRICE'] = cleaned_df['PRICE'].str.replace(r'[^\d.]', '', regex=True)
                    # Step 3: Convert to numeric
                    cleaned_df['PRICE'] = pd.to_numeric(cleaned_df['PRICE'], errors='coerce')

                # Clean quantity data
                if 'QTY' in cleaned_df.columns:
                    cleaned_df['QTY'] = pd.to_numeric(cleaned_df['QTY'], errors='coerce')

                # Remove rows with no valid barcode
                cleaned_df = cleaned_df.dropna(subset=['BARCODE'])

                # Select UPC and EAN if available, to carry them into the merged output
                barcode_cols = ['BARCODE']
                if 'UPC' in cleaned_df.columns:
                    cleaned_df = cleaned_df.rename(columns={'UPC': f'{vendor_name}_UPC'})
                    barcode_cols.append(f'{vendor_name}_UPC')
                if 'EAN' in cleaned_df.columns:
                    cleaned_df = cleaned_df.rename(columns={'EAN': f'{vendor_name}_EAN'})
                    barcode_cols.append(f'{vendor_name}_EAN')

                # Select only required columns and rename with vendor prefix
                cols = barcode_cols + ['PRODUCT_NAME', 'PRICE', 'QTY']
                cols = [c for c in cols if c in cleaned_df.columns]
                cleaned_df = cleaned_df[cols].copy()
                cleaned_df = cleaned_df.rename(columns={
                    'PRODUCT_NAME': f'{vendor_name}_PRODUCT_NAME',
                    'PRICE': f'{vendor_name}_PRICE',
                    'QTY': f'{vendor_name}_QTY'
                })

                cleaned_dataframes[vendor_name] = cleaned_df
                logger.info(f"Cleaned {vendor_name}: {len(cleaned_df)} valid products")

            except Exception as e:
                logger.error(f"Failed to clean data for {vendor_name}: {e}")
                continue

        return cleaned_dataframes

    def _get_vendor_config(self, vendor_name: str) -> Dict:
        """Retrieve configuration for a specific vendor."""
        for vendor in self.config['vendors']:
            if vendor['name'] == vendor_name:
                return vendor
        raise ValueError(f"No configuration found for vendor: {vendor_name}")

    def merge_vendor_data(self, vendor_dataframes: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        """
        Perform outer join on all vendor DataFrames using BARCODE as key.

        This creates a unified price matrix where:
        - Each row represents a unique product (UPC/EAN)
        - Each vendor gets separate price and quantity columns
        - Products missing from some vendors show NaN

        Args:
            vendor_dataframes: Cleaned vendor DataFrames

        Returns:
            Merged DataFrame with all vendor prices
        """
        if not vendor_dataframes:
            raise ValueError("No vendor data to merge")

        logger.info(f"Merging data from {len(vendor_dataframes)} vendors")

        # Start with the first vendor's data
        vendor_names = list(vendor_dataframes.keys())
        merged_df = vendor_dataframes[vendor_names[0]].copy()

        # Sequentially merge remaining vendors using outer join
        for vendor_name in vendor_names[1:]:
            merged_df = merged_df.merge(
                vendor_dataframes[vendor_name],
                on='BARCODE',
                how='outer'
            )

        # Consolidate UPC: take the first non-null value across all vendor UPC columns
        upc_cols = [c for c in merged_df.columns if c.endswith('_UPC')]
        if upc_cols:
            # Build a clean string-only copy of these columns (no in-place assign to avoid dtype issues)
            upc_clean = (
                merged_df[upc_cols]
                .copy()
                .apply(lambda col: col.astype(str).str.strip())
                .replace({'nan': pd.NA, 'None': pd.NA, '': pd.NA})
            )
            merged_df['UPC'] = upc_clean.bfill(axis=1).iloc[:, 0]
            merged_df = merged_df.drop(columns=upc_cols)
        else:
            merged_df['UPC'] = merged_df['BARCODE']

        # Consolidate EAN: take the first non-null value across all vendor EAN columns
        ean_cols = [c for c in merged_df.columns if c.endswith('_EAN')]
        if ean_cols:
            # Build a clean string-only copy of these columns (no in-place assign to avoid dtype issues)
            ean_clean = (
                merged_df[ean_cols]
                .copy()
                .apply(lambda col: col.astype(str).str.strip())
                .replace({'nan': pd.NA, 'None': pd.NA, '': pd.NA})
            )
            merged_df['EAN'] = ean_clean.bfill(axis=1).iloc[:, 0]
            merged_df = merged_df.drop(columns=ean_cols)
        else:
            merged_df['EAN'] = pd.NA

        # Drop BARCODE — UPC is now the single clean identifier
        merged_df = merged_df.drop(columns=['BARCODE'])

        # Consolidate PRODUCT_NAME: take first non-null/non-empty value across all vendor name columns
        name_cols = [c for c in merged_df.columns if c.endswith('_PRODUCT_NAME')]
        if name_cols:
            merged_df['PRODUCT_NAME'] = (
                merged_df[name_cols]
                .replace('', pd.NA)
                .bfill(axis=1)
                .iloc[:, 0]
                .fillna('')
            )
            merged_df = merged_df.drop(columns=name_cols)
        else:
            merged_df['PRODUCT_NAME'] = ''

        # Add a column to show the lowest price vendor
        price_columns = [col for col in merged_df.columns if col.endswith('_PRICE')]
        merged_df['LOWEST_PRICE'] = merged_df[price_columns].min(axis=1)
        merged_df['BEST_VENDOR'] = merged_df[price_columns].idxmin(axis=1).str.replace('_PRICE', '')

        # Reorder columns: UPC, EAN, PRODUCT_NAME, LOWEST_PRICE, BEST_VENDOR, then vendor columns
        meta_cols = ['UPC', 'EAN', 'PRODUCT_NAME', 'LOWEST_PRICE', 'BEST_VENDOR']
        vendor_cols = [col for col in merged_df.columns if col not in meta_cols]
        merged_df = merged_df[meta_cols + vendor_cols]

        logger.info(f"Merge complete: {len(merged_df)} unique products across all vendors")
        return merged_df

    def publish_to_sheets(self, merged_df: pd.DataFrame) -> None:
        """
        Push merged data to Google Sheets with conditional formatting.

        Critical Features:
        - Clear existing data before upload
        - Apply conditional formatting to highlight lowest prices in GREEN
        - Freeze header row for easier navigation
        - Auto-resize columns for readability

        Args:
            merged_df: Merged vendor price data
        """
        try:
            sheet_id = self.config['output_sheet_id']
            logger.info(f"Publishing data to Google Sheet: {sheet_id}")

            # Open the target spreadsheet
            spreadsheet = self.sheets_client.open_by_key(sheet_id)
            worksheet = spreadsheet.sheet1  # Use first sheet

            # Clear existing data
            worksheet.clear()

            # Convert DataFrame to list format for gspread
            # Replace NaN with empty string for better display
            export_df = merged_df.fillna('').copy()

            # Force UPC and EAN columns to text by prefixing with apostrophe (prevents Sheets number formatting)
            for barcode_col in ['UPC', 'EAN']:
                if barcode_col in export_df.columns:
                    # Cast to string first to guarantee scalar strings (avoids concatenation from mixed dtypes)
                    export_df[barcode_col] = export_df[barcode_col].astype(str).str.strip()
                    export_df[barcode_col] = export_df[barcode_col].replace({'nan': '', 'None': '', '<NA>': ''})
                    export_df[barcode_col] = export_df[barcode_col].apply(
                        lambda v: f"'{v}" if v != '' else ''
                    )

            data_values = [export_df.columns.tolist()] + export_df.values.tolist()

            # Upload data
            worksheet.update(range_name='A1', values=data_values, value_input_option='USER_ENTERED')
            logger.info(f"Uploaded {len(merged_df)} rows to Google Sheets")

            # Apply formatting
            self._apply_conditional_formatting(worksheet, merged_df)
            self._format_worksheet(worksheet, len(merged_df))

            logger.info("Formatting applied successfully")

        except Exception as e:
            logger.error(f"Failed to publish to Google Sheets: {e}")
            raise

    def _apply_conditional_formatting(self, worksheet, merged_df: pd.DataFrame) -> None:
        """
        Apply conditional formatting to highlight lowest prices in each row.

        Strategy:
        - For each product row, find the minimum price across all vendors
        - Highlight that cell in GREEN using Google Sheets API
        """
        try:
            # First: delete ALL existing conditional format rules to prevent accumulation across runs
            existing_rules = worksheet.spreadsheet.fetch_sheet_metadata()
            sheet_meta = next(
                (s for s in existing_rules['sheets'] if s['properties']['sheetId'] == worksheet.id),
                None
            )
            delete_requests = []
            if sheet_meta:
                num_rules = len(sheet_meta.get('conditionalFormats', []))
                # Delete rules in reverse order (highest index first) to avoid index shifting
                for i in range(num_rules - 1, -1, -1):
                    delete_requests.append({
                        'deleteConditionalFormatRule': {
                            'sheetId': worksheet.id,
                            'index': i
                        }
                    })
            if delete_requests:
                worksheet.spreadsheet.batch_update({'requests': delete_requests})
                logger.info(f"Cleared {len(delete_requests)} stale conditional format rules")

            # Get all price column indices (1-indexed for Sheets API)
            price_columns = [col for col in merged_df.columns if col.endswith('_PRICE')]
            price_col_indices = [merged_df.columns.get_loc(col) + 1 for col in price_columns]

            # Get column letter for LOWEST_PRICE column
            lowest_price_col_index = merged_df.columns.get_loc('LOWEST_PRICE') + 1

            requests = []

            # Create conditional format rule for each price column
            for col_index in price_col_indices:
                # Convert column index to A1 notation range (skip header row)
                start_row = 2  # Row 1 is header
                end_row = len(merged_df) + 1

                col_letter = self._col_index_to_letter(col_index)
                lowest_col_letter = self._col_index_to_letter(lowest_price_col_index)

                # Create conditional format rule: highlight if cell equals LOWEST_PRICE
                requests.append({
                    'addConditionalFormatRule': {
                        'rule': {
                            'ranges': [{
                                'sheetId': worksheet.id,
                                'startRowIndex': start_row - 1,
                                'endRowIndex': end_row,
                                'startColumnIndex': col_index - 1,
                                'endColumnIndex': col_index
                            }],
                            'booleanRule': {
                                'condition': {
                                    'type': 'CUSTOM_FORMULA',
                                    'values': [{
                                        'userEnteredValue': f'=AND(${col_letter}{start_row}=${lowest_col_letter}{start_row},${lowest_col_letter}{start_row}<>"")'
                                    }]
                                },
                                'format': {
                                    'backgroundColor': {
                                        'red': 0.7176,
                                        'green': 0.8824,
                                        'blue': 0.8
                                    },
                                    'textFormat': {
                                        'bold': True
                                    }
                                }
                            }
                        },
                        'index': 0
                    }
                })

            # Execute batch update
            if requests:
                worksheet.spreadsheet.batch_update({'requests': requests})
                logger.info(f"Applied conditional formatting to {len(price_col_indices)} price columns")

        except Exception as e:
            logger.error(f"Failed to apply conditional formatting: {e}")
            # Don't raise - formatting failure shouldn't break the pipeline

    def _format_worksheet(self, worksheet, num_rows: int) -> None:
        """Apply general formatting improvements to the worksheet."""
        try:
            requests = []

            # Freeze header row
            requests.append({
                'updateSheetProperties': {
                    'properties': {
                        'sheetId': worksheet.id,
                        'gridProperties': {
                            'frozenRowCount': 1
                        }
                    },
                    'fields': 'gridProperties.frozenRowCount'
                }
            })

            # Bold header row
            requests.append({
                'repeatCell': {
                    'range': {
                        'sheetId': worksheet.id,
                        'startRowIndex': 0,
                        'endRowIndex': 1
                    },
                    'cell': {
                        'userEnteredFormat': {
                            'textFormat': {
                                'bold': True
                            },
                            'backgroundColor': {
                                'red': 0.9,
                                'green': 0.9,
                                'blue': 0.9
                            }
                        }
                    },
                    'fields': 'userEnteredFormat(textFormat,backgroundColor)'
                }
            })

            # Auto-resize columns
            requests.append({
                'autoResizeDimensions': {
                    'dimensions': {
                        'sheetId': worksheet.id,
                        'dimension': 'COLUMNS',
                        'startIndex': 0,
                        'endIndex': 20  # Resize first 20 columns
                    }
                }
            })

            worksheet.spreadsheet.batch_update({'requests': requests})
            logger.info("Applied worksheet formatting")

        except Exception as e:
            logger.error(f"Failed to format worksheet: {e}")

    @staticmethod
    def _col_index_to_letter(col_index: int) -> str:
        """Convert column index (1-based) to Excel-style letter (A, B, C, ... AA, AB, etc.)."""
        result = ""
        while col_index > 0:
            col_index -= 1
            result = chr(col_index % 26 + 65) + result
            col_index //= 26
        return result

    def run_pipeline(self) -> None:
        """
        Execute the complete VendorSync Pro pipeline.

        Pipeline Steps:
        1. Download vendor files from Google Drive
        2. Clean and normalize data
        3. Merge using outer join on barcodes
        4. Publish to Google Sheets with formatting
        """
        logger.info("=" * 60)
        logger.info("VendorSync Pipeline Starting")
        logger.info("=" * 60)

        try:
            # Step 1: Download
            vendor_dataframes = self.download_vendor_files()
            if not vendor_dataframes:
                raise ValueError("No vendor data downloaded")

            # Step 2: Clean
            cleaned_dataframes = self.clean_and_normalize_data(vendor_dataframes)
            if not cleaned_dataframes:
                raise ValueError("No vendor data survived cleaning")

            # Step 3: Merge
            merged_df = self.merge_vendor_data(cleaned_dataframes)

            # Step 4: Publish
            self.publish_to_sheets(merged_df)

            logger.info("=" * 60)
            logger.info("VendorSync Pipeline Complete!")
            logger.info(f"Processed {len(merged_df)} unique products")
            logger.info("=" * 60)

        except Exception as e:
            logger.error(f"Pipeline failed: {e}")
            raise


def preflight_check():
    """Ensure environment is ready before starting the pipeline."""
    checks = {
        "Config file (config.json)": Path("config.json").exists(),
        "Credentials (google_credentials.json)": Path("google_credentials.json").exists(),
        "Python version (3.8+)": sys.version_info >= (3, 8),
    }
    all_passed = True
    for check, status in checks.items():
        if status:
            logger.info(f"Preflight OK: {check}")
        else:
            logger.error(f"Preflight failed: {check}")
            all_passed = False
    if not all_passed:
        sys.exit(1)


def main():
    """Entry point. Runs preflight check then executes the pipeline."""
    preflight_check()

    start = time.time()
    try:
        vendorsync = VendorSync('config.json', 'google_credentials.json')
        vendorsync.run_pipeline()
        elapsed = time.time() - start
        print(f"\n🚀 Done in {elapsed:.1f} seconds")
    except Exception as e:
        logger.error(f"VendorSync failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
