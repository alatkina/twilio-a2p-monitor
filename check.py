#!/usr/bin/env python3
"""
Twilio Monitor — проверяет субаккаунты, бренды и кампании,
записывает результат в Google Sheets.
"""

import os
import json
import base64
import requests
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────
MASTER_SID   = os.environ["TWILIO_ACCOUNT_SID"]
MASTER_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
SHEET_ID     = os.environ["GOOGLE_SHEET_ID"]
GCP_JSON     = os.environ["GOOGLE_CREDENTIALS_JSON"]

TWILIO_BASE = "https://api.twilio.com/2010-04-01"
MSG_BASE    = "https://messaging.twilio.com/v1"

RELEVANT_USE_CASES = {"MARKETING", "ACCOUNT_NOTIFICATION", "MIXED"}

# ── Twilio helpers ────────────────────────────────────────────────────────────
def t_get(url):
    """Authenticated GET to Twilio, returns parsed JSON or None on error."""
    r = requests.get(url, auth=(MASTER_SID, MASTER_TOKEN), timeout=30)
    if r.ok:
        return r.json()
    print(f"  ⚠️  {r.status_code} {url}")
    return None

def get_subaccounts():
    data = t_get(f"{TWILIO_BASE}/Accounts.json?PageSize=100")
    if not data:
        return []
    return [a for a in data.get("accounts", []) if a["sid"] != MASTER_SID]

def get_brands():
    data = t_get(f"{MSG_BASE}/a2p/BrandRegistrations?PageSize=50")
    return data.get("data", []) if data else []

def get_services():
    data = t_get(f"{MSG_BASE}/Services?PageSize=50")
    return data.get("services", []) if data else []

def get_campaigns(service_sid):
    data = t_get(f"{MSG_BASE}/Services/{service_sid}/UsAppToPerson")
    if not data:
        return []
    return data.get("compliance", data.get("us_app_to_person", []))

# ── Google Sheets ─────────────────────────────────────────────────────────────
def get_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_info = json.loads(GCP_JSON)
    creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID)

def ensure_worksheet(spreadsheet, title):
    try:
        ws = spreadsheet.worksheet(title)
        ws.clear()
        return ws
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=title, rows=1000, cols=20)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"🚀 Twilio Monitor — {run_at}")

    print("📋 Fetching subaccounts…")
    subaccounts = get_subaccounts()
    print(f"   Found {len(subaccounts)} subaccounts")

    print("🏢 Fetching brands…")
    brands = get_brands()
    brands_by_sid = {b["sid"]: b for b in brands}
    print(f"   Found {len(brands)} brands")

    print("💬 Fetching messaging services…")
    services = get_services()
    print(f"   Found {len(services)} services")

    # Attach campaigns to services
    services_with_campaigns = []
    for svc in services:
        campaigns = get_campaigns(svc["sid"])
        relevant = [
            c for c in campaigns
            if (c.get("us_app_to_person_usecase") or c.get("use_case", "")).upper() in RELEVANT_USE_CASES
        ]
        services_with_campaigns.append({**svc, "campaigns": relevant})

    # ── Build rows ────────────────────────────────────────────────────────────
    # Sheet 1: Brands per subaccount
    brand_rows = [[
        "Run Date", "Subaccount Name", "Subaccount SID", "Subaccount Status",
        "Brand Name", "Brand SID", "Brand Status", "Failure Reason"
    ]]
    for sub in subaccounts:
        sub_brands = [b for b in brands if b.get("account_sid") == sub["sid"]]
        if not sub_brands:
            brand_rows.append([
                run_at, sub["friendly_name"], sub["sid"], sub["status"],
                "—", "—", "—", "—"
            ])
        else:
            for b in sub_brands:
                brand_rows.append([
                    run_at,
                    sub["friendly_name"], sub["sid"], sub["status"],
                    b.get("company_name", "—"), b["sid"],
                    b.get("status", "—"), b.get("failure_reason", "—")
                ])

    # Sheet 2: Campaigns per subaccount
    campaign_rows = [[
        "Run Date", "Subaccount Name", "Subaccount SID",
        "Service Name", "Service SID",
        "Campaign ID", "Use Case", "Campaign Status"
    ]]
    for sub in subaccounts:
        sub_services = [s for s in services_with_campaigns if s.get("account_sid") == sub["sid"]]
        if not sub_services:
            campaign_rows.append([
                run_at, sub["friendly_name"], sub["sid"],
                "—", "—", "—", "—", "—"
            ])
        else:
            for svc in sub_services:
                if not svc["campaigns"]:
                    campaign_rows.append([
                        run_at, sub["friendly_name"], sub["sid"],
                        svc.get("friendly_name", "—"), svc["sid"],
                        "—", "—", "—"
                    ])
                else:
                    for c in svc["campaigns"]:
                        campaign_rows.append([
                            run_at, sub["friendly_name"], sub["sid"],
                            svc.get("friendly_name", "—"), svc["sid"],
                            c.get("campaign_id") or c.get("sid", "—"),
                            c.get("us_app_to_person_usecase") or c.get("use_case", "—"),
                            c.get("campaign_status") or c.get("status", "—")
                        ])

    # ── Write to Google Sheets ────────────────────────────────────────────────
    print("📊 Writing to Google Sheets…")
    spreadsheet = get_sheet()

    ws_brands = ensure_worksheet(spreadsheet, "Brands")
    ws_brands.update(brand_rows, value_input_option="USER_ENTERED")
    print(f"   ✅ Brands sheet: {len(brand_rows)-1} rows")

    ws_campaigns = ensure_worksheet(spreadsheet, "Campaigns")
    ws_campaigns.update(campaign_rows, value_input_option="USER_ENTERED")
    print(f"   ✅ Campaigns sheet: {len(campaign_rows)-1} rows")

    # Sheet 3: Run log — append, never overwrite
    try:
        ws_log = spreadsheet.worksheet("Run Log")
    except gspread.WorksheetNotFound:
        ws_log = spreadsheet.add_worksheet(title="Run Log", rows=500, cols=5)
        ws_log.append_row(["Date", "Subaccounts", "Brands", "Campaign Rows", "Note"])

    ws_log.append_row([
        run_at,
        len(subaccounts),
        len(brands),
        len(campaign_rows) - 1,
        os.environ.get("INPUT_NOTE", "Scheduled run")
    ])
    print("   ✅ Run Log updated")
    print("🎉 Done!")

if __name__ == "__main__":
    main()
