#!/usr/bin/env python3
"""
fetch_newsletter_emails.py
Fetches emails from the newsletter sender list for the past 7 days
and prints their subject + body text for digest generation.
"""

import urllib.request
import os
import json
import base64
import re
import sys
from datetime import datetime, timedelta, timezone

MATON_API_KEY = os.environ.get("MATON_API_KEY", "")
BASE_URL = "https://gateway.maton.ai/google-mail/gmail/v1/users/me"

# Full sender list (mix of exact addresses and domain matches)
SENDERS_EXACT = [
    "404-media@ghost.io",
    "noreply@medium.com",
    "hello@snipd.com",
    "nobody@feedspot.com",
    "noreply@ip2location.com",
    "info@designrush.co",
    "jon@charm-offensive.co.uk",
    "info@allisland.media",
    "info@digital.je",
    "info@newsletter.theweek.co.uk",
    "growthmemo@substack.com",
    "ben@speero.com",
]

SENDER_DOMAINS = [
    "crazygraph-global.com",
    "seostuff.com",
    "wesmcdowell.com",
    "email.mckinsey.com",
    "flexos.work",
    "opensourceceo.com",
    "mail.profgalloway.com",
    "aimarketers.co",
    "thefutur.com",
    "daily.therundown.ai",
    "agencyhackers.co.uk",
    "conversion-rate-experts.com",
]


def gmail_get(path, params=None, extra_qs=""):
    url = f"{BASE_URL}/{path}"
    if params:
        query = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
        url = f"{url}?{query}"
        if extra_qs:
            url = f"{url}&{extra_qs}"
    elif extra_qs:
        url = f"{url}?{extra_qs}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {MATON_API_KEY}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except Exception as e:
        print(f"[ERROR] {path}: {e}", file=sys.stderr)
        return {}


import urllib.parse


def build_query():
    """Build a Gmail search query for the past 7 days from all senders."""
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y/%m/%d")
    
    from_parts = [f"from:{s}" for s in SENDERS_EXACT]
    from_parts += [f"from:@{d}" for d in SENDER_DOMAINS]
    from_clause = " OR ".join(from_parts)
    
    return f"({from_clause}) after:{since}"


def decode_body(payload):
    """Recursively extract plain text from message payload."""
    mime = payload.get("mimeType", "")
    
    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            try:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            except Exception:
                return ""
    
    if mime == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            try:
                html = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                # Strip HTML tags
                text = re.sub(r"<[^>]+>", " ", html)
                text = re.sub(r"\s+", " ", text).strip()
                return text
            except Exception:
                return ""
    
    # Multipart: recurse
    parts = payload.get("parts", [])
    texts = []
    for part in parts:
        t = decode_body(part)
        if t:
            texts.append(t)
    # Prefer plain text if both exist
    return texts[0] if texts else ""


def get_header(headers, name):
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def fetch_emails():
    query = build_query()
    print(f"[INFO] Gmail query: {query}", file=sys.stderr)
    
    result = gmail_get("messages", {"q": query, "maxResults": "50"})
    messages = result.get("messages", [])
    
    if not messages:
        print("[INFO] No newsletter emails found in the past 7 days.", file=sys.stderr)
        return []
    
    # Cap at 20 to keep runtime reasonable
    messages = messages[:20]
    print(f"[INFO] Fetching full content for {len(messages)} messages.", file=sys.stderr)

    emails = []
    seen_subjects = set()
    for msg in messages:
        mid = msg["id"]
        full = gmail_get(f"messages/{mid}")
        payload = full.get("payload", {})
        headers = payload.get("headers", [])

        subject = get_header(headers, "Subject")
        sender  = get_header(headers, "From")
        date    = get_header(headers, "Date")

        # Skip duplicate subjects (e.g. threading)
        key = subject[:50].lower().strip()
        if key in seen_subjects:
            continue
        seen_subjects.add(key)

        body = decode_body(payload)

        # Truncate body to ~1500 chars to keep total context manageable
        if len(body) > 1500:
            body = body[:1500] + "... [truncated]"

        if body.strip():
            emails.append({
                "subject": subject,
                "from": sender,
                "date": date,
                "body": body.strip(),
            })

    return emails


if __name__ == "__main__":
    emails = fetch_emails()
    print(json.dumps(emails, indent=2, ensure_ascii=False))
