
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
 
SHEET_TITLE = "Subaccount Campaigns"
 
HEADER = [
    "Subaccount Name",
    "Messaging Service Name",
    "Use Case",
    "Campaign Status",
    "Error Code",
    "Failure Reason",
    "Brand Registration SID",
    "Run Date",
]
 
# Column indexes (0-based), used by the formatter
COL_STATUS = HEADER.index("Campaign Status")
COL_ERROR = HEADER.index("Error Code")
COL_REASON = HEADER.index("Failure Reason")
COL_SID = HEADER.index("Brand Registration SID")
 
COLUMN_WIDTHS = [230, 270, 175, 135, 90, 440, 290, 150]
 
# Status shown when a subaccount/service has nothing to report
NO_CAMPAIGN = "NO CAMPAIGN"
NO_SERVICES = "NO SERVICES"
NO_TOKEN = "NO ACCESS"
 
# Lower rank = more urgent = higher in the sheet
STATUS_RANK = {
    "FAILED": 0,
    "IN_PROGRESS": 1,
    "PENDING": 1,
    NO_TOKEN: 2,
    NO_SERVICES: 3,
    NO_CAMPAIGN: 3,
    "VERIFIED": 4,
}
 
 
# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
 
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
 
 
def get_raw(obj, *keys):
    for key in keys:
        value = obj.get(key)
 
        if value not in [None, "", []]:
            return value
 
    return None
 
 
def get_value(obj, *keys, default="—"):
    value = get_raw(obj, *keys)
    return default if value is None else clean_cell(value)
 
 
def format_failure(value):
    """Turn Twilio's error payload into (error_code_cell, readable_text)."""
    if value in [None, "", [], {}]:
        return "", ""
 
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return "", value
 
    if isinstance(value, dict):
        value = [value]
 
    if not isinstance(value, list):
        return "", str(value)
 
    codes, lines = [], []
 
    for err in value:
        if not isinstance(err, dict):
            lines.append(str(err))
            continue
 
        code = err.get("error_code")
        desc = err.get("description", "").strip()
        fields = err.get("fields") or []
 
        if code:
            codes.append((str(code), err.get("url")))
 
        line = desc
        if fields:
            line += f"  [{', '.join(fields)}]"
        lines.append(line)
 
    if len(codes) == 1 and codes[0][1]:
        code, url = codes[0]
        code_cell = f'=HYPERLINK("{url}", "{code}")'
    else:
        code_cell = ", ".join(c for c, _ in codes)
 
    return code_cell, "\n".join(lines)
 
 
def status_rank(status):
    return STATUS_RANK.get(status, 1)
 
 
# --------------------------------------------------------------------------
# Twilio
# --------------------------------------------------------------------------
 
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
 
 
# --------------------------------------------------------------------------
# Google Sheets
# --------------------------------------------------------------------------
 
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
    ws.clear()
 
    ws.resize(
        rows=max(len(rows), 2),
        cols=max(len(rows[0]), 1),
    )
 
    ws.update(
        rows,
        value_input_option="USER_ENTERED",
    )
 
 
def rgb(hex_color):
    h = hex_color.lstrip("#")
    return {
        "red": int(h[0:2], 16) / 255,
        "green": int(h[2:4], 16) / 255,
        "blue": int(h[4:6], 16) / 255,
    }
 
 
def grid(sheet_id, r0, r1, c0, c1):
    return {
        "sheetId": sheet_id,
        "startRowIndex": r0,
        "endRowIndex": r1,
        "startColumnIndex": c0,
        "endColumnIndex": c1,
    }
 
 
def existing_conditional_rule_count(spreadsheet, sheet_id):
    meta = spreadsheet.fetch_sheet_metadata(
        params={"fields": "sheets.properties.sheetId,sheets.conditionalFormats"}
    )
 
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["sheetId"] == sheet_id:
            return len(sheet.get("conditionalFormats", []))
 
    return 0
 
 
def format_sheet(spreadsheet, ws, n_rows, groups, has_failures):
    """
    Re-applies all formatting from scratch on every run, so the sheet looks
    the same no matter what the previous run left behind.
 
    groups: list of (start_row, end_row) 0-based, end exclusive, one per subaccount.
    """
    sid = ws.id
    n_cols = len(HEADER)
    data = grid(sid, 1, n_rows, 0, n_cols)
    requests_ = []
 
    # 1. Drop old conditional rules (they would pile up every run)
    for _ in range(existing_conditional_rule_count(spreadsheet, sid)):
        requests_.append({"deleteConditionalFormatRule": {"sheetId": sid, "index": 0}})
 
    # 2. Reset all cell formatting
    requests_.append({
        "repeatCell": {
            "range": grid(sid, 0, n_rows, 0, n_cols),
            "cell": {"userEnteredFormat": {}},
            "fields": "userEnteredFormat",
        }
    })
 
    # 3. Base style for data rows
    requests_.append({
        "repeatCell": {
            "range": data,
            "cell": {"userEnteredFormat": {
                "verticalAlignment": "MIDDLE",
                "wrapStrategy": "CLIP",
                "padding": {"left": 6, "right": 6, "top": 3, "bottom": 3},
                "textFormat": {"fontFamily": "Inter", "fontSize": 10,
                               "foregroundColor": rgb("#1F2933")},
            }},
            "fields": "userEnteredFormat.verticalAlignment,userEnteredFormat.wrapStrategy,userEnteredFormat.padding,userEnteredFormat.textFormat",
        }
    })
 
    # 4. Alternate light background per subaccount + divider line between groups
    for i, (start, end) in enumerate(groups):
        if i % 2 == 1:
            requests_.append({
                "repeatCell": {
                    "range": grid(sid, start, end, 0, n_cols),
                    "cell": {"userEnteredFormat": {"backgroundColor": rgb("#F4F6F9")}},
                    "fields": "userEnteredFormat.backgroundColor",
                }
            })
        if i > 0:
            requests_.append({
                "updateBorders": {
                    "range": grid(sid, start, end, 0, n_cols),
                    "top": {"style": "SOLID", "color": rgb("#C9D1DC")},
                }
            })
        # Show the subaccount name only once per group, in bold
        requests_.append({
            "repeatCell": {
                "range": grid(sid, start, start + 1, 0, 1),
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }
        })
        if end - start > 1:
            requests_.append({
                "repeatCell": {
                    "range": grid(sid, start + 1, end, 0, 1),
                    "cell": {"userEnteredFormat": {"textFormat": {
                        "foregroundColor": rgb("#9AA5B1")}}},
                    "fields": "userEnteredFormat.textFormat.foregroundColor",
                }
            })
 
    # 5. Column-specific styles
    requests_ += [
        {   # Status: centered, bold
            "repeatCell": {
                "range": grid(sid, 1, n_rows, COL_STATUS, COL_STATUS + 1),
                "cell": {"userEnteredFormat": {
                    "horizontalAlignment": "CENTER",
                    "textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.horizontalAlignment,userEnteredFormat.textFormat.bold",
            }
        },
        {   # Error code: centered
            "repeatCell": {
                "range": grid(sid, 1, n_rows, COL_ERROR, COL_ERROR + 1),
                "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
                "fields": "userEnteredFormat.horizontalAlignment",
            }
        },
        {   # Failure reason: wrap long text
            "repeatCell": {
                "range": grid(sid, 1, n_rows, COL_REASON, COL_REASON + 1),
                "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                "fields": "userEnteredFormat.wrapStrategy",
            }
        },
        {   # Brand SID + run date: small grey monospace
            "repeatCell": {
                "range": grid(sid, 1, n_rows, COL_SID, n_cols),
                "cell": {"userEnteredFormat": {"textFormat": {
                    "fontFamily": "Roboto Mono", "fontSize": 9,
                    "foregroundColor": rgb("#7B8794")}}},
                "fields": "userEnteredFormat.textFormat.fontFamily,userEnteredFormat.textFormat.fontSize,userEnteredFormat.textFormat.foregroundColor",
            }
        },
    ]
 
    # 6. Header
    requests_ += [
        {
            "repeatCell": {
                "range": grid(sid, 0, 1, 0, n_cols),
                "cell": {"userEnteredFormat": {
                    "backgroundColor": rgb("#1F3A5F"),
                    "verticalAlignment": "MIDDLE",
                    "wrapStrategy": "WRAP",
                    "padding": {"left": 6, "right": 6},
                    "textFormat": {"fontFamily": "Inter", "fontSize": 10,
                                   "bold": True, "foregroundColor": rgb("#FFFFFF")},
                }},
                "fields": "userEnteredFormat",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": sid, "dimension": "ROWS",
                          "startIndex": 0, "endIndex": 1},
                "properties": {"pixelSize": 36},
                "fields": "pixelSize",
            }
        },
    ]
 
    # 7. Column widths
    for i, width in enumerate(COLUMN_WIDTHS):
        requests_.append({
            "updateDimensionProperties": {
                "range": {"sheetId": sid, "dimension": "COLUMNS",
                          "startIndex": i, "endIndex": i + 1},
                "properties": {"pixelSize": width},
                "fields": "pixelSize",
            }
        })
 
    # 8. Freeze header + first column, hide gridlines, tab color
    requests_.append({
        "updateSheetProperties": {
            "properties": {
                "sheetId": sid,
                "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 1,
                                   "hideGridlines": True},
                "tabColor": rgb("#D64545" if has_failures else "#2E9E5B"),
            },
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount,gridProperties.hideGridlines,tabColor",
        }
    })
 
    # 9. Filter buttons on the header
    requests_.append({
        "setBasicFilter": {"filter": {"range": grid(sid, 0, n_rows, 0, n_cols)}}
    })
 
    # 10. Conditional colors (first matching rule wins, so status cell rules go first)
    status_range = grid(sid, 1, n_rows, COL_STATUS, COL_STATUS + 1)
    status_styles = [
        ("VERIFIED", "#DCF3E5", "#1E7B45"),
        ("FAILED", "#FBDADA", "#B3261E"),
        ("IN_PROGRESS", "#FFF1C2", "#8A6100"),
        ("PENDING", "#FFF1C2", "#8A6100"),
        (NO_TOKEN, "#FFE3CC", "#9C4A00"),
        (NO_CAMPAIGN, "#ECEFF3", "#616E7C"),
        (NO_SERVICES, "#ECEFF3", "#616E7C"),
    ]
 
    rules = []
    for text, bg, fg in status_styles:
        rules.append({
            "ranges": [status_range],
            "booleanRule": {
                "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": text}]},
                "format": {"backgroundColor": rgb(bg),
                           "textFormat": {"foregroundColor": rgb(fg), "bold": True}},
            },
        })
 
    status_col = chr(ord("A") + COL_STATUS)
    rules += [
        {   # Whole row tinted red when the campaign failed
            "ranges": [data],
            "booleanRule": {
                "condition": {"type": "CUSTOM_FORMULA",
                              "values": [{"userEnteredValue": f'=${status_col}2="FAILED"'}]},
                "format": {"backgroundColor": rgb("#FFF4F4")},
            },
        },
        {   # Placeholder dashes in light grey
            "ranges": [data],
            "booleanRule": {
                "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "—"}]},
                "format": {"textFormat": {"foregroundColor": rgb("#C1C7CD")}},
            },
        },
    ]
 
    for index, rule in enumerate(rules):
        requests_.append({"addConditionalFormatRule": {"rule": rule, "index": index}})
 
    spreadsheet.batch_update({"requests": requests_})
 
 
# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
 
def main():
    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
 
    markdown = [
        f"# Twilio A2P Result ({run_at})",
        "",
    ]
 
    # Each item: (subaccount_name, [row, row, ...])
    groups_data = []
 
    subaccounts = get_subaccounts()
 
    for sub in subaccounts:
        sub_sid = sub.get("sid", "—")
        sub_name = sub.get("friendly_name", "—")
        sub_rows = []
 
        def add_row(service, use_case, status, error="", reason="", brand="—"):
            sub_rows.append([sub_name, service, use_case, status,
                             error, reason, brand, run_at])
 
        markdown.append(f"## {sub_name}")
        markdown.append("")
 
        sub_token = get_subaccount_auth_token(sub_sid)
 
        if not sub_token:
            add_row("—", "—", NO_TOKEN, reason="Could not fetch subaccount auth token")
            markdown.append("- Could not fetch subaccount auth token")
            markdown.append("")
            groups_data.append((sub_name, sub_rows))
            continue
 
        services = get_services(sub_sid, sub_token)
 
        if not services:
            add_row("—", "—", NO_SERVICES, reason="No Messaging Services found")
            markdown.append("- No Messaging Services found")
            markdown.append("")
            groups_data.append((sub_name, sub_rows))
            continue
 
        for svc in services:
            service_sid = svc.get("sid", "—")
            service_name = svc.get("friendly_name", "—")
 
            campaigns = get_campaigns(sub_sid, sub_token, service_sid)
 
            if not campaigns:
                add_row(service_name, "—", NO_CAMPAIGN, reason="No A2P Campaign found")
                markdown.append(f"- {service_name}: No A2P Campaign found")
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
 
                failure_raw = get_raw(
                    campaign,
                    "failure_reason",
                    "failureReason",
                    "errors",
                )
                error_code, reason = format_failure(failure_raw)
 
                brand_sid = get_value(
                    campaign,
                    "brand_registration_sid",
                    "brandRegistrationSid",
                )
 
                add_row(service_name, use_case, status, error_code, reason, brand_sid)
 
                markdown.append(f"- {service_name} | {use_case} | {status}")
 
        markdown.append("")
        groups_data.append((sub_name, sub_rows))
 
    # Problems first: sort rows inside each subaccount, then subaccounts by worst status
    for _, sub_rows in groups_data:
        sub_rows.sort(key=lambda r: (status_rank(r[COL_STATUS]), r[1]))
 
    groups_data.sort(key=lambda g: (
        min(status_rank(r[COL_STATUS]) for r in g[1]),
        g[0].lower(),
    ))
 
    rows = [HEADER]
    groups = []
 
    for _, sub_rows in groups_data:
        start = len(rows)
        rows.extend(sub_rows)
        groups.append((start, len(rows)))
 
    has_failures = any(r[COL_STATUS] == "FAILED" for r in rows[1:])
 
    spreadsheet = get_sheet()
    ws = ensure_worksheet(spreadsheet, SHEET_TITLE)
 
    replace_sheet(ws, rows)
    format_sheet(spreadsheet, ws, len(rows), groups, has_failures)
 
    with open("result.md", "w") as f:
        f.write("\n".join(markdown))
 
    print("🎉 Done!")
 
 
if __name__ == "__main__":
    main()
