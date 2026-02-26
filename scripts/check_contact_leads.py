#!/usr/bin/env python3
"""
Check Gmail for contact form emails with "Form Type: I need some help",
classify each one, and post HIGH_VALUE / QUALIFIED leads to Slack #salesfunnel.

Uses email_classifier.py for scoring. Only "ALERT_AND_LOG" verdicts are posted.
Tracks posted message IDs to avoid duplicates.
"""

import sys
import os
import re
import json
import urllib.request
import urllib.parse
import base64
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────────
MATON_API_KEY   = os.environ.get("MATON_API_KEY", "")
OPENCLAW_CONFIG = Path.home() / ".openclaw/openclaw.json"
STATE_FILE      = Path(__file__).parent / "contact_leads_seen.json"
SLACK_CHANNEL   = "salesfunnel"
GMAIL_QUERY     = 'label:contact "Form Type: I need some help"'
MAX_RESULTS     = 10

# ── Classifier ──────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from email_classifier import classify_email

VERDICT_EMOJI = {
    "HIGH_VALUE": ":fire:",
    "QUALIFIED":  ":star:",
    "MONITOR":    ":eyes:",
    "SPAM":       ":no_entry:",
    "INTERNAL":   ":house:",
}

# ── Helpers ─────────────────────────────────────────────────────────────────────

def get_slack_token():
    cfg = json.loads(OPENCLAW_CONFIG.read_text())
    return cfg["channels"]["slack"]["botToken"]


def gmail_get(path, params=None):
    base = "https://gateway.maton.ai/google-mail/gmail/v1/users/me"
    url  = f"{base}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {MATON_API_KEY}")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def decode_body(payload) -> str:
    """Recursively extract plain-text body from a Gmail message payload."""
    import html as html_module
    mime = payload.get("mimeType", "")
    data = payload.get("body", {}).get("data", "")

    if data and mime in ("text/plain", "text/html"):
        raw = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        if mime == "text/html":
            raw = re.sub(r'<br\s*/?>', '\n', raw, flags=re.IGNORECASE)
            raw = re.sub(r'<[^>]+>', '', raw)
            raw = html_module.unescape(raw)
        return raw

    for part in payload.get("parts", []):
        result = decode_body(part)
        if result:
            return result
    return ""


def slack_post(token: str, channel: str, text: str):
    url     = "https://slack.com/api/chat.postMessage"
    payload = json.dumps({"channel": channel, "text": text}).encode()
    req     = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as r:
        resp = json.load(r)
    if not resp.get("ok"):
        raise RuntimeError(f"Slack error: {resp.get('error')}")
    return resp


def load_seen() -> set:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()).get("seen", []))
    return set()


def save_seen(seen: set):
    STATE_FILE.write_text(json.dumps({"seen": list(seen)}, indent=2))


def build_slack_message(r: dict, subject: str, date: str) -> str:
    p      = r["parsed"]
    emoji  = VERDICT_EMOJI.get(r["verdict"], ":envelope:")
    name   = p["name"] or "Unknown"
    company = f", {p['company']}" if p["company"] else ""
    phone   = f" | {p['phone']}" if p["phone"] else ""
    email   = f" | {p['email']}" if p["email"] else ""

    return (
        f"{emoji} *{name}{company}*{email}{phone}\n"
        f"{r['summary']}"
    )


# ── Main ─────────────────────────────────────────────────────────────────────────

def main():
    if not MATON_API_KEY:
        print("ERROR: MATON_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    slack_token = get_slack_token()
    seen        = load_seen()

    resp     = gmail_get("/messages", {"maxResults": MAX_RESULTS, "q": GMAIL_QUERY})
    messages = resp.get("messages", [])

    if not messages:
        print("No matching emails found.")
        return

    posted = skipped = ignored = 0

    for msg in messages:
        msg_id = msg["id"]
        if msg_id in seen:
            continue

        detail  = gmail_get(f"/messages/{msg_id}", {"format": "full"})
        headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
        subject = headers.get("Subject", "(no subject)")
        sender  = headers.get("From", "")
        date    = headers.get("Date", "")
        body    = decode_body(detail["payload"])

        # Skip replies and forwards — we only want original form submissions
        if re.match(r"^\s*(re|fwd|fw)\s*:", subject, re.IGNORECASE):
            seen.add(msg_id)
            skipped += 1
            print(f"[SKIP      ] {subject[:60]} (reply/forward)")
            continue

        # For LWDA relay emails, replace sender with the customer's parsed email
        # so the classifier uses the enquirer's domain, not the relay domain
        customer_email_match = re.search(r"Email Address\s*[:\*]?\s*\n?(.+?)(?:\n|$)", body, re.IGNORECASE)
        customer_email = customer_email_match.group(1).strip() if customer_email_match else ""
        classify_sender = f"<{customer_email}>" if customer_email else sender

        result  = classify_email({"subject": subject, "sender": classify_sender, "body": body})
        verdict = result["verdict"]
        action  = result["action"]

        print(f"[{verdict:10s}] {subject[:60]} | score={result['score']}")

        # Always mark as seen so we don't re-classify on the next run
        seen.add(msg_id)

        if action == "ALERT_AND_LOG":
            text = build_slack_message(result, subject, date)
            slack_post(slack_token, SLACK_CHANNEL, text)
            posted += 1
        elif action == "LOG_ONLY":
            # Future: write to Notion. For now just log.
            ignored += 1
        else:
            # DISCARD or IGNORE
            skipped += 1

    save_seen(seen)
    print(f"\nDone. {posted} posted · {ignored} monitor/log · {skipped} skipped.")


if __name__ == "__main__":
    main()
