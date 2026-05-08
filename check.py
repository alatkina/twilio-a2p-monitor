#!/usr/bin/env python3
"""
Twilio Monitor

Получает:
- все subaccounts
- Messaging Services
- A2P Campaigns

Пишет в Google Sheets:
- Campaign ID
- Brand Registration SID
- Use Case
- Campaign Status
- Messaging Service SID
"""

import os
import json
import requests
import gspread
from datetime import datetime, timezone
from google.oauth2.service_account import Credentials

MASTER_SID = os.environ["TWILIO_ACCOUNT_SID"]
MASTER_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]

SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
GCP_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]

TWILIO_BASE = "https://api.twilio.com/2010-04-01"
MSG_BASE = "https://messaging.twilio.com/v1"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def t_get(url, sid, token):
    r = requests.get(
        url,
        auth=(sid, token),
        timeout=30,
    )

    if r.ok:
        return r.json()

    print(f"⚠️ {r.status_code}: {url}")
    print(r.text[:300])

    return None


def get_value(obj, *keys, default="—"):
    for key in keys:
        value = obj.get(key)

        if value not in [None, ""]:
            return value

    return default


# ─────────────────────────────────────────────────────────────────────────────
# Twilio
# ─────────────────────────────────────────────────────────────────────────────

def get_subaccounts():
    data = t_get(
        f"{TWILIO_BASE}/Accounts.json?PageSize=1000",
        MASTER_SID,
        MASTER_TOKEN,
    )

    if not data:
        return []

    return [
        account
        for account in data.get("accounts", [])
        if account.get("sid") != MASTER_SID
    ]


def get_subaccount_auth_token(subaccount_sid):
    data = t_get(
        f"{TWILIO_BASE}/Accounts/{subaccount_sid}.json",
        MASTER_SID,
        MASTER_TOKEN,
    )

    if not data:
        return None

    return data.get("auth_token")


def get_services(sub_sid, sub_token):
    data = t_get(
        f"{MSG_BASE}/Services?PageSize=1000",
        sub_sid,
        sub_token,
    )

    if not data:
        return []

    return data.get("services", [])


def get_campaigns(sub_sid, sub_token, service_sid):
    data = t_get(
        f"{MSG_BASE}/Services/{service_sid}/Compliance/Usa2p?PageSize=100",
        sub_sid,
        sub_token,
    )

    if not data:
        return []

    # ВАЖНО:
    # Campaigns лежат в "compliance"
    return data.get("compliance", [])


# ─────────────────────────────────────────────────────────────────────────────
# Google Sheets
# ─────────────────────────────────────────────────────────────────────────────

def get_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    creds = Credentials.from_service_account_info(
        json.loads(GCP_JSON),
        scopes=scopes,
    )

    gc = gspread.authorize(creds)

    return gc.open_by_key(SHEET_ID)


def ensure_worksheet(spreadsheet, title):
    try:
        return spreadsheet.worksheet(title)

    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(
            title=title,
            rows=1000,
            cols=20,
        )


def replace_sheet(ws, rows):
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

    print(f"Found subaccounts: {len(subaccounts)}")

    rows = [[
        "Run Date",
        "Subaccount Name",
        "Subaccount SID",
        "Subaccount Status",
        "Messaging Service Name",
        "Messaging Service SID",
        "Campaign ID",
        "Brand Registration SID",
        "Use Case",
        "Campaign Status",
        "Failure Reason",
        "Note",
    ]]

    total_services = 0
    total_campaigns = 0

    for sub in subaccounts:
        sub_sid = sub.get("sid", "—")
        sub_name = sub.get("friendly_name", "—")
        sub_status = sub.get("status", "—")

        print(f"🔎 {sub_name} / {sub_sid}")

        # ─────────────────────────────────────────────────────────────────────
        # Subaccount Token
        # ─────────────────────────────────────────────────────────────────────

        sub_token = get_subaccount_auth_token(sub_sid)

        if not sub_token:
            rows.append([
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
                "—",
                "Could not fetch subaccount auth token",
            ])

            continue

        # ─────────────────────────────────────────────────────────────────────
        # Messaging Services
        # ─────────────────────────────────────────────────────────────────────

        services = get_services(
            sub_sid,
            sub_token,
        )

        print(f"   Messaging Services: {len(services)}")

        if not services:
            rows.append([
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
                "—",
                "No Messaging Services found",
            ])

            continue

        total_services += len(services)

        # ─────────────────────────────────────────────────────────────────────
        # Campaigns
        # ─────────────────────────────────────────────────────────────────────

        for svc in services:
            service_sid = svc.get("sid", "—")

            service_name = svc.get(
                "friendly_name",
                "—",
            )

            print(f"   Service: {service_name}")

            campaigns = get_campaigns(
                sub_sid,
                sub_token,
                service_sid,
            )

            print(f"      Campaigns: {len(campaigns)}")

            if not campaigns:
                rows.append([
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
                    "—",
                    "No A2P Campaign found",
                ])

                continue

            total_campaigns += len(campaigns)

            for campaign in campaigns:
                rows.append([
                    run_at,
                    sub_name,
                    sub_sid,
                    sub_status,
                    service_name,
                    service_sid,

                    # Campaign ID
                    get_value(
                        campaign,
                        "campaign_id",
                        "campaignId",
                        "sid",
                    ),

                    # Brand SID
                    get_value(
                        campaign,
                        "brand_registration_sid",
                        "brandRegistrationSid",
                    ),

                    # Use Case
                    get_value(
                        campaign,
                        "use_case",
                        "useCase",
                        "us_app_to_person_usecase",
                        "usAppToPersonUsecase",
                    ),

                    # Campaign Status
                    get_value(
                        campaign,
                        "campaign_status",
                        "campaignStatus",
                        "status",
                    ),

                    # Failure Reason
                    get_value(
                        campaign,
                        "failure_reason",
                        "failureReason",
                        "errors",
                    ),

                    "Campaign found",
                ])

    # ─────────────────────────────────────────────────────────────────────────
    # Google Sheets
    # ─────────────────────────────────────────────────────────────────────────

    print("📊 Writing to Google Sheets...")

    spreadsheet = get_sheet()

    ws = ensure_worksheet(
        spreadsheet,
        "Subaccount Campaigns",
    )

    replace_sheet(ws, rows)

    # ─────────────────────────────────────────────────────────────────────────
    # Run Log
    # ─────────────────────────────────────────────────────────────────────────

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
            "Services",
            "Campaigns",
            "Rows",
        ])

    ws_log.append_row([
        run_at,
        len(subaccounts),
        total_services,
        total_campaigns,
        len(rows) - 1,
    ])

    print(f"✅ Rows written: {len(rows) - 1}")
    print("🎉 Done!")


if __name__ == "__main__":
    main()
