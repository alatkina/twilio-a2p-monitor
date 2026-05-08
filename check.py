#!/usr/bin/env python3
"""
Twilio Monitor — проверяет subaccounts, Messaging Services и A2P 10DLC campaigns.
Пишет результат в Google Sheets.
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

RELEVANT_USE_CASES = {
    "MARKETING",
    "ACCOUNT_NOTIFICATION",
    "MIXED",
}


def t_get(url, auth_sid):
    r = requests.get(
        url,
        auth=(auth_sid, MASTER_TOKEN),
        timeout=30,
    )

    if r.ok:
        return r.json()

    print(f"⚠️ {r.status_code}: {url}")
    print(r.text[:500])
    return None


def get_subaccounts():
    data = t_get(
        f"{TWILIO_BASE}/Accounts.json?PageSize=1000",
        MASTER_SID,
    )

    if not data:
        return []

    return [
        account
        for account in data.get("accounts", [])
        if account.get("sid") != MASTER_SID
    ]


def get_services_for_subaccount(subaccount_sid):
    data = t_get(
        f"{MSG_BASE}/Services?PageSize=1000",
        subaccount_sid,
    )

    if not data:
        return []

    return data.get("services", [])


def get_campaigns_for_service(subaccount_sid, service_sid):
    data = t_get(
        f"{MSG_BASE}/Services/{service_sid}/Compliance/Usa2p?PageSize=100",
        subaccount_sid,
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


def main():
    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print(f"🚀 Twilio Monitor — {run_at}")

    print("📋 Fetching subaccounts from master account...")
    subaccounts = get_subaccounts()
    print(f"Found subaccounts: {len(subaccounts)}")

    rows = [[
        "Run Date",
        "Subaccount Name",
        "Subaccount SID",
        "Subaccount Status",
        "Messaging Service Name",
        "Messaging Service SID",
        "Campaign SID",
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

        print(f"🔎 Checking subaccount: {sub_name} / {sub_sid}")

        services = get_services_for_subaccount(sub_sid)
        print(f"   Messaging services found: {len(services)}")

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
                "No Messaging Service found",
            ])
            continue

        total_services += len(services)

        for svc in services:
            service_sid = svc.get("sid", "—")
            service_name = svc.get("friendly_name", "—")

            campaigns = get_campaigns_for_service(
                sub_sid,
                service_sid,
            )

            print(f"   {service_name} / {service_sid}: campaigns found {len(campaigns)}")

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

            for campaign in campaigns:
                use_case = get_value(
                    campaign,
                    "us_app_to_person_usecase",
                    "usAppToPersonUsecase",
                    "use_case",
                    "useCase",
                )

                use_case_upper = str(use_case).upper()

                if use_case_upper not in RELEVANT_USE_CASES:
                    rows.append([
                        run_at,
                        sub_name,
                        sub_sid,
                        sub_status,
                        service_name,
                        service_sid,
                        get_value(
                            campaign,
                            "sid",
                            "campaign_sid",
                            "campaignSid",
                            "campaign_id",
                            "campaignId",
                        ),
                        get_value(
                            campaign,
                            "brand_registration_sid",
                            "brandRegistrationSid",
                        ),
                        use_case,
                        get_value(
                            campaign,
                            "campaign_status",
                            "campaignStatus",
                            "status",
                        ),
                        get_value(
                            campaign,
                            "failure_reason",
                            "failureReason",
                        ),
                        "Campaign found, but use case is not Marketing / Account Notification",
                    ])
                    total_campaigns += 1
                    continue

                rows.append([
                    run_at,
                    sub_name,
                    sub_sid,
                    sub_status,
                    service_name,
                    service_sid,
                    get_value(
                        campaign,
                        "sid",
                        "campaign_sid",
                        "campaignSid",
                        "campaign_id",
                        "campaignId",
                    ),
                    get_value(
                        campaign,
                        "brand_registration_sid",
                        "brandRegistrationSid",
                    ),
                    use_case,
                    get_value(
                        campaign,
                        "campaign_status",
                        "campaignStatus",
                        "status",
                    ),
                    get_value(
                        campaign,
                        "failure_reason",
                        "failureReason",
                    ),
                    "Relevant campaign found",
                ])

                total_campaigns += 1

    print("📊 Writing to Google Sheets...")

    spreadsheet = get_sheet()

    ws = ensure_worksheet(
        spreadsheet,
        "Subaccount Campaigns",
    )

    replace_sheet(ws, rows)

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
            "Note",
        ])

    ws_log.append_row([
        run_at,
        len(subaccounts),
        total_services,
        total_campaigns,
        len(rows) - 1,
        os.environ.get("INPUT_NOTE", "Scheduled run"),
    ])

    print(f"✅ Updated rows: {len(rows) - 1}")
    print("🎉 Done!")


if __name__ == "__main__":
    main()
