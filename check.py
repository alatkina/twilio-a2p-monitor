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
    "Numbers in Service",
    "Brand Registration SID",
    "Run Date",
]
 
# Column indexes (0-based), used by the formatter
COL_STATUS = HEADER.index("Campaign Status")
COL_ERROR = HEADER.index("Error Code")
COL_REASON = HEADER.index("Failure Reason")
COL_NUMBERS = HEADER.index("Numbers in Service")
COL_SID = HEADER.index("Brand Registration SID")
 
COLUMN_WIDTHS = [230, 270, 175, 135, 90, 440, 200, 290, 150]
 
# Number types = columns of the "Phone Numbers" tab.
# A number's type comes from its Friendly Name; if that says nothing, from the
# name of the Messaging Service it sits in. One number can have several types
# ("Kokomo - CashBids/Marketing/Sales"). Matching ignores case and punctuation.
NUMBER_TYPES = [
    ("Cash Bids", ["cashbid", "cash bid"]),
    ("Marketing", ["market"]),
    ("Sales / CRM", ["sales", "crm"]),
    ("System", ["system"]),
    ("Goose", ["goose"]),
]
OTHER_TYPE = "Other"
 
# Numbers whose type can't be read from the name: phone number -> column(s)
NUMBER_TYPE_OVERRIDES = {
    "+12084909353": ["Sales / CRM"],  # Valley Wide Cooperative CRM
}
TYPE_COLUMNS = [label for label, _ in NUMBER_TYPES] + [OTHER_TYPE]
 
NUMBERS_HEADER = (
    ["Subaccount Name", "Numbers in Subaccount"]
    + TYPE_COLUMNS
    + ["Subaccount SID", "Run Date"]
)
 
N_COL_TOTAL = NUMBERS_HEADER.index("Numbers in Subaccount")
N_COL_FIRST_TYPE = NUMBERS_HEADER.index(TYPE_COLUMNS[0])
N_COL_LAST_TYPE = NUMBERS_HEADER.index(OTHER_TYPE)
N_COL_SID = NUMBERS_HEADER.index("Subaccount SID")
 
NUMBERS_COLUMN_WIDTHS = ([230, 110] + [215] * len(TYPE_COLUMNS) + [290, 150])
 
NOT_IN_SERVICE_MARK = "not in service"
ON_MAIN_MARK = "on main"
 
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
    "Number Type",
    "Messaging Service Name",
    "Match Status",
    "Next Step",
    "Capabilities",
    "Date Added",
    "Phone Number SID",
    "Run Date",
]
 
M_COL_STATUS = MAIN_HEADER.index("Match Status")
M_COL_NEXT = MAIN_HEADER.index("Next Step")
M_COL_SERVICE = MAIN_HEADER.index("Messaging Service Name")
M_COL_SID = MAIN_HEADER.index("Phone Number SID")
 
MAIN_COLUMN_WIDTHS = [250, 260, 170, 260, 260, 290, 140, 105, 290, 150]
 
NO_SUBACCOUNT = "NO SUBACCOUNT"
HAS_SUBACCOUNT = "SUBACCOUNT EXISTS"
UNNAMED = "UNNAMED"
INTERNAL = "INTERNAL"
GOOSE = "GOOSE ASSISTANT"
RETIRING = "TO BE DELETED"
 
MATCH_GROUPS = {
    NO_SUBACCOUNT: "No matching subaccount",
    HAS_SUBACCOUNT: "Partner already has a subaccount",
    GOOSE: "Goose Assistant",
    UNNAMED: "Can't tell whose number it is",
    INTERNAL: "AgVend internal / test",
    RETIRING: "Scheduled for removal",
}
 
# Manual decisions for specific numbers, matched by Friendly Name prefix
# (case, spaces and punctuation ignored). Checked before anything else.
#   (name prefix, block, Match Status text, Next Step text)
MANUAL_OVERRIDES = [
    ("toll-free - do NOT use", "INTERNAL", "INTERNAL", ""),
    ("Local DEV - Conversation Agent", "INTERNAL", "INTERNAL", ""),
    ("ICI - Marketing", "TO BE DELETED",
     "Will be deleted on Nov 30, 2026 · no subaccount needed",
     "Delete on Nov 30, 2026"),
    ("ICI - Sales", "TO BE DELETED",
     "Will be deleted on Nov 30, 2026 · no subaccount needed",
     "Delete on Nov 30, 2026"),
]
 
# Numbers whose name contains this word go to the "Goose Assistant" block
GOOSE_KEYWORD = "goose"
 
# Known Friendly Name prefixes on the main account -> subaccount they belong to.
# Matching ignores case, spaces and punctuation ("the_rack" == "The Rack"),
# and the longest prefix wins ("legacy_coop_nebraska" beats "legacycoop").
PARTNER_ALIASES = {
    "Farmers Coop Society": ["FCS", "Farmers Coop Society"],
    "Gold-Eagle Cooperative": ["GoldEagle"],
    "Growmark, Inc.": ["Growmark"],
    "Tri-Ag Products, Inc.": ["Tri Ag"],
    "McEwen’s Fuels & Fertilizers Inc.": ["McEwens"],
    "AgroPlus Inc.": ["AgroPlus"],
    "AgState": ["AgState"],
    "Horizon Fertilizers": ["Horizon Fertilizers", "horizon_fertilizers"],
    "South Central FS, Inc.": ["South Central FS"],
    "Braungardt Agricultural Services": ["braungardt"],
    "ADM": ["adm", "adm_canada", "adm_wholesale"],
    "Ag Partners MN": ["ag_partnersmn"],
    "Ag Valley Co-op": ["ag_valley_coop"],
    "Agri Partners": ["agri_partners"],
    "AgXplore": ["agxplore"],
    "Alcivia": ["Alcivia"],
    "American Plains Coop": ["americanplainscoop"],
    "CenDak Cooperative": ["cendak"],
    "Centerra Co-op": ["centerra"],
    "Centra Sota Cooperative": ["Centra Sota"],
    "Central Farm Service": ["cfs"],
    "Central Missouri AgriService LLC": ["cmas"],
    "Clifford Farmers Elevator": ["clifford_farmers_elevator"],
    "Cooperative Farmers Elevator": ["cfe"],
    "Cooperative Producers Inc.": ["CPI"],
    "Country Partners Cooperative": ["countrypartners"],
    "Country Visions": ["country_visions"],
    "CVA": ["CVA"],
    "Emerge Ag Solutions Inc.": ["emergeagsolution"],
    "Five Star Cooperative": ["Five Star", "five_star"],
    "Frontier": ["frontier_coop"],
    "GreenPoint Ag": ["greenpoint"],
    "Hawks Agro": ["hawksagro"],
    "Heartland Feed Services": ["heartland_feed"],
    "HerbersAg": ["herbersag"],
    "Integrated Agronomy Advisors LLC": ["integratedagronomyadvisors"],
    "Kanza Cooperative": ["kanza_coop"],
    "Kokomo Grain Co., Inc.": ["Kokomo"],
    "Legacy Cooperative": ["legacycoop"],
    "Legacy Cooperative Nebraska": ["legacy_coop_nebraska"],
    "Mercer Landmark": ["mercer_landmark"],
    "Mid Kansas Cooperative": ["mkc"],
    "Midway Coop": ["midwaycoop"],
    "NEW Cooperative": ["NEW Cooperative"],
    "Agtegra": ["Agtegra"],
    "Enerbase Coop": ["Enerbase"],
    "FCD": ["FCD"],
    "Parallel Ag Group": ["Parallel Ag"],
    "Nexus Cooperative": ["Nexus"],
    "NuWay-K&H Cooperative": ["Nuway"],
    "Premier Ag": ["premier_comp"],  # probable match
    "Pro Cooperative": ["pro_coop"],
    "Pro Valley LLC": ["provalley"],
    "Producers Cooperative Association": ["pcacoop"],
    "Rack Petroleum Ltd.": ["the_rack"],
    "Redstar, LLC": ["redstar"],
    "Reichmansales": ["reichmansales"],
    "River Valley": ["river_valley"],
    "Shur Gro": ["shurgro"],
    "Soil Mender": ["Soil Mender"],
    "South West Terminal Ltd": ["southwestterminal"],
    "Superior Ag": ["superiorag"],
    "SYNENERGY PARTNERS, LLC": ["SynEnergy"],
    "Tyree Ag LLC": ["Tyree Ag"],
    "United Cooperative": ["united_coop"],
    "United Farmers Cooperative": ["ufc_mn"],
    "Ursa Coop": ["ursa_coop"],
    "Valley Wide Cooperative": ["valleywide"],
}
 
# Numbers whose name contains one of these are treated as AgVend's own
INTERNAL_KEYWORDS = ["agvend", "tcp", "test", "qa", "demo", "sandbox", "internal"]
 
# Words that say nothing about the partner and are ignored when matching names
NAME_STOP_WORDS = {
    "inc", "llc", "ltd", "lp", "co", "corp", "corporation", "company", "the",
    "of", "and", "cooperative", "coop", "cooperatives", "partners", "group",
    "messaging", "service", "services", "a2p", "sms", "mms", "default",
    "marketing", "account", "notification", "notifications", "conversations",
    "for", "number", "numbers", "phone",
}
 
# "Next Step" hints on the Phone Numbers tab
READY_TO_MOVE = "Ready: move numbers here"
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
    GOOSE: 2,
    UNNAMED: 3,
    INTERNAL: 4,
    RETIRING: 5,
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
    (GOOSE, "#EDE7F6", "#5E35B1"),
    (RETIRING, "#F1F1F1", "#8A8F98"),
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
        # leading apostrophe: stored as text, so Sheets can't turn it into 45068
        return "'" + parsedate_to_datetime(value).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return "'" + str(value)[:10]
 
 
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
 
 
def squash(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())
 
 
def alias_partner(texts):
    """Partner from PARTNER_ALIASES whose prefix starts one of the names (longest wins)."""
    best, best_len = None, 0
 
    for partner, aliases in PARTNER_ALIASES.items():
        for alias in aliases:
            key = squash(alias)
            if len(key) > best_len and any(squash(t).startswith(key) for t in texts):
                best, best_len = partner, len(key)
 
    return best
 
 
def guess_partner_name(friendly):
    """
    "Agtegra - Marketing" -> "Agtegra", "Enerbase Coop Sales" -> "Enerbase Coop".
    Used for numbers on the main account whose partner has no subaccount and is
    not in PARTNER_ALIASES yet, so they still get a row on the Phone Numbers tab.
    """
    if not has_letters(friendly):
        return None
 
    name = re.split(r"\s+-\s+|\s*\[", friendly)[0].strip()
    type_words = {w for _, words in NUMBER_TYPES for w in words} | {"cash", "bids"}
    words = name.split()
    keys = {squash(w) for w in type_words}
    while words and any(squash(words[-1]).startswith(k) for k in keys):
        words.pop()
    name = " ".join(words).strip(" -_/")
    return name or None
 
 
def find_subaccount(partner, subaccounts):
    """The real subaccount name for a partner, or None if there is no such subaccount."""
    key = squash(partner)
 
    for sub in subaccounts:
        name = sub.get("friendly_name", "")
        if squash(name) == key:
            return name
 
    for sub in subaccounts:  # small spelling differences
        name = sub.get("friendly_name", "")
        if difflib.SequenceMatcher(None, squash(name), key).ratio() >= 0.9:
            return name
 
    return None
 
 
def has_word(text, word):
    """Whole word; "_" and "-" count as separators ("tcp_main", "QA-1")."""
    return re.search(rf"(?<![a-z0-9]){word}(?![a-z0-9])", (text or "").lower()) is not None
 
 
def is_internal_subaccount(name):
    """QA / test subaccounts go to the bottom of every tab."""
    return any(has_word(name, k) for k in INTERNAL_KEYWORDS)
 
 
def number_types(friendly, service_names, phone=""):
    """Column(s) of the Phone Numbers tab this number belongs to."""
    if phone in NUMBER_TYPE_OVERRIDES:
        return list(NUMBER_TYPE_OVERRIDES[phone])
 
    def found(text):
        key = squash(text)
        return [label for label, words in NUMBER_TYPES
                if any(squash(w) in key for w in words)]
 
    types = found(friendly)
    if not types:
        for name in service_names:
            types += [t for t in found(name) if t not in types]
    return types or [OTHER_TYPE]
 
 
def environment(friendly):
    """prod / staging / dev, as written in the name ("Goose Assistant (prod/staging)")."""
    text = (friendly or "").lower()
    envs = [env for env, words in (("prod", ["prod"]), ("staging", ["staging", "stg"]),
                                   ("dev", ["dev"]))
            if any(has_word(text, w) for w in words)]
    return "/".join(envs)
 
 
def classify_main_number(friendly, service_names, subaccounts):
    """
    Returns (category, match_label, next_step, matched_subaccounts).
    match_label is the subaccount name when we know whose number it is.
    """
    texts = [t for t in [friendly] + list(service_names) if t]
    blob = " ".join(texts).lower()
 
    # Manual decisions win over everything else
    friendly_key = squash(friendly)
    for prefix, block, label, next_step in MANUAL_OVERRIDES:
        if friendly_key and friendly_key.startswith(squash(prefix)):
            return block, label, next_step, []
 
    partner = alias_partner(texts)
 
    # Goose Assistant numbers: own block, still showing whose they are
    if GOOSE_KEYWORD in blob:
        if partner:
            sub_name = find_subaccount(partner, subaccounts)
            return (GOOSE, sub_name or f"{partner} (no subaccount)", "",
                    [sub_name] if sub_name else [])
        return GOOSE, GOOSE, "", []
 
    # AgVend's own numbers (TCP, test, QA...) before partner matching
    if any(has_word(blob, k) for k in INTERNAL_KEYWORDS):
        return INTERNAL, INTERNAL, "", []
 
    if partner:
        sub_name = find_subaccount(partner, subaccounts)
        if sub_name:
            return (HAS_SUBACCOUNT, sub_name,
                    "Check: should it move to that subaccount?", [sub_name])
        return (NO_SUBACCOUNT, NO_SUBACCOUNT,
                f"Create a subaccount for {partner}?", [])
 
    if not any(has_letters(t) and name_tokens(t) for t in texts):
        return UNNAMED, UNNAMED, "Give the number a partner name to identify it", []
 
    matches = match_subaccounts(texts, subaccounts)
    if matches:
        return (HAS_SUBACCOUNT, ", ".join(matches),
                "Check: should it move to that subaccount?", matches)
 
    return (NO_SUBACCOUNT, NO_SUBACCOUNT,
            "Check: does this partner need a subaccount?", [])
 
 
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
                 dim_first_col=True, cell_fills=(), muted_rows=(),
                 zebra=False, grid_lines=False, first_col_fill=None):
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
 
    # Static colors for cells whose text varies (e.g. a subaccount name as status)
    for r0, r1, c, bg, fg in cell_fills:
        requests_.append({
            "repeatCell": {
                "range": grid(sid, r0, r1, c, c + 1),
                "cell": {"userEnteredFormat": {
                    "backgroundColor": rgb(bg),
                    "textFormat": {"foregroundColor": rgb(fg), "bold": True}}},
                "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.foregroundColor,"
                          "userEnteredFormat.textFormat.bold",
            }
        })
 
    # Alternating row background, so neighbouring subaccounts don't blend
    if zebra:
        for r in range(2, n_rows, 2):
            requests_.append({
                "repeatCell": {
                    "range": grid(sid, r, r + 1, 0, n_cols),
                    "cell": {"userEnteredFormat": {"backgroundColor": rgb("#F2F5F9")}},
                    "fields": "userEnteredFormat.backgroundColor",
                }
            })
 
    # Name column tinted so it reads as a row label
    if first_col_fill:
        requests_.append({
            "repeatCell": {
                "range": grid(sid, 1, n_rows, 0, 1),
                "cell": {"userEnteredFormat": {"backgroundColor": rgb(first_col_fill)}},
                "fields": "userEnteredFormat.backgroundColor",
            }
        })
 
    # Thin lines between every row and column
    if grid_lines and n_rows > 1:
        line = {"style": "SOLID", "color": rgb("#D5DCE4")}
        requests_.append({
            "updateBorders": {
                "range": data,
                "top": line, "bottom": line, "left": line, "right": line,
                "innerHorizontal": line, "innerVertical": line,
            }
        })
 
    # QA / test rows: grey italic
    for r in muted_rows:
        requests_.append({
            "repeatCell": {
                "range": grid(sid, r, r + 1, 0, n_cols),
                "cell": {"userEnteredFormat": {
                    "backgroundColor": rgb("#F6F7F9"),
                    "textFormat": {"italic": True, "foregroundColor": rgb("#8A94A0")}}},
                "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.italic,"
                          "userEnteredFormat.textFormat.foregroundColor",
            }
        })
 
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
 
 
def build_rows(header, groups_data, rank, summary, band_prefix="Subaccount",
               internal_last=True):
    """
    Problems first: sort rows inside each subaccount, then subaccounts by their
    most urgent row. Each subaccount starts with a band row: name | SID · summary.
 
    groups_data: list of (subaccount_name, subaccount_sid, rows)
    Returns rows and [(band_row, first_data_row, end_row)].
    """
    for _, _, sub_rows in groups_data:
        sub_rows.sort(key=lambda r: (rank(r), str(r[1])))
 
    groups_data.sort(key=lambda g: (
        internal_last and is_internal_subaccount(g[0]),
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
 
    # Subaccount Campaigns tab: (subaccount_name, sid, [rows])
    campaign_groups = []
 
    # Phone Numbers tab: one dict per subaccount
    #   {"name", "sid", "total", "types": {column: [cell text]}, "no_access"}
    number_pivot = []
 
    subaccounts = get_subaccounts()
 
    for sub in subaccounts:
        sub_sid = sub.get("sid", "—")
        sub_name = sub.get("friendly_name", "—")
        sub_rows = []
        pivot = {"name": sub_name, "sid": sub_sid, "total": 0,
                 "types": {col: [] for col in TYPE_COLUMNS}, "no_access": False}
 
        def add_row(service, use_case, status, error="", reason="",
                    numbers="—", brand="—"):
            sub_rows.append([sub_name, service, use_case, status,
                             error, reason, numbers, brand, run_at])
 
        markdown.append(f"## {sub_name}")
        markdown.append("")
 
        sub_token = get_subaccount_auth_token(sub_sid)
 
        if not sub_token:
            add_row("—", "—", NO_TOKEN, reason="Could not fetch subaccount auth token")
            pivot["no_access"] = True
            markdown.append("- Could not fetch subaccount auth token")
            markdown.append("")
            campaign_groups.append((sub_name, sub_sid, sub_rows))
            number_pivot.append(pivot)
            continue
 
        # Phone Numbers > Inventory
        inventory = get_incoming_numbers(sub_sid, sub_token)
 
        # number SID -> [service names]
        number_links = {}
 
        services = get_services(sub_sid, sub_token)
 
        if not services:
            add_row("—", "—", NO_SERVICES, reason="No Messaging Services found")
            markdown.append("- No Messaging Services found")
 
        for svc in services:
            service_sid = svc.get("sid", "—")
            service_name = svc.get("friendly_name", "—")
 
            campaigns = get_campaigns(sub_sid, sub_token, service_sid)
            service_numbers = get_service_numbers(sub_sid, sub_token, service_sid)
 
            for n in service_numbers:
                number_links.setdefault(n.get("sid"), []).append(service_name)
 
            count_cell = plural(len(service_numbers), "number") if service_numbers else "—"
 
            if not campaigns:
                add_row(service_name, "—", NO_CAMPAIGN,
                        reason="No A2P Campaign found", numbers=count_cell)
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
 
                # Approved campaign, no numbers yet: time to move numbers in
                cell = READY_TO_MOVE if status == "VERIFIED" and not service_numbers \
                    else count_cell
 
                add_row(service_name, use_case, status, error_code, reason,
                        cell, brand_sid)
 
                markdown.append(f"- {service_name} | {use_case} | {status}")
 
        # Phone Numbers tab: put each number into its type column(s)
        for num in inventory:
            friendly = num.get("friendly_name") or ""
            linked = number_links.get(num.get("sid"), [])
 
            text = num.get("phone_number", "")
            notes = []
            types = number_types(friendly, linked, num.get("phone_number", ""))
            if "Goose" in types and environment(friendly):
                notes.append(environment(friendly))
            if not linked:
                notes.append(NOT_IN_SERVICE_MARK)
            if notes:
                text += "  · " + " · ".join(notes)
 
            for t in types:
                pivot["types"][t].append(text)
            pivot["total"] += 1
 
            markdown.append(f"- {num.get('phone_number')} | {', '.join(types)} | "
                            f"{', '.join(linked) or NOT_IN_SERVICE_MARK}")
 
        markdown.append("")
        campaign_groups.append((sub_name, sub_sid, sub_rows))
        number_pivot.append(pivot)
 
    # ---- Main AgVend account: numbers not moved to a subaccount yet ----
    all_subaccounts = get_subaccounts(apply_filter=False)
    main_buckets = {status: [] for status in MATCH_GROUPS}
    pivot_by_name = {p["name"]: p for p in number_pivot}
 
    main_links = {}  # number SID -> [service names]
    for svc in get_services(MASTER_SID, MASTER_TOKEN):
        for n in get_service_numbers(MASTER_SID, MASTER_TOKEN, svc.get("sid")):
            main_links.setdefault(n.get("sid"), []).append(svc.get("friendly_name", "—"))
 
    markdown += ["## Main account numbers", ""]
 
    main_category = {}  # number SID -> category, for sorting
 
    for num in get_incoming_numbers(MASTER_SID, MASTER_TOKEN):
        friendly = num.get("friendly_name") or ""
        services = main_links.get(num.get("sid"), [])
        status, matched, next_step, subs = classify_main_number(
            friendly, services, all_subaccounts)
        main_category[num.get("sid", "—")] = status
 
        types = number_types(friendly, services, num.get("phone_number", ""))
 
        # Phone Numbers tab: show this number in its partner's row, marked "on main".
        # Partners from PARTNER_ALIASES without a subaccount get a row of their own.
        targets = list(subs)
        if not targets and status in (NO_SUBACCOUNT, GOOSE):
            partner = alias_partner([t for t in [friendly] + services if t])
            if not partner and status == NO_SUBACCOUNT:
                partner = guess_partner_name(friendly)
            if partner:
                targets = [partner]
                if partner not in pivot_by_name:
                    pivot_by_name[partner] = {
                        "name": partner, "sid": "—", "total": NO_SUBACCOUNT,
                        "types": {col: [] for col in TYPE_COLUMNS}, "no_access": False,
                    }
                    number_pivot.append(pivot_by_name[partner])
 
        notes = []
        if "Goose" in types and environment(friendly):
            notes.append(environment(friendly))
        notes.append(ON_MAIN_MARK)
        cell_text = num.get("phone_number", "") + "  · " + " · ".join(notes)
 
        for name in targets:
            if name in pivot_by_name:  # (skipped when the run is filtered to one account)
                for t in types:
                    pivot_by_name[name]["types"][t].append(cell_text)
        type_cell = ", ".join(types)
        if "Goose" in types and environment(friendly):
            type_cell += f" ({environment(friendly)})"
 
        main_buckets[status].append([
            as_text(num.get("phone_number", "")),
            friendly or "—",
            type_cell,
            ", ".join(services) or "—",
            matched,
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
        # by category, then partner name so one partner's numbers sit together
        rank=lambda r: (status_rank(main_category.get(r[M_COL_SID])),
                        r[M_COL_STATUS].lower()),
        summary=lambda rs: plural(len(rs), "phone number"),
        band_prefix="Main account",
        internal_last=False,
    )
 
    category_by_label = {label: cat for cat, label in MATCH_GROUPS.items()}
    category_colors = {cat: (bg, fg) for cat, bg, fg in STATUS_STYLES
                       if cat in MATCH_GROUPS}
    main_fills = []
    for band, start, end in main_groups:
        cat = category_by_label.get(main_rows[band][0])
        if cat and end > start:
            bg, fg = category_colors[cat]
            main_fills.append((start, end, M_COL_STATUS, bg, fg))
 
    # ---- Subaccount Campaigns rows ----
    rows, groups = build_rows(
        HEADER, campaign_groups,
        rank=lambda r: status_rank(r[COL_STATUS]),
        summary=lambda rs: plural(len({r[1] for r in rs if r[1] != "—"}),
                                  "messaging service"),
    )
 
    # ---- Phone Numbers rows: one per subaccount ----
    # Partners that still need a subaccount first, then A-Z, QA/test last
    number_pivot.sort(key=lambda p: (is_internal_subaccount(p["name"]),
                                     p["total"] != NO_SUBACCOUNT,
                                     p["name"].lower()))
 
    num_rows = [NUMBERS_HEADER]
    muted = []
 
    for p in number_pivot:
        if is_internal_subaccount(p["name"]):
            muted.append(len(num_rows))
 
        num_rows.append(
            [p["name"], "no access" if p["no_access"] else p["total"]]
            + [as_text("\n".join(p["types"][col])) if p["types"][col] else ""
               for col in TYPE_COLUMNS]
            + [p["sid"], run_at]
        )
 
    ready_bg, ready_fg = READY_STYLE
    ready_format = {"backgroundColor": rgb(ready_bg),
                    "textFormat": {"foregroundColor": rgb(ready_fg), "bold": True}}
 
    spreadsheet = get_sheet()
 
    ws = ensure_worksheet(spreadsheet, SHEET_TITLE)
    replace_sheet(ws, rows)
    format_sheet(
        spreadsheet, ws, len(rows), groups,
        header=HEADER,
        widths=COLUMN_WIDTHS,
        status_col=COL_STATUS,
        tab_color=tab_color_for({r[COL_STATUS] for r in rows[1:]}),
        center_cols=[COL_ERROR, COL_NUMBERS],
        wrap_cols=[COL_REASON],
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
 
    type_cols = (N_COL_FIRST_TYPE, N_COL_LAST_TYPE + 1)
    ws_numbers = ensure_worksheet(spreadsheet, NUMBERS_SHEET_TITLE)
    replace_sheet(ws_numbers, num_rows)
    pivot_text = [str(c) for r in num_rows[1:] for c in r]
    format_sheet(
        spreadsheet, ws_numbers, len(num_rows), [],
        header=NUMBERS_HEADER,
        widths=NUMBERS_COLUMN_WIDTHS,
        status_col=N_COL_TOTAL,
        tab_color=("#D64545" if NO_SUBACCOUNT in pivot_text
                   else "#E8A33D" if any(ON_MAIN_MARK in c or NOT_IN_SERVICE_MARK in c
                                         for c in pivot_text)
                   else "#2E9E5B"),
        wrap_cols=list(range(*type_cols)),
        mono_cols=list(range(*type_cols)),
        bold_cols=[0],
        muted_from_col=N_COL_SID,
        muted_rows=muted,
        zebra=True,
        grid_lines=True,
        first_col_fill="#E6ECF3",
        extra_rules=[
            {   # still on the main account: needs moving (or a subaccount first)
                "cols": type_cols,
                "booleanRule": {
                    "condition": {"type": "TEXT_CONTAINS",
                                  "values": [{"userEnteredValue": ON_MAIN_MARK}]},
                    "format": {"backgroundColor": rgb("#FFF1C2"),
                               "textFormat": {"foregroundColor": rgb("#8A6100"),
                                              "bold": True}},
                },
            },
            {   # number not attached to any Messaging Service
                "cols": type_cols,
                "booleanRule": {
                    "condition": {"type": "TEXT_CONTAINS",
                                  "values": [{"userEnteredValue": NOT_IN_SERVICE_MARK}]},
                    "format": {"backgroundColor": rgb("#FFE3CC"),
                               "textFormat": {"foregroundColor": rgb("#9C4A00")}},
                },
            },
            {   # subaccount without any numbers
                "cols": (N_COL_TOTAL, N_COL_TOTAL + 1),
                "booleanRule": {
                    "condition": {"type": "NUMBER_EQ",
                                  "values": [{"userEnteredValue": "0"}]},
                    "format": {"textFormat": {"foregroundColor": rgb("#B0B8C1")}},
                },
            },
        ],
    )
 
    ws_main = ensure_worksheet(spreadsheet, MAIN_SHEET_TITLE)
    replace_sheet(ws_main, main_rows if len(main_rows) > 1
                  else main_rows + [["No phone numbers on the main account"]
                                    + [""] * (len(MAIN_HEADER) - 1)])
    main_statuses = set(main_category.values())
    format_sheet(
        spreadsheet, ws_main, max(len(main_rows), 2), main_groups,
        header=MAIN_HEADER,
        widths=MAIN_COLUMN_WIDTHS,
        status_col=M_COL_STATUS,
        tab_color=("#D64545" if NO_SUBACCOUNT in main_statuses
                   else "#E8A33D" if HAS_SUBACCOUNT in main_statuses
                   else "#2E9E5B"),
        wrap_cols=[M_COL_SERVICE, M_COL_STATUS],
        cell_fills=main_fills,
        bold_cols=[M_COL_NEXT],
        muted_from_col=M_COL_SID,
        dim_first_col=False,
    )
 
    with open("result.md", "w") as f:
        f.write("\n".join(markdown))
 
    print("🎉 Done!")
 
 
if __name__ == "__main__":
    main()
