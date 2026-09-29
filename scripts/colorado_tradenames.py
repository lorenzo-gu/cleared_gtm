#!/usr/bin/env python3
"""Pull Colorado trade names that look like restaurants/bars and email them as a CSV.

Source: Colorado Information Marketplace, "Trade Names" dataset (Socrata).
First run (no CSV yet): pulls the last FIRST_RUN_DAYS days.
Later runs: pulls the last DAILY_LOOKBACK_DAYS days, appends only rows whose
masterTradenameId is not already in the CSV, and emails the full CSV.

Stdlib only. Configuration via environment variables (see README).
"""

import csv
import json
import os
import re
import smtplib
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

BASE_URL = "https://data.colorado.gov"
DATASET_IDS = [d for d in os.getenv("DATASET_ID", "u7sb-g482,c48n-6dwv").split(",") if d]
CSV_PATH = Path(os.getenv("CSV_PATH", "data/colorado_restaurant_tradenames.csv"))
FIRST_RUN_DAYS = int(os.getenv("FIRST_RUN_DAYS", "183"))
DAILY_LOOKBACK_DAYS = int(os.getenv("DAILY_LOOKBACK_DAYS", "7"))
APP_TOKEN = os.getenv("SOCRATA_APP_TOKEN", "")
PAGE_SIZE = 50000

DATE_FIELD = "effectivedate"
ID_FIELD = "mastertradenameid"
MATCH_FIELDS = ["tradenamedescription", "registrantorganization"]
EXTRA_COLUMN = "matchedKeywords"

# Word-boundary matches, optional plural "s". Loose on purpose.
KEYWORDS = [
    "restaurant", "kitchen", "eatery", "eats", "dining", "diner", "cafe", "café",
    "coffee", "espresso", "bistro", "brasserie", "trattoria", "osteria", "cantina",
    "taqueria", "pizzeria", "pizza", "grill", "grille", "bbq", "barbecue", "bar-b-q",
    "smokehouse", "steakhouse", "chophouse", "sushi", "ramen", "noodle", "pho",
    "taco", "burrito", "tamale", "empanada", "burger", "wings", "sandwich", "deli",
    "bagel", "bakery", "donut", "doughnut", "creamery", "ice cream", "gelato",
    "crepe", "boba", "tea house", "teahouse", "juice", "smoothie", "food",
    "food truck", "foods", "cuisine", "catering", "caterer", "canteen", "bar",
    "pub", "gastropub", "tavern", "saloon", "lounge", "taproom", "tap room",
    "taphouse", "tap house", "brewery", "brewing", "brewpub", "beer", "biergarten",
    "beer garden", "winery", "wine bar", "distillery", "cocktail", "speakeasy",
    "drink", "drinks", "drinking", "spirits", "cidery", "meadery",
]
KEYWORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(KEYWORDS, key=len, reverse=True)) + r")s?\b",
    re.IGNORECASE,
)


def http_get_json(url):
    headers = {"Accept": "application/json"}
    if APP_TOKEN:
        headers["X-App-Token"] = APP_TOKEN
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)


def dataset_columns(dataset_id):
    """Return [(fieldName, displayName)] in dataset order, or None if metadata is unavailable."""
    try:
        meta = http_get_json(f"{BASE_URL}/api/views/{dataset_id}.json")
    except urllib.error.URLError as e:
        print(f"warning: metadata for {dataset_id} unavailable: {e}", file=sys.stderr)
        return None
    cols = [c for c in meta.get("columns", []) if not c.get("fieldName", "").startswith(":")]
    cols.sort(key=lambda c: c.get("position", 0))
    return [(c["fieldName"], c.get("name") or c["fieldName"]) for c in cols]


def fetch_since(dataset_id, since):
    """Fetch every row with effectiveDate >= since, paging through the SODA API."""
    rows, offset = [], 0
    where = f"{DATE_FIELD} >= '{since.strftime('%Y-%m-%dT00:00:00')}'"
    while True:
        params = urllib.parse.urlencode({
            "$where": where,
            "$order": ":id",
            "$limit": PAGE_SIZE,
            "$offset": offset,
        })
        page = http_get_json(f"{BASE_URL}/resource/{dataset_id}.json?{params}")
        rows.extend(page)
        print(f"  fetched {len(page)} rows (offset {offset})")
        if len(page) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE


def matched_keywords(row):
    text = " ".join(str(row.get(f, "")) for f in MATCH_FIELDS)
    found = {m.group(1).lower() for m in KEYWORD_RE.finditer(text)}
    return ", ".join(sorted(found))


def load_existing():
    if not CSV_PATH.exists():
        return None, []
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def send_email(subject, body, attachment):
    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    if not (host and user and password):
        print("SMTP_HOST/SMTP_USER/SMTP_PASSWORD not set; skipping email.")
        return
    msg = EmailMessage()
    msg["From"] = os.getenv("EMAIL_FROM") or user
    msg["To"] = os.getenv("EMAIL_TO") or "lorenzo@clearedtoopen.com"
    msg["Subject"] = subject
    msg.set_content(body)
    msg.add_attachment(attachment.read_bytes(), maintype="text", subtype="csv", filename=attachment.name)
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT") or "587"), timeout=60) as s:
        s.starttls()
        s.login(user, password)
        s.send_message(msg)
    print(f"Emailed {attachment} to {msg['To']}")


def main():
    header, existing = load_existing()
    first_run = header is None
    days = FIRST_RUN_DAYS if first_run else DAILY_LOOKBACK_DAYS
    today = datetime.now(timezone.utc).date()
    since = today - timedelta(days=days)

    rows, columns, used_id, errors = None, None, None, []
    for dataset_id in DATASET_IDS:
        print(f"Querying dataset {dataset_id} for {DATE_FIELD} >= {since}")
        try:
            rows = fetch_since(dataset_id, since)
        except urllib.error.HTTPError as e:
            errors.append(f"{dataset_id}: HTTP {e.code} {e.read()[:300]!r}")
            continue
        columns = dataset_columns(dataset_id)
        used_id = dataset_id
        break
    if rows is None:
        sys.exit("Could not query any dataset:\n" + "\n".join(errors))

    matches = []
    for row in rows:
        kw = matched_keywords(row)
        if kw:
            row[EXTRA_COLUMN] = kw
            matches.append(row)
    print(f"{len(rows)} trade names since {since}; {len(matches)} look like food/drink businesses.")

    # Map API field names to the dataset's display names (masterTradenameId, Entity ID, ...).
    if not columns:
        seen = []
        for r in rows:
            seen += [k for k in r if k not in seen and k != EXTRA_COLUMN]
        columns = [(k, k) for k in seen]
    if first_run:
        header = [name for _, name in columns] + [EXTRA_COLUMN]
    field_to_name = dict(columns)
    field_to_name[EXTRA_COLUMN] = EXTRA_COLUMN

    id_col = field_to_name.get(ID_FIELD, ID_FIELD)
    known_ids = {r.get(id_col) for r in existing}
    new_rows = []
    for r in sorted(matches, key=lambda r: (r.get(DATE_FIELD, ""), r.get(ID_FIELD, ""))):
        out = {field_to_name.get(k, k): v for k, v in r.items()}
        if out.get(id_col) in known_ids:
            continue
        known_ids.add(out.get(id_col))
        new_rows.append(out)

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("a" if not first_run else "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        if first_run:
            writer.writeheader()
        writer.writerows(new_rows)
    total = len(existing) + len(new_rows)
    print(f"Appended {len(new_rows)} new rows to {CSV_PATH} (total {total}).")

    window = f"last {days} days" if first_run else f"since the previous run (checked the last {days} days)"
    body = (
        f"Colorado trade names that look like restaurants, bars or other food/drink businesses.\n\n"
        f"New rows {window}: {len(new_rows)}\n"
        f"Total rows in the attached CSV: {total}\n"
        f"Source: {BASE_URL}/resource/{used_id} (filtered on {DATE_FIELD}, "
        f"keywords matched in tradenameDescription or registrantOrganization)\n"
    )
    if new_rows:
        body += "\nNew rows:\n" + "\n".join(
            f"- {r.get(field_to_name.get('tradenamedescription', 'tradenamedescription'), '')} "
            f"({r.get(field_to_name.get('city', 'city'), '')}) [{r.get(EXTRA_COLUMN, '')}]"
            for r in new_rows[:200]
        )
    subject = f"Colorado restaurant trade names {today}: {len(new_rows)} new"
    send_email(subject, body, CSV_PATH)


if __name__ == "__main__":
    main()
