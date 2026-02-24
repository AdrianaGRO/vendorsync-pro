#!/usr/bin/env python3
"""Debug Google Sheets 404 error"""

import json
import sys
from google.oauth2.service_account import Credentials
import gspread

print("\n" + "="*60)
print("  Google Sheets 404 Error Debugger")
print("="*60 + "\n")

# Step 1: Read config
print("1️⃣  Reading config.json...")
try:
    with open('config.json', 'r') as f:
        config = json.load(f)
    sheet_id = config['output_sheet_id']
    print(f"   ✅ Sheet ID found: {sheet_id}")
    print(f"   📏 Length: {len(sheet_id)} characters")

    # Check for whitespace
    if sheet_id != sheet_id.strip():
        print(f"   ⚠️  WARNING: Sheet ID has extra whitespace!")
        print(f"   Original: '{sheet_id}'")
        print(f"   Trimmed:  '{sheet_id.strip()}'")
        sheet_id = sheet_id.strip()
except Exception as e:
    print(f"   ❌ Error reading config: {e}")
    sys.exit(1)

# Step 2: Load credentials
print("\n2️⃣  Loading Google credentials...")
try:
    credentials = Credentials.from_service_account_file(
        'google_credentials.json',
        scopes=['https://www.googleapis.com/auth/spreadsheets']
    )

    with open('google_credentials.json', 'r') as f:
        creds_data = json.load(f)
    service_account_email = creds_data['client_email']

    print(f"   ✅ Credentials loaded")
    print(f"   📧 Service account: {service_account_email}")
except Exception as e:
    print(f"   ❌ Error loading credentials: {e}")
    sys.exit(1)

# Step 3: Try to access the sheet
print("\n3️⃣  Attempting to access Google Sheet...")
print(f"   🔗 Sheet URL: https://docs.google.com/spreadsheets/d/{sheet_id}/edit")
print()

try:
    client = gspread.authorize(credentials)
    sheet = client.open_by_key(sheet_id)

    print(f"   ✅ SUCCESS! Sheet found!")
    print(f"   📄 Title: {sheet.title}")
    print(f"   🆔 ID: {sheet.id}")
    print()

except gspread.exceptions.APIError as e:
    if e.response.status_code == 404:
        print(f"   ❌ 404 ERROR - Sheet Not Found!\n")
        print("   🔍 This means one of the following:")
        print()
        print("   A) The Sheet ID in config.json is WRONG")
        print("      → Double-check you copied the correct ID from the URL")
        print()
        print("   B) The sheet hasn't been shared with the service account")
        print(f"      → Open the sheet and verify this email is in the 'Share' list:")
        print(f"        {service_account_email}")
        print()
        print("   C) You shared a DIFFERENT sheet (not the one in config)")
        print("      → Make sure the IDs match!")
        print()

        print("="*60)
        print("🔧 HOW TO FIX:")
        print("="*60)
        print()
        print("1. Open this URL in your browser:")
        print(f"   https://docs.google.com/spreadsheets/d/{sheet_id}/edit")
        print()
        print("2. What happens?")
        print("   a) Sheet opens? → Click Share and add the service account")
        print("   b) 404 error?   → The Sheet ID is WRONG - create a new sheet")
        print()

    else:
        print(f"   ❌ API Error {e.response.status_code}: {e}")

except Exception as e:
    print(f"   ❌ Unexpected error: {e}")

print("\n" + "="*60)
print("📝 NEXT STEPS:")
print("="*60)
print()
print("Option 1: Fix the Sheet ID")
print("  1. Create a NEW Google Sheet: https://sheets.google.com")
print("  2. Copy its ID from the URL")
print("  3. Update config.json with the new ID")
print()
print("Option 2: Share Existing Sheet")
print("  1. Open the sheet URL shown above")
print("  2. Click 'Share' button")
print(f"  3. Add: {service_account_email}")
print("  4. Permission: Editor")
print("  5. Uncheck 'Notify people'")
print("  6. Click 'Send'")
print("  7. Wait 30 seconds and try again")
print()
