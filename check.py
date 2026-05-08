#!/usr/bin/env python3
"""
Twilio Monitor — проверяет субаккаунты, бренды и A2P кампании,
записывает результат в Google Sheets.
"""

import os
import json
import requests
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timezone

MASTER_SID = os.environ["TWILIO_ACCOUNT_SID"]
MASTER_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
GCP_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]

TWILIO_BASE = "https://api.twilio.com/2010-04-01"
MSG_BASE = "https://messaging.twilio.com/v1"

RELEVANT_USE_CASES = {"MARKETING", "ACCOUNT_NOTIFICATION", "MIXED"}


# ─────────────────────────────────────────────────────────────────────────────
# Twilio Helpers
# ─────────────────────────────────────────────────────────────────────────────

def t_get(url, account_sid=None):
    auth_sid = account_sid or MASTER_SID

    r = requests.get(
        url,
        auth=(auth_sid, MASTER_TOKEN),
        timeout=30,
    )

    if r.ok:
        return r.json()

    print(f"  ⚠️ {r.status_code} {url}")
    return None


def get_subaccounts():
    data = t_get(f"{TWILIO_BASE}/Accounts.json?PageSize=1000")

    if not data:
        return []

    return [
        a for a in data.get("accounts", [])
        if a.get("sid") != MASTER_SID
    ]


def get_brands(account_sid):
    data = t_get(
        f"{MSG_BASE}/a2p/BrandRegistrations?PageSize=100",
        account_sid=account_sid,
    )

    return data.get("data", []) if data else []


def get_services(account_sid):
    data = t_get(
        f"{MSG_BASE}/Services?PageSize=100",
        account_sid=account_sid,
    )

    return data.get("services", []) if data else []


def get_campaigns(account_sid, service_sid):
    data = t_get(
        f"{MSG_BASE}/Services/{service_sid}/Compliance/Usa2p?PageSize=100",
        account_sid=account_sid,
    )

    if not data:
        return []

    return data.get("us_app_to_person", [])


def get_value(obj, *keys, default="—"):
    for key in keys:
        value = obj.get(key)

        if value not in [None, ""]:
            return value

    return default


# ─────────────────────────────────────────────────────────────────────────────
# Google Sheets
# ─────────────────────────────────────────────────────────────────────────────

def get_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    creds_info = json.loads(GCP_JSON)

    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=scopes,
    )

    gc = gspread.authorize(creds)

    return gc.open_by_key(SHEET_ID)


def ensure_worksheet(spreadsheet, title, rows=1000, cols=20):
    try:
        ws = spreadsheet.worksheet(title)
        return ws

    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(
            title=title,
            rows=rows,
            cols=cols,
        )


def replace_worksheet_data(ws, rows):
    ws.clear()

    ws.resize(
        rows=max(len(rows), 2),
        cols=max(len(rows[0]), 1),
    )

    ws.update(
        rows,
        value_input_option="USER_ENTERED",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print(f"🚀 Twilio Monitor — {run_at}")

    print("📋 Fetching subaccounts...")
    subaccounts = get_subaccounts()
    print(f"   Found {len(subaccounts)} subaccounts")

    brand_rows = [[
        "Run Date",
        "Subaccount Name",
        "Subaccount SID",
        "Subaccount Status",
        "Brand Name",
        "Brand SID",
        "Brand Status",
        "Failure Reason",
    ]]

    campaign_rows = [[
        "Run Date",
        "Subaccount Name",
        "Subaccount SID",
        "Subaccount Status",
        "Service Name",
        "Service SID",
        "Campaign ID",
        "Use Case",
        "Campaign Status",
        "Failure Reason",
    ]]

    total_brands = 0
    total_campaign_rows = 0

    for sub in subaccounts:
        sub_sid = sub["sid"]
        sub_name = sub.get("friendly_name", "—")
        sub_status = sub.get("status", "—")

        print(f"🔎 Checking {sub_name} / {sub_sid}")

        # ── Brands ──────────────────────────────────────────────────────────

        brands = get_brands(sub_sid)

        total_brands += len(brands)

        if not brands:
            brand_rows.append([
                run_at,
                sub_name,
                sub_sid,
                sub_status,
                "—",
                "—",
                "—",
                "—",
            ])

        else:
            for b in brands:
                brand_rows.append([
                    run_at,
                    sub_name,
                    sub_sid,
                    sub_status,
                    get_value(
                        b,
                        "company_name",
                        "companyName",
                        "brand_name",
                        "brandName",
                    ),
                    get_value(b, "sid"),
                    get_value(b, "status"),
                    get_value(
                        b,
                        "failure_reason",
                        "failureReason",
                    ),
                ])

        # ── Messaging Services ──────────────────────────────────────────────

        services = get_services(sub_sid)

        if not services:
            campaign_rows.append([
                run_at,
                sub_name,
                sub_sid,
                sub_status,
                "—",
                "—",
                "—",
                "—",
                "—",
                "—",
            ])

            total_campaign_rows += 1
            continue

        # ── Campaigns ───────────────────────────────────────────────────────

        for svc in services:
            service_sid = svc["sid"]

            service_name = svc.get(
                "friendly_name",
                "—",
            )

            campaigns = get_campaigns(
                sub_sid,
                service_sid,
            )

            relevant_campaigns = []

            for c in campaigns:
                use_case = get_value(
                    c,
                    "us_app_to_person_usecase",
                    "usAppToPersonUsecase",
                    "use_case",
                    "useCase",
                    default="",
                )

                if use_case.upper() in RELEVANT_USE_CASES:
                    relevant_campaigns.append(c)

            if not relevant_campaigns:
                campaign_rows.append([
                    run_at,
                    sub_name,
                    sub_sid,
                    sub_status,
                    service_name,
                    service_sid,
                    "—",
                    "—",
                    "—",
                    "—",
                ])

                total_campaign_rows += 1

            else:
                for c in relevant_campaigns:
                    campaign_rows.append([
                        run_at,
                        sub_name,
                        sub_sid,
                        sub_status,
                        service_name,
                        service_sid,
                        get_value(
                            c,
                            "campaign_id",
                            "campaignId",
                            "sid",
                        ),
                        get_value(
                            c,
                            "us_app_to_person_usecase",
                            "usAppToPersonUsecase",
                            "use_case",
                            "useCase",
                        ),
                        get_value(
                            c,
                            "campaign_status",
                            "campaignStatus",
                            "status",
                        ),
                        get_value(
                            c,
                            "failure_reason",
                            "failureReason",
                        ),
                    ])

                    total_campaign_rows += 1

    # ────────────────────────────────────────────────────────────────────────
    # Google Sheets Write
    # ────────────────────────────────────────────────────────────────────────

    print("📊 Writing to Google Sheets...")

    spreadsheet = get_sheet()

    # ── Brands Sheet ────────────────────────────────────────────────────────

    ws_brands = ensure_worksheet(
        spreadsheet,
        "Brands",
    )

    replace_worksheet_data(
        ws_brands,
        brand_rows,
    )

    print(
        f"   ✅ Brands sheet updated: {len(brand_rows) - 1} rows"
    )

    # ── Campaigns Sheet ─────────────────────────────────────────────────────

    ws_campaigns = ensure_worksheet(
        spreadsheet,
        "Campaigns",
    )

    replace_worksheet_data(
        ws_campaigns,
        campaign_rows,
    )

    print(
        f"   ✅ Campaigns sheet updated: {len(campaign_rows) - 1} rows"
    )

    # ── Run Log ─────────────────────────────────────────────────────────────

    try:
        ws_log = spreadsheet.worksheet("Run Log")

    except gspread.WorksheetNotFound:
        ws_log = spreadsheet.add_worksheet(
            title="Run Log",
            rows=500,
            cols=5,
        )

        ws_log.append_row([
            "Date",
            "Subaccounts",
            "Brands",
            "Campaign Rows",
            "Note",
        ])

    ws_log.append_row([
        run_at,
        len(subaccounts),
        total_brands,
        total_campaign_rows,
        os.environ.get(
            "INPUT_NOTE",
            "Scheduled run",
        ),
    ])

    print("   ✅ Run Log updated")

    print("🎉 Done!")


if __name__ == "__main__":
    main()
