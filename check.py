Twilio a2p monitor · PY
#!/usr/bin/env python3
 
import os
import re
import json
import difflib
import requests
import gspread
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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
NUMBERS_SHEET_TITLE = "Phone Numbers"
 
HEADER = [
    "Subaccount Name",
    "Messaging Service Name",
    "Use Case",
    "Campaign Status",
    "Error Code",
    "Failure Reason",
    "Phone Numbers",
    "Brand Registration SID",
    "Run Date",
]
 
# Column indexes (0-based), used by the formatter
COL_STATUS = HEADER.index("Campaign Status")
COL_ERROR = HEADER.index("Error Code")
COL_REASON = HEADER.index("Failure Reason")
COL_NUMBERS = HEADER.index("Phone Numbers")
COL_SID = HEADER.index("Brand Registration SID")
 
COLUMN_WIDTHS = [230, 270, 175, 135, 90, 440, 300, 290, 150]
 
NUMBERS_HEADER = [
    "Subaccount Name",
    "Phone Number",
    "Friendly Name",
    "Messaging Service Name",
    "Campaign Status",
    "Next Step",
    "Capabilities",
    "Date Added",
    "Phone Number SID",
    "Run Date",
]
 
N_COL_NUMBER = NUMBERS_HEADER.index("Phone Number")
N_COL_SERVICE = NUMBERS_HEADER.index("Messaging Service Name")
N_COL_STATUS = NUMBERS_HEADER.index("Campaign Status")
N_COL_NEXT = NUMBERS_HEADER.index("Next Step")
N_COL_SID = NUMBERS_HEADER.index("Phone Number SID")
 
NUMBERS_COLUMN_WIDTHS = [230, 150, 200, 270, 135, 230, 140, 105, 290, 150]
 
# Status shown when a subaccount/service has nothing to report
NO_CAMPAIGN = "NO CAMPAIGN"
NO_SERVICES = "NO SERVICES"
NO_TOKEN = "NO ACCESS"
NOT_IN_SERVICE = "NOT IN SERVICE"
NO_NUMBERS = "NO NUMBERS"
 
MAIN_SHEET_TITLE = "Main Account Numbers"
 
MAIN_HEADER = [
    "Phone Number",
    "Friendly Name",
    "Messaging Service Name",
    "Match Status",
    "Matched Subaccount",
    "Next Step",
    "Capabilities",
    "Date Added",
    "Phone Number SID",
    "Run Date",
]
 
M_COL_STATUS = MAIN_HEADER.index("Match Status")
M_COL_MATCH = MAIN_HEADER.index("Matched Subaccount")
M_COL_NEXT = MAIN_HEADER.index("Next Step")
M_COL_SERVICE = MAIN_HEADER.index("Messaging Service Name")
M_COL_SID = MAIN_HEADER.index("Phone Number SID")
 
MAIN_COLUMN_WIDTHS = [250, 230, 260, 160, 230, 280, 140, 105, 290, 150]
 
NO_SUBACCOUNT = "NO SUBACCOUNT"
HAS_SUBACCOUNT = "SUBACCOUNT EXISTS"
UNNAMED = "UNNAMED"
INTERNAL = "INTERNAL"
 
MATCH_GROUPS = {
    NO_SUBACCOUNT: "No matching subaccount",
    HAS_SUBACCOUNT: "Partner already has a subaccount",
    UNNAMED: "Can't tell whose number it is",
    INTERNAL: "AgVend internal / test",
}
 
# Numbers whose name contains one of these are treated as AgVend's own
INTERNAL_KEYWORDS = ["agvend", "test", "qa", "demo", "sandbox", "internal"]
 
# Words that say nothing about the partner and are ignored when matching names
NAME_STOP_WORDS = {
    "inc", "llc", "ltd", "lp", "co", "corp", "corporation", "company", "the",
    "of", "and", "cooperative", "coop", "cooperatives", "partners", "group",
    "messaging", "service", "services", "a2p", "sms", "mms", "default",
    "marketing", "account", "notification", "notifications", "conversations",
    "for", "number", "numbers", "phone", "main", "primary", "new", "old",
}
 
# "Next Step" hints on the Phone Numbers tab
READY_TO_MOVE = "Ready: move numbers here"
NEXT_STEPS = {
    "FAILED": "Fix campaign and resubmit",
    "IN_PROGRESS": "Wait for campaign review",
    "PENDING": "Wait for campaign review",
    NOT_IN_SERVICE: "Attach to a Messaging Service",
    NO_CAMPAIGN: "Register an A2P campaign",
    NO_TOKEN: "Check subaccount access",
}
READY_STYLE = ("#DCEBFF", "#1A56C4")  # bright blue: "everything is ready"
 
# Lower rank = more urgent = higher in the sheet
STATUS_RANK = {
    "FAILED": 0,
    "IN_PROGRESS": 1,
    "PENDING": 1,
    NOT_IN_SERVICE: 1,
    NO_TOKEN: 2,
    NO_SERVICES: 3,
    NO_CAMPAIGN: 3,
    NO_NUMBERS: 3,
    "VERIFIED": 4,
    NO_SUBACCOUNT: 0,
    HAS_SUBACCOUNT: 1,
    UNNAMED: 2,
    INTERNAL: 3,
}
 
STATUS_STYLES = [
    # (status text, background, text color)
    ("VERIFIED", "#DCF3E5", "#1E7B45"),
    ("FAILED", "#FBDADA", "#B3261E"),
    ("IN_PROGRESS", "#FFF1C2", "#8A6100"),
    ("PENDING", "#FFF1C2", "#8A6100"),
    (NOT_IN_SERVICE, "#FFE3CC", "#9C4A00"),
    (NO_TOKEN, "#FFE3CC", "#9C4A00"),
    (NO_CAMPAIGN, "#ECEFF3", "#616E7C"),
    (NO_SERVICES, "#ECEFF3", "#616E7C"),
    (NO_NUMBERS, "#ECEFF3", "#616E7C"),
    (NO_SUBACCOUNT, "#FBDADA", "#B3261E"),
    (HAS_SUBACCOUNT, "#FFE3CC", "#9C4A00"),
    (UNNAMED, "#ECEFF3", "#616E7C"),
    (INTERNAL, "#ECEFF3", "#616E7C"),
]
 
 
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
 
 
def worst_status(statuses):
    statuses = [s for s in statuses if s]
    if not statuses:
        return NO_CAMPAIGN
    return min(statuses, key=status_rank)
 
 
def as_text(value):
    """Leading apostrophe keeps Sheets from turning +15551234567 into a number."""
    return f"'{value}" if value else "—"
 
 
def format_capabilities(caps):
    if not isinstance(caps, dict):
        return "—"
    labels = [("sms", "SMS"), ("mms", "MMS"), ("voice", "Voice"), ("fax", "Fax")]
    enabled = [label for key, label in labels if caps.get(key) or caps.get(key.upper())]
    return " · ".join(enabled) or "—"
 
 
def format_date(value):
    if not value:
        return "—"
    try:
        return parsedate_to_datetime(value).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return str(value)[:10]
 
 
def name_tokens(text):
    text = (text or "").lower().replace("&", " and ")
    tokens = re.findall(r"[a-z0-9]+", text)
    return [t for t in tokens if t not in NAME_STOP_WORDS and not t.isdigit()]
 
 
def has_letters(text):
    return bool(re.search(r"[A-Za-z]", text or ""))
 
 
def match_subaccounts(texts, subaccounts):
    """
    Which subaccounts does a number belong to, judging by its friendly name and
    the names of the Messaging Services it sits in? Returns matching names.
    """
    tokens = set()
    for t in texts:
        tokens.update(name_tokens(t))
 
    if not tokens:
        return []
 
    # "Herbers Ag" -> "herbersag", so spacing and small typos don't matter
    squashed_texts = ["".join(name_tokens(t)) for t in texts if name_tokens(t)]
    matches = []
 
    for sub in subaccounts:
        name = sub.get("friendly_name", "")
        sub_tokens = set(name_tokens(name))
        if not sub_tokens:
            continue
 
        # Every meaningful word of the subaccount name appears in the number's name
        if sub_tokens <= tokens:
            matches.append(name)
        # Short name like "Kokomo" for "Kokomo Grain Co., Inc."
        elif tokens <= sub_tokens and any(len(t) >= 5 for t in tokens):
            matches.append(name)
        # Typos / spacing differences ("Herbers Ag" vs "HerbersAg")
        elif any(
            difflib.SequenceMatcher(None, squashed, "".join(name_tokens(name))).ratio() >= 0.85
            for squashed in squashed_texts
        ):
            matches.append(name)
 
    return matches
 
 
def classify_main_number(friendly, service_names, subaccounts):
    texts = [friendly] + list(service_names)
    blob = " ".join(texts).lower()
 
    if any(re.search(rf"\b{k}\b", blob) for k in INTERNAL_KEYWORDS):
        return INTERNAL, "", ""
 
    if not any(has_letters(t) and name_tokens(t) for t in texts):
        return UNNAMED, "", "Give the number a partner name to identify it"
 
    matches = match_subaccounts(texts, subaccounts)
 
    if matches:
        return (HAS_SUBACCOUNT, ", ".join(matches),
                "Check: should it move to that subaccount?")
 
    return NO_SUBACCOUNT, "", "Check: does this partner need a subaccount?"
 
 
# --------------------------------------------------------------------------
# Twilio
# --------------------------------------------------------------------------
 
def t_get_all(url, sid, token, key):
    """Follows Twilio pagination (both the 2010 API and the v1 APIs)."""
    items = []
 
    while url:
        data = t_get(url, sid, token)
        if not data:
            break
 
        items.extend(data.get(key, []))
 
        next_uri = data.get("next_page_uri")
        if next_uri:
            url = f"https://api.twilio.com{next_uri}"
        else:
            url = (data.get("meta") or {}).get("next_page_url")
 
    return items
 
 
def get_subaccounts(apply_filter=True):
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
 
    if ACCOUNT_FILTER and apply_filter:
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
 
 
def get_incoming_numbers(sub_sid, sub_token):
    """Everything in Phone Numbers > Inventory for this subaccount."""
    return t_get_all(
        f"{TWILIO_BASE}/Accounts/{sub_sid}/IncomingPhoneNumbers.json?PageSize=1000",
        sub_sid,
        sub_token,
        "incoming_phone_numbers",
    )
 
 
def get_service_numbers(sub_sid, sub_token, service_sid):
    """Phone numbers attached to a Messaging Service (its sender pool)."""
    return t_get_all(
        f"{MSG_BASE}/Services/{service_sid}/PhoneNumbers?PageSize=1000",
        sub_sid,
        sub_token,
        "phone_numbers",
    )
 
 
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
 
 
def format_sheet(spreadsheet, ws, n_rows, groups, *, header, widths,
                 status_col, tab_color, center_cols=(), wrap_cols=(),
                 mono_cols=(), muted_from_col=None, bold_cols=(), extra_rules=(),
                 dim_first_col=True):
    """
    Re-applies all formatting from scratch on every run, so the sheet looks
    the same no matter what the previous run left behind.
 
    groups: list of (start_row, end_row) 0-based, end exclusive, one per subaccount.
    """
    sid = ws.id
    n_cols = len(header)
    data = grid(sid, 1, n_rows, 0, n_cols)
    requests_ = []
 
    def col_style(col_from, col_to, fmt, fields):
        requests_.append({
            "repeatCell": {
                "range": grid(sid, 1, n_rows, col_from, col_to),
                "cell": {"userEnteredFormat": fmt},
                "fields": fields,
            }
        })
 
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
    col_style(0, n_cols, {
        "verticalAlignment": "MIDDLE",
        "wrapStrategy": "CLIP",
        "padding": {"left": 6, "right": 6, "top": 3, "bottom": 3},
        "textFormat": {"fontFamily": "Inter", "fontSize": 10,
                       "foregroundColor": rgb("#1F2933")},
    }, "userEnteredFormat.verticalAlignment,userEnteredFormat.wrapStrategy,"
       "userEnteredFormat.padding,userEnteredFormat.textFormat")
 
    # 4. Subaccount blocks: a colored band row with the subaccount name + SID,
    #    then its rows, then a thick line before the next subaccount
    for band, start, end in groups:
        requests_ += [
            {
                "repeatCell": {
                    "range": grid(sid, band, band + 1, 0, n_cols),
                    "cell": {"userEnteredFormat": {
                        "backgroundColor": rgb("#D9E2EE"),
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "OVERFLOW_CELL",
                        "padding": {"left": 6, "right": 6},
                        "textFormat": {"fontFamily": "Inter", "fontSize": 11, "bold": True,
                                       "foregroundColor": rgb("#1F3A5F")},
                    }},
                    "fields": "userEnteredFormat",
                }
            },
            {   # Subaccount SID / summary next to the name: smaller and lighter
                "repeatCell": {
                    "range": grid(sid, band, band + 1, 1, n_cols),
                    "cell": {"userEnteredFormat": {"textFormat": {
                        "fontFamily": "Roboto Mono", "fontSize": 9, "bold": False,
                        "foregroundColor": rgb("#52606D")}}},
                    "fields": "userEnteredFormat.textFormat",
                }
            },
            {
                "updateBorders": {
                    "range": grid(sid, band, band + 1, 0, n_cols),
                    "top": {"style": "SOLID_THICK", "color": rgb("#1F3A5F")},
                }
            },
        ]
        if end > start and dim_first_col:  # name repeated on data rows, very light
            requests_.append({
                "repeatCell": {
                    "range": grid(sid, start, end, 0, 1),
                    "cell": {"userEnteredFormat": {
                        "padding": {"left": 18, "right": 6, "top": 3, "bottom": 3},
                        "textFormat": {"foregroundColor": rgb("#B0B8C1")}}},
                    "fields": "userEnteredFormat.padding,userEnteredFormat.textFormat.foregroundColor",
                }
            })
 
    # 5. Column-specific styles
    col_style(status_col, status_col + 1,
              {"horizontalAlignment": "CENTER", "textFormat": {"bold": True}},
              "userEnteredFormat.horizontalAlignment,userEnteredFormat.textFormat.bold")
 
    for c in center_cols:
        col_style(c, c + 1, {"horizontalAlignment": "CENTER"},
                  "userEnteredFormat.horizontalAlignment")
 
    for c in wrap_cols:
        col_style(c, c + 1, {"wrapStrategy": "WRAP"}, "userEnteredFormat.wrapStrategy")
 
    for c in bold_cols:
        col_style(c, c + 1, {"textFormat": {"bold": True}},
                  "userEnteredFormat.textFormat.bold")
 
    for c in mono_cols:
        col_style(c, c + 1, {"textFormat": {"fontFamily": "Roboto Mono"}},
                  "userEnteredFormat.textFormat.fontFamily")
 
    if muted_from_col is not None:  # SIDs + run date: small grey monospace
        col_style(muted_from_col, n_cols, {"textFormat": {
            "fontFamily": "Roboto Mono", "fontSize": 9,
            "foregroundColor": rgb("#7B8794")}},
            "userEnteredFormat.textFormat.fontFamily,userEnteredFormat.textFormat.fontSize,"
            "userEnteredFormat.textFormat.foregroundColor")
 
    # Fit row heights to the new content (old heights would otherwise stick
    # to whatever row now sits there), then make the subaccount bands taller
    requests_.append({
        "autoResizeDimensions": {
            "dimensions": {"sheetId": sid, "dimension": "ROWS",
                           "startIndex": 1, "endIndex": n_rows},
        }
    })
    for band, _, _ in groups:
        requests_.append({
            "updateDimensionProperties": {
                "range": {"sheetId": sid, "dimension": "ROWS",
                          "startIndex": band, "endIndex": band + 1},
                "properties": {"pixelSize": 30},
                "fields": "pixelSize",
            }
        })
 
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
    for i, width in enumerate(widths):
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
                "tabColor": rgb(tab_color),
            },
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount,"
                      "gridProperties.hideGridlines,tabColor",
        }
    })
 
    # 9. Filter buttons on the header
    requests_.append({
        "setBasicFilter": {"filter": {"range": grid(sid, 0, n_rows, 0, n_cols)}}
    })
 
    # 10. Conditional colors (first matching rule wins, so status cell rules go first)
    status_range = grid(sid, 1, n_rows, status_col, status_col + 1)
    rules = []
 
    for text, bg, fg in STATUS_STYLES:
        rules.append({
            "ranges": [status_range],
            "booleanRule": {
                "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": text}]},
                "format": {"backgroundColor": rgb(bg),
                           "textFormat": {"foregroundColor": rgb(fg), "bold": True}},
            },
        })
 
    # Caller-specific highlights (e.g. "ready to move numbers")
    rules += [{**rule, "ranges": [grid(sid, 1, n_rows, *rule["cols"])]}
              for rule in extra_rules]
    for rule in rules:
        rule.pop("cols", None)
 
    status_letter = chr(ord("A") + status_col)
    rules += [
        {   # Whole row tinted red when the campaign failed
            "ranges": [data],
            "booleanRule": {
                "condition": {"type": "CUSTOM_FORMULA",
                              "values": [{"userEnteredValue": f'=${status_letter}2="FAILED"'}]},
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
 
 
def tab_color_for(statuses):
    if "FAILED" in statuses:
        return "#D64545"
    if any(s in statuses for s in ("IN_PROGRESS", "PENDING", NOT_IN_SERVICE, NO_TOKEN)):
        return "#E8A33D"
    return "#2E9E5B"
 
 
def build_rows(header, groups_data, rank, summary, band_prefix="Subaccount"):
    """
    Problems first: sort rows inside each subaccount, then subaccounts by their
    most urgent row. Each subaccount starts with a band row: name | SID · summary.
 
    groups_data: list of (subaccount_name, subaccount_sid, rows)
    Returns rows and [(band_row, first_data_row, end_row)].
    """
    for _, _, sub_rows in groups_data:
        sub_rows.sort(key=lambda r: (rank(r), str(r[1])))
 
    groups_data.sort(key=lambda g: (
        min((rank(r) for r in g[2]), default=99),
        g[0].lower(),
    ))
 
    rows, groups = [header], []
 
    for name, sub_sid, sub_rows in groups_data:
        band = len(rows)
        rows.append([name, f"{band_prefix} {sub_sid}  ·  {summary(sub_rows)}"]
                    + [""] * (len(header) - 2))
        rows.extend(sub_rows)
        groups.append((band, band + 1, len(rows)))
 
    return rows, groups
 
 
def plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")
 
 
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
    campaign_groups = []
    number_groups = []
 
    subaccounts = get_subaccounts()
 
    for sub in subaccounts:
        sub_sid = sub.get("sid", "—")
        sub_name = sub.get("friendly_name", "—")
        sub_rows = []
        num_rows = []
 
        def add_row(service, use_case, status, error="", reason="",
                    numbers="—", brand="—"):
            sub_rows.append([sub_name, service, use_case, status,
                             error, reason, numbers, brand, run_at])
 
        def add_number_row(number, friendly, service, status,
                           caps="—", added="—", pn_sid="—", next_step=None):
            if next_step is None:
                next_step = NEXT_STEPS.get(status, "")
            num_rows.append([sub_name, as_text(number), friendly, service, status,
                             next_step, caps, added, pn_sid, run_at])
 
        markdown.append(f"## {sub_name}")
        markdown.append("")
 
        sub_token = get_subaccount_auth_token(sub_sid)
 
        if not sub_token:
            add_row("—", "—", NO_TOKEN, reason="Could not fetch subaccount auth token")
            add_number_row("", "—", "—", NO_TOKEN)
            markdown.append("- Could not fetch subaccount auth token")
            markdown.append("")
            campaign_groups.append((sub_name, sub_sid, sub_rows))
            number_groups.append((sub_name, sub_sid, num_rows))
            continue
 
        # Phone Numbers > Inventory
        inventory = get_incoming_numbers(sub_sid, sub_token)
 
        # number SID -> [(service name, campaign status)]
        number_links = {}
 
        # Services with an approved campaign but an empty sender pool
        ready_services = []
 
        services = get_services(sub_sid, sub_token)
 
        if not services:
            add_row("—", "—", NO_SERVICES, reason="No Messaging Services found")
            markdown.append("- No Messaging Services found")
 
        for svc in services:
            service_sid = svc.get("sid", "—")
            service_name = svc.get("friendly_name", "—")
 
            campaigns = get_campaigns(sub_sid, sub_token, service_sid)
            service_numbers = get_service_numbers(sub_sid, sub_token, service_sid)
 
            numbers_cell = as_text(", ".join(
                n.get("phone_number", "") for n in service_numbers
            ))
            # Filled in below once we know the campaign status
 
            if not campaigns:
                add_row(service_name, "—", NO_CAMPAIGN,
                        reason="No A2P Campaign found", numbers=numbers_cell)
                markdown.append(f"- {service_name}: No A2P Campaign found")
                service_status = NO_CAMPAIGN
 
            else:
                statuses = []
 
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
                    statuses.append(status)
 
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
 
                    if status == "VERIFIED" and not service_numbers:
                        cell = READY_TO_MOVE
                    else:
                        cell = numbers_cell
 
                    add_row(service_name, use_case, status, error_code, reason,
                            cell, brand_sid)
 
                    markdown.append(f"- {service_name} | {use_case} | {status}")
 
                service_status = worst_status(statuses)
 
                if service_status == "VERIFIED" and not service_numbers:
                    ready_services.append(service_name)
 
            for n in service_numbers:
                number_links.setdefault(n.get("sid"), []).append(
                    (service_name, service_status)
                )
 
        # Approved campaign, no numbers yet: time to move numbers in
        for service_name in ready_services:
            add_number_row("", "—", service_name, "VERIFIED",
                           next_step=READY_TO_MOVE)
            markdown.append(f"- {service_name}: VERIFIED, no numbers yet")
 
        # One row per number in the inventory
        if not inventory and not ready_services:
            add_number_row("", "—", "—", NO_NUMBERS)
            markdown.append("- Phone numbers: none")
 
        for num in inventory:
            links = number_links.get(num.get("sid"), [])
 
            if links:
                service = ", ".join(name for name, _ in links)
                status = worst_status([st for _, st in links])
            else:
                service, status = "—", NOT_IN_SERVICE
 
            add_number_row(
                num.get("phone_number", ""),
                num.get("friendly_name") or "—",
                service,
                status,
                format_capabilities(num.get("capabilities")),
                format_date(num.get("date_created")),
                num.get("sid", "—"),
            )
            markdown.append(f"- {num.get('phone_number')} | {service} | {status}")
 
        markdown.append("")
        campaign_groups.append((sub_name, sub_sid, sub_rows))
        number_groups.append((sub_name, sub_sid, num_rows))
 
    # ---- Main AgVend account: numbers that don't belong to any subaccount ----
    all_subaccounts = get_subaccounts(apply_filter=False)
    main_buckets = {status: [] for status in MATCH_GROUPS}
 
    main_links = {}  # number SID -> [service names]
    for svc in get_services(MASTER_SID, MASTER_TOKEN):
        for n in get_service_numbers(MASTER_SID, MASTER_TOKEN, svc.get("sid")):
            main_links.setdefault(n.get("sid"), []).append(svc.get("friendly_name", "—"))
 
    markdown += ["## Main account numbers", ""]
 
    for num in get_incoming_numbers(MASTER_SID, MASTER_TOKEN):
        friendly = num.get("friendly_name") or ""
        services = main_links.get(num.get("sid"), [])
        status, matched, next_step = classify_main_number(friendly, services, all_subaccounts)
 
        main_buckets[status].append([
            as_text(num.get("phone_number", "")),
            friendly or "—",
            ", ".join(services) or "—",
            status,
            matched or "—",
            next_step,
            format_capabilities(num.get("capabilities")),
            format_date(num.get("date_created")),
            num.get("sid", "—"),
            run_at,
        ])
        markdown.append(f"- {num.get('phone_number')} | {friendly} | {status} {matched}")
 
    main_groups = [(MATCH_GROUPS[status], MASTER_SID, bucket)
                   for status, bucket in main_buckets.items() if bucket]
 
    main_rows, main_groups = build_rows(
        MAIN_HEADER, main_groups,
        rank=lambda r: status_rank(r[M_COL_STATUS]),
        summary=lambda rs: plural(len(rs), "phone number"),
        band_prefix="Main account",
    )
 
    rows, groups = build_rows(
        HEADER, campaign_groups,
        rank=lambda r: status_rank(r[COL_STATUS]),
        summary=lambda rs: plural(len({r[1] for r in rs if r[1] != "—"}),
                                  "messaging service"),
    )
 
    def number_rank(r):
        if r[N_COL_NEXT] == READY_TO_MOVE:
            return 0.5  # right after FAILED
        return status_rank(r[N_COL_STATUS])
 
    num_rows, num_groups = build_rows(
        NUMBERS_HEADER, number_groups,
        rank=number_rank,
        summary=lambda rs: plural(sum(1 for r in rs if r[N_COL_NUMBER] != "—"),
                                  "phone number"),
    )
 
    ready_bg, ready_fg = READY_STYLE
    ready_format = {"backgroundColor": rgb(ready_bg),
                    "textFormat": {"foregroundColor": rgb(ready_fg), "bold": True}}
    next_letter = chr(ord("A") + N_COL_NEXT)
 
    spreadsheet = get_sheet()
 
    ws = ensure_worksheet(spreadsheet, SHEET_TITLE)
    replace_sheet(ws, rows)
    format_sheet(
        spreadsheet, ws, len(rows), groups,
        header=HEADER,
        widths=COLUMN_WIDTHS,
        status_col=COL_STATUS,
        tab_color=tab_color_for({r[COL_STATUS] for r in rows[1:]}),
        center_cols=[COL_ERROR],
        wrap_cols=[COL_REASON, COL_NUMBERS],
        mono_cols=[COL_NUMBERS],
        muted_from_col=COL_SID,
        extra_rules=[{
            "cols": (COL_NUMBERS, COL_NUMBERS + 1),
            "booleanRule": {
                "condition": {"type": "TEXT_EQ",
                              "values": [{"userEnteredValue": READY_TO_MOVE}]},
                "format": ready_format,
            },
        }],
    )
 
    ws_numbers = ensure_worksheet(spreadsheet, NUMBERS_SHEET_TITLE)
    replace_sheet(ws_numbers, num_rows)
    format_sheet(
        spreadsheet, ws_numbers, len(num_rows), num_groups,
        header=NUMBERS_HEADER,
        widths=NUMBERS_COLUMN_WIDTHS,
        status_col=N_COL_STATUS,
        tab_color=tab_color_for({r[N_COL_STATUS] for r in num_rows[1:]}),
        mono_cols=[N_COL_NUMBER],
        wrap_cols=[N_COL_SERVICE],
        bold_cols=[N_COL_NEXT],
        muted_from_col=N_COL_SID,
        extra_rules=[{
            # Whole row bright blue: approved campaign, no numbers yet
            "cols": (1, len(NUMBERS_HEADER)),
            "booleanRule": {
                "condition": {"type": "CUSTOM_FORMULA",
                              "values": [{"userEnteredValue":
                                          f'=${next_letter}2="{READY_TO_MOVE}"'}]},
                "format": ready_format,
            },
        }],
    )
 
    ws_main = ensure_worksheet(spreadsheet, MAIN_SHEET_TITLE)
    replace_sheet(ws_main, main_rows if len(main_rows) > 1
                  else main_rows + [["No phone numbers on the main account"]
                                    + [""] * (len(MAIN_HEADER) - 1)])
    main_statuses = {r[M_COL_STATUS] for r in main_rows[1:]}
    format_sheet(
        spreadsheet, ws_main, max(len(main_rows), 2), main_groups,
        header=MAIN_HEADER,
        widths=MAIN_COLUMN_WIDTHS,
        status_col=M_COL_STATUS,
        tab_color=("#D64545" if NO_SUBACCOUNT in main_statuses
                   else "#E8A33D" if HAS_SUBACCOUNT in main_statuses
                   else "#2E9E5B"),
        wrap_cols=[M_COL_SERVICE, M_COL_MATCH],
        bold_cols=[M_COL_NEXT],
        muted_from_col=M_COL_SID,
        dim_first_col=False,
    )
 
    with open("result.md", "w") as f:
        f.write("\n".join(markdown))
 
    print("🎉 Done!")
 
 
if __name__ == "__main__":
    main()
