#!/usr/bin/env python3
import os, json, requests, gspread
from datetime import datetime, timezone
from google.oauth2.service_account import Credentials

MASTER_SID = os.environ["TWILIO_ACCOUNT_SID"]
MASTER_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
GCP_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]

TWILIO_BASE = "https://api.twilio.com/2010-04-01"
MSG_BASE = "https://messaging.twilio.com/v1"

def req(url, sid, token):
    r = requests.get(url, auth=(sid, token), timeout=30)
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:500]}
    return r.status_code, body

def val(obj, *keys, default="—"):
    for k in keys:
        if isinstance(obj, dict) and obj.get(k) not in [None, ""]:
            return obj.get(k)
    return default

def sheet():
    creds = Credentials.from_service_account_info(
        json.loads(GCP_JSON),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    return gspread.authorize(creds).open_by_key(SHEET_ID)

def replace(ws, rows):
    ws.clear()
    ws.resize(rows=max(len(rows), 2), cols=max(len(rows[0]), 1))
    ws.update(rows, value_input_option="USER_ENTERED")

def ws(spreadsheet, name):
    try:
        return spreadsheet.worksheet(name)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=name, rows=1000, cols=30)

def main():
    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    status, data = req(f"{TWILIO_BASE}/Accounts.json?PageSize=1000", MASTER_SID, MASTER_TOKEN)
    accounts = [a for a in data.get("accounts", []) if a.get("sid") != MASTER_SID]

    rows = [[
        "Run Date", "Subaccount Name", "Subaccount SID", "Subaccount Status",
        "Token Fetched", "Services HTTP", "Services Count",
        "Service Name", "Service SID",
        "Compliance/Usa2p HTTP", "Compliance/Usa2p Count",
        "UsAppToPerson HTTP", "UsAppToPerson Count",
        "Campaign SID", "Campaign ID", "Brand Registration SID",
        "Use Case", "Campaign Status", "Failure / Errors", "Diagnosis"
    ]]

    for sub in accounts:
        sub_sid = sub.get("sid")
        sub_name = sub.get("friendly_name", "—")
        sub_status = sub.get("status", "—")

        token_status, token_data = req(f"{TWILIO_BASE}/Accounts/{sub_sid}.json", MASTER_SID, MASTER_TOKEN)
        sub_token = token_data.get("auth_token")

        if not sub_token:
            rows.append([
                run_at, sub_name, sub_sid, sub_status,
                "NO", "—", 0, "—", "—", "—", 0, "—", 0,
                "—", "—", "—", "—", "—", str(token_data)[:300],
                f"No auth_token. Account fetch HTTP {token_status}"
            ])
            continue

        services_status, services_data = req(f"{MSG_BASE}/Services?PageSize=1000", sub_sid, sub_token)
        services = services_data.get("services", []) if isinstance(services_data, dict) else []

        if not services:
            rows.append([
                run_at, sub_name, sub_sid, sub_status,
                "YES", services_status, 0, "—", "—", "—", 0, "—", 0,
                "—", "—", "—", "—", "—", str(services_data)[:300],
                "Subaccount token works/not works for Messaging Services; see HTTP/body"
            ])
            continue

        for svc in services:
            mg = svc.get("sid")
            svc_name = svc.get("friendly_name", "—")

            c1_status, c1_data = req(f"{MSG_BASE}/Services/{mg}/Compliance/Usa2p?PageSize=100", sub_sid, sub_token)
            c1 = c1_data.get("us_app_to_person", []) if isinstance(c1_data, dict) else []

            c2_status, c2_data = req(f"{MSG_BASE}/Services/{mg}/UsAppToPerson", sub_sid, sub_token)
            if isinstance(c2_data, dict) and any(k in c2_data for k in ["sid", "campaign_id", "campaignId", "campaignStatus"]):
                c2 = [c2_data]
            elif isinstance(c2_data, dict):
                c2 = c2_data.get("us_app_to_person", [])
            else:
                c2 = []

            campaigns = c1 or c2

            if not campaigns:
                rows.append([
                    run_at, sub_name, sub_sid, sub_status,
                    "YES", services_status, len(services), svc_name, mg,
                    c1_status, len(c1), c2_status, len(c2),
                    "—", "—", "—", "—", "—",
                    f"C1={str(c1_data)[:150]} | C2={str(c2_data)[:150]}",
                    "Service found, but no campaign from either endpoint"
                ])
                continue

            for c in campaigns:
                rows.append([
                    run_at, sub_name, sub_sid, sub_status,
                    "YES", services_status, len(services), svc_name, mg,
                    c1_status, len(c1), c2_status, len(c2),
                    val(c, "sid"),
                    val(c, "campaign_id", "campaignId"),
                    val(c, "brand_registration_sid", "brandRegistrationSid"),
                    val(c, "us_app_to_person_usecase", "usAppToPersonUsecase", "use_case", "useCase"),
                    val(c, "campaign_status", "campaignStatus", "status"),
                    json.dumps(val(c, "errors", "failure_reason", "failureReason", default="—"))[:300],
                    "Campaign found"
                ])

    ss = sheet()
    replace(ws(ss, "Twilio Diagnostic"), rows)

    print(f"Done. Subaccounts checked: {len(accounts)}. Rows: {len(rows)-1}")

if __name__ == "__main__":
    main()
