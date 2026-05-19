#!/usr/bin/env python3

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

ACCOUNT_FILTER = os.environ.get(
    "INPUT_ACCOUNT",
    "",
).lower().strip()

TWILIO_BASE = "https://api.twilio.com/2010-04-01"
MSG_BASE = "https://messaging.twilio.com/v1"


def clean_cell(value):
    if value in [None, ""]:
        return "—"

    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)

    return str(value)


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
            return clean_cell(value)

    return default


def get_subaccounts():
    data = t_get(
        f"{TWILIO_BASE}/Accounts.json?PageSize=1000",
        MASTER_SID,
        MASTER_TOKEN,
    )

    if not data:
        return []

    accounts = [
        account
        for account in data.get("accounts", [])
        if account.get("sid") != MASTER_SID
    ]

    if ACCOUNT_FILTER:
        accounts = [
            a for a in accounts
            if ACCOUNT_FILTER in a.get(
                "friendly_name",
                "",
            ).lower()
        ]

    return accounts


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

    return data.get("compliance", [])


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
            rows=2000,
            cols=20,
        )


def replace_sheet(ws, rows):
    safe_rows = [
        [clean_cell(cell) for cell in row]
        for row in rows
    ]

    ws.clear()

    ws.resize(
        rows=max(len(safe_rows), 2),
        cols=max(len(safe_rows[0]), 1),
    )

    ws.update(
        safe_rows,
        value_input_option="USER_ENTERED",
    )


def main():
    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    rows = [[
        "Run Date",
        "Subaccount Name",
        "Messaging Service Name",
        "Brand Registration SID",
        "Use Case",
        "Campaign Status",
        "Failure Reason",
    ]]

    markdown = [
        f"# Twilio A2P Result ({run_at})",
        "",
    ]

    subaccounts = get_subaccounts()

    for sub in subaccounts:
        sub_sid = sub.get("sid", "—")
        sub_name = sub.get("friendly_name", "—")

        markdown.append(f"## {sub_name}")
        markdown.append("")

        sub_token = get_subaccount_auth_token(sub_sid)

        if not sub_token:
            markdown.append(
                "- Could not fetch subaccount auth token"
            )
            markdown.append("")
            continue

        services = get_services(sub_sid, sub_token)

        if not services:
            markdown.append(
                "- No Messaging Services found"
            )
            markdown.append("")
            continue

        rows.append(["", "", "", "", "", "", ""])

        for svc in services:
            service_sid = svc.get("sid", "—")
            service_name = svc.get("friendly_name", "—")

            campaigns = get_campaigns(
                sub_sid,
                sub_token,
                service_sid,
            )

            if not campaigns:
                rows.append([
                    run_at,
                    sub_name,
                    service_name,
                    "—",
                    "—",
                    "—",
                    "No A2P Campaign found",
                ])

                markdown.append(
                    f"- {service_name}: No A2P Campaign found"
                )

                continue

            for campaign in campaigns:
                use_case = get_value(
                    campaign,
                    "use_case",
                    "useCase",
                    "us_app_to_person_usecase",
                    "usAppToPersonUsecase",
                )

                status = get_value(
                    campaign,
                    "campaign_status",
                    "campaignStatus",
                    "status",
                )

                failure = get_value(
                    campaign,
                    "failure_reason",
                    "failureReason",
                    "errors",
                )

                brand_sid = get_value(
                    campaign,
                    "brand_registration_sid",
                    "brandRegistrationSid",
                )

                rows.append([
                    run_at,
                    sub_name,
                    service_name,
                    brand_sid,
                    use_case,
                    status,
                    failure,
                ])

                markdown.append(
                    f"- {service_name} | {use_case} | {status}"
                )

        markdown.append("")

    spreadsheet = get_sheet()

    ws = ensure_worksheet(
        spreadsheet,
        "Subaccount Campaigns",
    )

    replace_sheet(ws, rows)

    with open("result.md", "w") as f:
        f.write("\n".join(markdown))

    print("🎉 Done!")


if __name__ == "__main__":
    main()
