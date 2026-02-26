"""
email_classifier.py
===================
Standalone email classification module for the Devstars AI agent.
No external dependencies. Drop this anywhere and import classify_email().

Quick start
-----------
    from email_classifier import classify_email

    result = classify_email({
        "subject": "[LWDA] Contact Form - Iram Yasin",
        "sender":  "LWDA <info@londonwebdesignagency.com>",
        "body":    "...raw email body...",
    })

    result["verdict"]     # "HIGH_VALUE" | "QUALIFIED" | "MONITOR" | "SPAM" | "INTERNAL"
    result["score"]       # integer (negative = spam)
    result["action"]      # "ALERT_AND_LOG" | "LOG_ONLY" | "IGNORE" | "DISCARD"
    result["summary"]     # one-line explanation for the agent to reason over
    result["top_signals"] # list of top 5 matched rules
    result["parsed"]      # extracted fields: name, email, phone, company, message
    result["confident"]   # True when score is well above/below a threshold boundary

Agent usage
-----------
Use result["action"] to decide what tool to call next:
  "ALERT_AND_LOG" → send Slack alert + write Notion page
  "LOG_ONLY"      → write Notion page, no Slack
  "DISCARD"       → mark as read and archive, do nothing else
  "IGNORE"        → internal/test email, skip silently

Example agent pseudo-code:
    result = classify_email(email)
    if result["action"] == "ALERT_AND_LOG":
        send_slack(result)
        write_notion(result)
    elif result["action"] == "LOG_ONLY":
        write_notion(result)

Tuning
------
All scoring rules live in the RULES dict at the bottom of this file.
Adjust point values or add new phrases there — no logic changes needed.
Change sensitivity via the THRESHOLDS dict (also at the bottom).

Self-test
---------
Run directly to validate all rules against known emails from your inbox:
    python email_classifier.py
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ============================================================
# PUBLIC API
# ============================================================

def classify_email(email: dict[str, str]) -> dict[str, Any]:
    """
    Classify a single email. Returns a structured verdict dict.

    Parameters
    ----------
    email : dict
        subject : str — email subject line
        sender  : str — From header (e.g. "Name <email@domain.com>")
        body    : str — plain-text body

    Returns
    -------
    dict — see module docstring for all keys
    """
    raw    = _RawEmail(email.get("subject",""), email.get("sender",""), email.get("body",""))
    parsed = _parse_fields(raw)
    scored = _score(raw, parsed)
    verdict = _verdict(scored)
    return _build_output(parsed, scored, verdict)


def batch_classify(emails: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Classify a list of emails. Returns sorted by score, highest first."""
    return sorted([classify_email(e) for e in emails], key=lambda r: r["score"], reverse=True)


# ============================================================
# INTERNAL DATA STRUCTURES
# ============================================================

@dataclass
class _RawEmail:
    subject: str
    sender:  str
    body:    str

    @property
    def text(self) -> str:
        """Single lowercase string used for all pattern matching."""
        return (self.subject + " " + self.sender + " " + self.body).lower()


@dataclass
class _Parsed:
    first_name:    str = ""
    last_name:     str = ""
    email:         str = ""
    phone:         str = ""
    company:       str = ""
    form_type:     str = ""
    message:       str = ""
    source:        str = ""
    sender_domain: str = ""


@dataclass
class _Scored:
    score:         int  = 0
    disqualified:  bool = False
    dq_reason:     str  = ""
    signals:       list = field(default_factory=list)

    def hit(self, rule: str, points: int, matched: str = ""):
        self.score += points
        self.signals.append({"rule": rule, "points": points, "matched": matched})


# ============================================================
# PARSING
# ============================================================

def _parse_fields(raw: _RawEmail) -> _Parsed:
    p = _Parsed()

    def extract(label: str) -> str:
        m = re.search(
            rf"{re.escape(label)}\s*[:\*]?\s*\n?(.*?)(?=\n[A-Z][a-z]|\Z)",
            raw.body,
            re.IGNORECASE | re.DOTALL
        )
        return m.group(1).strip() if m else ""

    p.first_name = extract("First Name")
    p.last_name  = extract("Last Name")
    p.email      = extract("Email Address")
    p.phone      = extract("Phone")
    p.company    = extract("Company Name")
    p.form_type  = extract("Form Type")
    p.message    = extract("Message")

    sl = raw.subject.lower() + raw.sender.lower()
    p.source = "lwda" if "lwda" in sl else ("devstars" if "devstars" in sl else "unknown")

    # Resolve sender domain from parsed email field, or fall back to From header
    addr = p.email or re.sub(r".*<(.+?)>.*", r"\1", raw.sender)
    p.sender_domain = addr.split("@")[-1].lower() if "@" in addr else ""

    return p


# ============================================================
# SCORING ENGINE
# ============================================================

def _score(raw: _RawEmail, p: _Parsed) -> _Scored:
    s    = _Scored()
    text = raw.text

    # ── Stage 1: Hard disqualifiers ────────────────────────────────────────────
    if p.sender_domain in RULES["internal_domains"]:
        s.disqualified = True
        s.dq_reason    = "Own-domain / internal submission"
        return s

    for phrase in RULES["internal_test_phrases"]:
        if phrase in text:
            s.disqualified = True
            s.dq_reason    = f"Test message detected: '{phrase}'"
            return s

    if re.search(r"donotreply@jam\.co\.uk", raw.sender, re.I) or "silent call" in text:
        s.disqualified = True
        s.dq_reason    = "Internal: phone answering service relay (pureJAM)"
        return s

    # ── Stage 2: Negative signals ───────────────────────────────────────────────
    if p.form_type.lower().strip() == RULES["vendor_form_type"]:
        s.hit("Vendor pitch form type (buyer may have selected wrong option)", -15, p.form_type)

    for phrase in RULES["spam_phrases"]:
        if phrase in text:
            s.hit("Spam phrase", -20, phrase)
            break  # one hit registers the category; no stacking same penalty type

    for pattern in RULES["spam_email_patterns"]:
        if re.search(pattern, p.sender_domain):
            s.hit("Suspect email domain", -10, p.sender_domain)
            break

    for pattern in RULES["spam_phone_patterns"]:
        if re.search(pattern, p.phone):
            s.hit("Suspect phone prefix", -15, p.phone)
            break

    for company in RULES["known_spam_senders"]:
        if company in text:
            s.hit("Known spam sender", -25, company)
            break

    if s.score <= -25:
        s.disqualified = True
        s.dq_reason    = "Spam / vendor solicitation"
        return s

    # ── Stage 3: Positive signals ───────────────────────────────────────────────

    # Professional email domain (not a free provider)
    if p.sender_domain and p.sender_domain not in RULES["free_email_domains"]:
        pts = RULES["professional_email_bonus"]
        s.hit("Professional email domain", pts, p.sender_domain)

    # Message length — genuine enquirers write more
    msg_len = len(p.message)
    for threshold, pts in RULES["message_length_tiers"]:
        if msg_len >= threshold:
            s.hit("Message length", pts, f"{msg_len} chars")
            break

    # High-value project and intent phrases
    for phrase, pts in RULES["high_value_phrases"]:
        if phrase in text:
            s.hit("High-value phrase", pts, phrase)

    # Strong vertical fit for Devstars
    for phrase, pts in RULES["vertical_phrases"]:
        if phrase in text:
            s.hit("Vertical fit", pts, phrase)

    # Seniority / authority of the enquirer
    for phrase, pts in RULES["seniority_phrases"]:
        if phrase in text:
            s.hit("Seniority signal", pts, phrase)
            break  # one seniority signal per email is enough

    # Budget authority language
    for phrase, pts in RULES["budget_authority_phrases"]:
        if phrase in text:
            s.hit("Budget authority language", pts, phrase)
            break

    # Multi-brand signals — strong indicator of a large account
    for phrase, pts in RULES["multi_brand_phrases"]:
        if phrase in text:
            s.hit("Multi-brand / large account", pts, phrase)
            break

    # Small bonus signals
    if p.phone and len(p.phone.strip()) >= 8:
        s.hit("Phone number provided", 5, p.phone)
    if p.company and len(p.company.strip()) > 2:
        s.hit("Company named", 5, p.company)

    return s


# ============================================================
# VERDICT
# ============================================================

def _verdict(s: _Scored) -> str:
    if s.disqualified:
        lower = s.dq_reason.lower()
        return "INTERNAL" if ("internal" in lower or "test" in lower or "own-domain" in lower) else "SPAM"
    if s.score >= THRESHOLDS["high_value"]:  return "HIGH_VALUE"
    if s.score >= THRESHOLDS["qualified"]:   return "QUALIFIED"
    if s.score >= THRESHOLDS["monitor"]:     return "MONITOR"
    return "SPAM"


_META = {
    "HIGH_VALUE": {
        "tier":    "High Value",
        "action":  "ALERT_AND_LOG",
        "summary": "Strong buying signals — complex brief, professional domain, decision-maker language.",
    },
    "QUALIFIED": {
        "tier":    "Qualified",
        "action":  "ALERT_AND_LOG",
        "summary": "Legitimate enquiry with clear project signals. Respond promptly.",
    },
    "MONITOR": {
        "tier":    "Monitor",
        "action":  "LOG_ONLY",
        "summary": "Possible genuine enquiry but signals are weak. Log and review manually.",
    },
    "SPAM": {
        "tier":    "Spam",
        "action":  "DISCARD",
        "summary": "Vendor solicitation or spam pattern. No action needed.",
    },
    "INTERNAL": {
        "tier":    "Internal",
        "action":  "IGNORE",
        "summary": "Own-domain test or internal submission. Skip.",
    },
}


def _build_output(p: _Parsed, s: _Scored, verdict: str) -> dict[str, Any]:
    meta = _META[verdict]
    top5 = sorted(s.signals, key=lambda x: x["points"], reverse=True)[:5]
    return {
        "verdict":    verdict,
        "score":      s.score,
        "tier":       meta["tier"],
        "action":     meta["action"],
        "summary":    s.dq_reason if s.disqualified else meta["summary"],
        "confident":  abs(s.score) >= 20,
        "top_signals": [
            f"{sig['matched']} ({'+' if sig['points']>0 else ''}{sig['points']})"
            for sig in top5
        ],
        "signals": s.signals,
        "parsed": {
            "name":            f"{p.first_name} {p.last_name}".strip(),
            "email":           p.email,
            "phone":           p.phone,
            "company":         p.company,
            "source":          p.source.upper() if p.source != "unknown" else "Unknown",
            "message":         p.message,
            "message_preview": p.message[:300] + ("..." if len(p.message) > 300 else ""),
        },
        "classified_at": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================
# THRESHOLDS
# Adjust these to tune sensitivity without touching any logic.
# ============================================================

THRESHOLDS = {
    "high_value": 60,   # ≥ this → HIGH_VALUE + Slack alert
    "qualified":  35,   # ≥ this → QUALIFIED + Slack alert
    "monitor":    15,   # ≥ this → MONITOR + Notion log only
                        # < 20 and not disqualified → SPAM / DISCARD
}


# ============================================================
# RULES
# All scoring logic lives here. Edit freely.
# ============================================================

RULES = {
    # ── Hard disqualifiers ──────────────────────────────────────────────────────
    "internal_domains": {
        "devstars.co.uk",
        "devstars.com",
        "lwda.co.uk",
        "londonwebdesignagency.com",
    },
    "internal_test_phrases": [
        "hello world",
        "fdfasdfas",
        "did you get this",
        "hello bibi",
    ],

    # ── Negative signals ────────────────────────────────────────────────────────
    "vendor_form_type": "i'd like to provide a service",

    "spam_phrases": [
        # Vendor / link-building outreach
        "guest post", "guest blog", "do-follow", "backlink",
        "seo service", "seo technique",
        "we are a growing digital marketing",
        # Offshore dev solicitation
        "offshore", "outsourc", "starting rate", "usd/hour", "/hour",
        "affordable price", "at a competitive price",
        # Pitch template phrases
        "free homepage design", "no obligation",
        "source photoshop", "figma file with all layers",
        # Phone screen / call centre artefacts
        "amazon business", "purchasing account",
        "silent call", "sales call", "call back required",
    ],

    "spam_email_patterns": [
        r"gmail\.com$",      # Generic free email — soft negative signal only
        r"yahoo\.com$",
        r"hotmail\.com$",
        r"@declined\.com$",  # pureJAM placeholder address
    ],

    "spam_phone_patterns": [
        r"^\+91",    # India
        r"^\+92",    # Pakistan
        r"^\+880",   # Bangladesh
        r"^0334",    # VoIP spam prefix observed in your inbox
    ],

    "known_spam_senders": [
        "amazon business",
        "webgrityworks",
        "ecodesoft",
    ],

    # ── Positive signals: project complexity & buying intent ────────────────────
    "high_value_phrases": [
        # Technical complexity
        ("bespoke",               10), ("custom development",     12),
        ("platform",              12), ("subscription",           10),
        ("saas",                  15), ("e-commerce",              8),
        ("ecommerce",              8), ("rebuild",                  8),
        ("redesign",               7), ("ios",                     10),
        ("android",               10), ("mobile app",              12),
        ("web app",               10), ("integration",             10),
        ("api",                    8), ("recurring billing",       12),
        ("secure access",         10), ("accessibility",           10),
        ("wcag",                  12), ("digital transformation",  10),
        ("architecture",          10), ("scalability",             10),
        ("end-to-end",             8), ("migration",                8),
        ("protected video",       10), ("cross-device",             8),
        # Buying intent
        ("indicative budget",     10), ("budget range",            10),
        ("fixed price quotation",  8), ("proposal",                 6),
        ("case studies",           6), ("introductory call",        6),
        ("discovery call",         8), ("availability",             4),
        ("target launch",          8), ("go live",                  6),
        ("framework stage",       10), ("high-priority contract",  15),
        ("high priority",          8), ("mission-critical",        15),
        ("contract",               6), ("procurement",             12),
        ("tender",                15), ("rfq",                     15),
        ("rfp",                   15),
        # Org type / scale
        ("charity",                8), ("nhs",                     12),
        ("government",            12), ("council",                  8),
        ("housing association",   10), ("ltd",                      4),
        ("limited",                4), ("group",                    4),
        ("holdings",               6), ("plc",                     10),
    ],

    # ── Positive signals: sector / vertical fit ──────────────────────────────────
    "vertical_phrases": [
        ("property",      6), ("estate agent",  8), ("real estate",  6),
        ("lettings",      6), ("salon",          6), ("hospitality",  5),
        ("hotel",         5), ("charity",        6), ("nonprofit",    6),
        ("non-profit",    6), ("powersports",    4), ("storage",      4),
        ("luxury",        5), ("retail",         4), ("insurance",    5),
        ("legal",         5), ("finance",        5), ("fintech",      8),
    ],

    # ── Positive signals: seniority of the enquirer ──────────────────────────────
    # Higher seniority → more likely to be the budget holder
    "seniority_phrases": [
        ("founder",                 8), ("director",            8),
        ("ceo",                    12), ("cto",                12),
        ("head of",                 8), ("managing director",  12),
        ("chief",                  10), ("vp ",                10),
        ("vice president",         10), ("owner",               8),
        ("partner",                 6), ("manager",             4),
        ("digital platforms manager", 8), ("web manager",       4),
    ],

    # ── Positive signals: budget authority language ───────────────────────────────
    # These phrases suggest the enquirer controls or has direct access to budget
    "budget_authority_phrases": [
        ("our budget",              10), ("we have budget",         12),
        ("approved budget",         12), ("budget allocated",       12),
        ("we're ready to",           8), ("we are ready to",         8),
        ("looking to invest",        8), ("looking to spend",        8),
        ("happy to discuss budget", 10), ("can move quickly",        8),
        ("decision has been made",  12),
    ],

    # ── Positive signals: multi-brand / large account indicators ──────────────────
    "multi_brand_phrases": [
        ("several brands",          12), ("several property brands", 12),
        ("several agency brands",   12), ("several retail brands",   12),
        ("multiple brands",         12), ("several sites",           10),
        ("multiple sites",          10), ("portfolio of sites",      12),
        ("group of companies",      10), ("holding company",         10),
        ("across our brands",       12),
    ],

    # ── Supporting positive signals ───────────────────────────────────────────────
    "free_email_domains": {
        "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
        "icloud.com", "me.com", "live.com", "msn.com", "aol.com",
    },
    "professional_email_bonus": 8,
    "message_length_tiers": [
        (600, 12),   # detailed brief
        (300,  8),   # considered enquiry
        (150,  4),   # brief but genuine
    ],
}


# ============================================================
# SELF-TEST
# Run: python email_classifier.py
# ============================================================

if __name__ == "__main__":
    TESTS = [
        {
            "_expect": "HIGH_VALUE",
            "subject": "[LWDA] Contact Form - Iram Yasin",
            "sender":  "LWDA <info@londonwebdesignagency.com>",
            "body": """Form Type: I need some help
First Name: Iram
Last Name: Yasin
Email Address: iram@loveluxury.co.uk
Phone: 07841578396
Message: We are seeking a development partner to build a secure, subscription-based video content platform with iOS and Android apps. We are at framework stage wanting an agency to advise on architecture, scalability, security and end-to-end delivery. Includes recurring billing, secure access control, and protected video hosting. Please discuss your technical approach, timelines and indicative budget range.""",
        },
        {
            "_expect": "HIGH_VALUE",
            "subject": "[LWDA] Contact Form - Mary Senier",
            "sender":  "LWDA <info@londonwebdesignagency.com>",
            "body": """Form Type: I need some help
First Name: Mary
Last Name: Senier
Email Address: Mary.Senier@victimsupport.org.uk
Phone:
Message: My name is Mary, I'm the Digital Platforms Manager at Victim Support. We're looking for a developer agency for a high-priority contract to build a lightweight, fully accessible website including a 24/7 helpline and a referral form integrating with our internal systems. I'd love to arrange an introductory call.""",
        },
        {
            "_expect": "QUALIFIED",
            "subject": "[LWDA] Contact Form - Liam Riordan",
            "sender":  "LWDA <info@londonwebdesignagency.com>",
            "body": """Form Type: I need some help
First Name: Liam
Last Name: Riordan
Email Address: liam.riordan@campionsgroup.co.uk
Phone: 07989 476 973
Message: I'm the web manager for the Campions property group. We have several property brands. I'm interested in a new skin for the Chestertons site. https://www.chestertons.co.uk/""",
        },
        {
            "_expect": "QUALIFIED",
            "subject": "[LWDA] Contact Form - Nicholas Ware",
            "sender":  "LWDA <info@londonwebdesignagency.com>",
            "body": """Form Type: I'd like to provide a service
First Name: Nicholas
Last Name: Ware
Email Address: nick@yourbrandcover.com
Phone: 07904858811
Message: We are seeking a UK agency to fully redesign and rebuild our site with modern UX, e-commerce functionality and complete technical rebuild. Target launch: 31 July 2026. Please confirm availability and indicative budget range.""",
        },
        {
            "_expect": "MONITOR",
            "subject": "[LWDA] Contact Form - Abdullah Aqtash",
            "sender":  "LWDA <info@londonwebdesignagency.com>",
            "body": """Form Type: I need some help
First Name: Abdullah
Last Name: Aqtash
Email Address: gcb.ltd7@gmail.com
Phone: 07305122662
Company Name: Glamour Carpets & Beds Ltd
Message: I run Glamour Carpets & Beds Ltd in Fulham SW6. We need website redevelopment for product pages and local SEO. Looking for a WordPress redesign. Could you send a fixed price quotation and examples.""",
        },
        {
            "_expect": "SPAM",
            "subject": "[LWDA] Contact Form - Avani Shukla",
            "sender":  "LWDA <info@londonwebdesignagency.com>",
            "body": """Form Type: I'd like to provide a service
First Name: Avani
Last Name: Shukla
Email Address: avanishukla.seo@gmail.com
Phone: 07827908900
Message: We are a growing digital marketing company providing Guest Posts. Guest blogging is a powerful SEO technique with do-follow backlinks. Website design at affordable price.""",
        },
        {
            "_expect": "SPAM",
            "subject": "[LWDA] Contact Form - Alice Flores",
            "sender":  "LWDA <info@londonwebdesignagency.com>",
            "body": """Form Type: I need some help
First Name: Alice
Last Name: Flores
Email Address: support@webgrityworks.com
Phone: 03340073326
Message: Our free homepage design shows how our process works. Source Photoshop or Figma file with all layers. No obligation.""",
        },
        {
            "_expect": "INTERNAL",
            "subject": "[DevStars] Contact Form - Stuart Watkins",
            "sender":  "DevStars <info@londonwebdesignagency.com>",
            "body": """Form Type: I need some help
First Name: Stuart
Last Name: Watkins
Email Address: stuart@devstars.co.uk
Phone: 07866686178
Message: Hello Bibi""",
        },
        {
            "_expect": "INTERNAL",
            "subject": "Message London Web Design Agency group Screen - 0161 949 1226",
            "sender":  "pureJAM <donotreply@jam.co.uk>",
            "body": """Call Type? -
Call For? -
Message Silent call.
Name
Telephone 0161 949 1226""",
        },
    ]

    print(f"\n{'═'*74}")
    print(f" email_classifier.py — self-test ({len(TESTS)} emails)")
    print(f"{'═'*74}\n")
    print(f" {'RESULT':<10} {'NAME':<22} {'SCORE':>6} {'VERDICT':<12} TOP SIGNALS")
    print(f" {'─'*70}")

    passed = failed = 0
    for t in TESTS:
        expected = t.pop("_expect")
        r = classify_email(t)
        ok = r["verdict"] == expected
        if ok: passed += 1
        else:  failed += 1
        flag    = "✓ PASS" if ok else "✗ FAIL"
        name    = r["parsed"]["name"] or "(no name)"
        signals = ", ".join(r["top_signals"][:2]) or "—"
        print(f" {flag:<10} {name:<22} {r['score']:>6} {r['verdict']:<12} {signals}")
        if not ok:
            print(f"           ↳ Expected {expected}")

    print(f"\n {'─'*70}")
    print(f" {passed}/{len(TESTS)} passed", "✓ All good!" if failed == 0 else f" ✗ {failed} failed — check RULES")
    print()
