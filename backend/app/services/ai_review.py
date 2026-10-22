"""AI configuration validation and change security review (any configured AI provider).

Secrets (passwords, SNMP communities, keys, private keys) are always masked
before anything leaves the server. IP addresses can optionally be replaced by
placeholders as well; placeholders are mapped back in the result.
"""
from __future__ import annotations

import json
import logging
import re
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.services import settings_store as store
from app.services.diff import compare

logger = logging.getLogger("ncm.ai")

MAX_CHARS = 120_000          # default input cap
# Smaller caps for providers whose free tiers have small per-minute token budgets / local models
MAX_CHARS_BY_KIND = {"groq": 24_000, "ollama": 40_000, "custom": 60_000, "mistral": 60_000}

K_MASK_IPS = "ai.mask_ips"
K_AUTO = "ai.auto_review"
K_LAST_ERROR = "ai.last_error"

SEVERITIES = ["critical", "high", "medium", "low", "info"]
RISKS = ["low", "medium", "high", "critical"]


from app.services.llm import AIError  # noqa: E402  (re-exported for callers)
from app.services import llm, ai_providers  # noqa: E402


# ───────────────────────── masking ─────────────────────────

_KEYWORDS = (r"password|passwd|secret|pre-shared-key|preshared-key|authentication-key|auth-key|"
             r"key-string|community|shared-key|psk|md5|sha|encrypted-password|key|cipher|plain|"
             r"auth|priv|authentication-mode|privacy-mode")
# Algorithm / encoding words between a keyword and its value ("md5 7 <key>", "auth sha512 <key>")
_MODIFIERS = (r"cipher|simple|irreversible-cipher|encrypted|hash|sha\d*|ascii-text|plain-text|plain|"
              r"md5|hmac-sha\d*|hmac-md5|aes\d*|des|3des|read|write|ro|rw|key|\d{1,2}")
# [ \t] not \s: a match must never run onto the next config line
_SECRET_RE = re.compile(
    rf"(?P<pre>\b(?:{_KEYWORDS})\b(?:[ \t]+(?:{_MODIFIERS})\b)*[ \t]+)(?P<val>\"[^\"\n]*\"|\S+)",
    re.IGNORECASE,
)
# Hash / encrypted blobs anywhere (Junos $9$, crypt $1$ $5$ $6$, Huawei %^%#, %$%$, %@%@ and @%@%)
_HASH_RE = re.compile(r'"?(?:\$\d[a-z]?\$[^\s"]+|%\^%#[^\s"]+|%\$%\$[^\s"]+|%@%@[^\s"]+|@%@%[^\s"]+)"?')
_PEM_RE = re.compile(r"-----BEGIN [A-Z ]*-----.*?-----END [A-Z ]*-----", re.DOTALL)
_IPV4_RE = re.compile(r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b")
# Values after a keyword that are syntax, not secrets
_NOT_SECRET = {"enable", "disable", "none", "chain", "{", "required", "mode", "type", "authentication",
               "authentication-mode", "local", "group", "privacy-mode", "complexity-check", "generate",
               "zeroize", "exchange", "recovery", "policy", "-"}
# Well-known weak values: still hidden, but the AI is told they are weak defaults
_WEAK = {"public", "private", "community", "cisco", "admin", "password", "123456", "12345678", "huawei",
         "admin123", "admin@123", "huawei@123", "juniper", "arista", "test", "default", "secret"}


def mask_secrets(text: str) -> str:
    text = _PEM_RE.sub("<MASKED-CERTIFICATE-OR-KEY>", text)

    def _sub(m):
        val = m.group("val")
        if val.lower() in _NOT_SECRET or val.startswith("<MASKED"):
            return m.group(0)
        if val.strip('"').lower() in _WEAK:
            return m.group("pre") + "<MASKED-WEAK-DEFAULT>"
        return m.group("pre") + "<MASKED>"

    text = _SECRET_RE.sub(_sub, text)
    return _HASH_RE.sub("<MASKED>", text)


class IPMasker:
    """Replace IPv4 addresses with stable placeholders (IP-1, IP-2, ...)."""

    def __init__(self):
        self.forward: dict[str, str] = {}

    def mask(self, text: str) -> str:
        def _sub(m):
            ip = m.group(0)
            if ip in ("0.0.0.0", "255.255.255.255") or ip.startswith("255."):
                return ip   # masks/wildcards carry no identity
            if ip not in self.forward:
                self.forward[ip] = f"IP-{len(self.forward) + 1}"
            return self.forward[ip]
        return _IPV4_RE.sub(_sub, text)

    def unmask(self, obj):
        if not self.forward:
            return obj
        back = {v: k for k, v in self.forward.items()}
        pattern = re.compile(r"\bIP-(\d+)\b")

        def fix(s: str) -> str:
            return pattern.sub(lambda m: back.get(m.group(0), m.group(0)), s)

        if isinstance(obj, str):
            return fix(obj)
        if isinstance(obj, list):
            return [self.unmask(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.unmask(v) for k, v in obj.items()}
        return obj


def prepare(text: str, ip_masker: IPMasker | None, limit: int = MAX_CHARS) -> tuple[str, bool]:
    text = mask_secrets(text)
    if ip_masker:
        text = ip_masker.mask(text)
    truncated = len(text) > limit
    return (text[:limit] + "\n<TRUNCATED>") if truncated else text, truncated


# ───────────────────────── status / settings ─────────────────────────

def is_configured(db=None, user=None) -> bool:
    if db is None:
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            return ai_providers.is_configured(db, user)
        finally:
            db.close()
    return ai_providers.is_configured(db, user)


def status(db, user=None) -> dict:
    cands = ai_providers.candidates(db, user)
    active = cands[0] if cands else None
    d = ai_providers.get_default(db)
    return {
        "configured": bool(cands),
        "active_provider": active[0].name if active else None,
        "active_kind": active[0].kind if active else None,
        "model": active[1] if active else None,
        "default_provider_id": d.get("provider_id"), "default_model": d.get("model"),
        "fallback": ai_providers.fallback_on(db),
        "mask_ips": store.get(db, K_MASK_IPS, "0") == "1",
        "auto_review": store.get(db, K_AUTO, "0") == "1",
        "live_commands": store.get(db, "ai.live_commands", "0") == "1",
        "auto_queue": _auto_queue.qsize() if "_auto_queue" in globals() else 0,
        "last_error": store.get(db, K_LAST_ERROR),
    }


# ───────────────────────── Gemini call ─────────────────────────

FINDING_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "severity": {"type": "STRING", "enum": SEVERITIES},
        "category": {"type": "STRING", "enum": ["security", "compliance", "best-practice", "availability", "syntax"]},
        "title": {"type": "STRING"},
        "detail": {"type": "STRING"},
        "evidence": {"type": "STRING"},
        "recommendation": {"type": "STRING"},
        "fix_commands": {"type": "STRING"},
    },
    "required": ["severity", "category", "title", "detail", "recommendation"],
}

VALIDATE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "score": {"type": "INTEGER"},
        "summary": {"type": "STRING"},
        "strengths": {"type": "ARRAY", "items": {"type": "STRING"}},
        "findings": {"type": "ARRAY", "items": FINDING_SCHEMA},
    },
    "required": ["score", "summary", "findings"],
}

REVIEW_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "risk": {"type": "STRING", "enum": RISKS},
        "summary": {"type": "STRING"},
        "changes": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {"what": {"type": "STRING"}, "impact": {"type": "STRING"}},
            "required": ["what", "impact"],
        }},
        "findings": {"type": "ARRAY", "items": FINDING_SCHEMA},
        "rollback_commands": {"type": "STRING"},
    },
    "required": ["risk", "summary", "changes", "findings"],
}

COMMON_RULES = """
Rules:
- Secrets are replaced with <MASKED>. A masked value means the secret IS configured; never report it as missing or weak because it is masked.
- If a removed line and an added line differ only by <MASKED>, the secret value was changed (rotated); mention it, but it is not a problem by itself.
- <MASKED-WEAK-DEFAULT> means the secret is a well-known default or weak value (for example 'public'); report it as a finding.
- Addresses may be replaced with placeholders like IP-1; treat them as real addresses.
- Report only real issues you can see in the text. Copy the exact config line(s) into "evidence". Do not invent lines.
- Use the vendor's own CLI syntax in "fix_commands". Leave it empty if no command applies.
- Keep "title" short. Write "detail" and "recommendation" in plain English for a NOC engineer.
- If the configuration text is truncated, say so in the summary.
"""

VALIDATE_PROMPT = """You are a senior network security engineer auditing the running configuration of a {vendor} device ({model}), hostname {hostname}.

Check at least: management access (SSH v2 only, Telnet/HTTP/FTP disabled, ACL on VTY/management), AAA and local users (TACACS/RADIUS, privilege levels, password hashing), SNMP (prefer v3, no default or weak communities, ACL), NTP (configured, authenticated), logging and syslog servers, login banner, idle timeouts, unused or insecure services, routing protocol authentication (OSPF, IS-IS, BGP), BGP safety (max-prefix, prefix filters), control-plane protection, STP edge protections (BPDU guard, root guard), DHCP snooping / ARP inspection on access switches, interface hygiene (unused ports shut, descriptions), redundancy (single uplinks, single NTP/syslog server).
Give a "score" from 0 (very poor) to 100 (excellent). Start at 100 and deduct roughly: critical 25, high 12, medium 6, low 2.
List what is already done well in "strengths" (short items).
""" + COMMON_RULES

REVIEW_PROMPT = """You are a senior network security engineer reviewing a configuration CHANGE on a {vendor} device ({model}), hostname {hostname}.
The change is given as a unified diff: lines starting with '-' were removed, lines starting with '+' were added, other lines are context.

Assess security and operational risk. Pay special attention to: ACL or firewall rule removal or widening, management access opened, AAA/authentication disabled, new local users or privilege changes, credential changes, SNMP communities added, logging disabled, routing changes that could leak or blackhole traffic (BGP neighbours, policies, static routes, OSPF areas), interfaces shut or VLANs removed from trunks, STP changes, NTP or syslog servers removed.
Set "risk": low (cosmetic or safe), medium (needs a check), high (likely security or outage impact), critical (dangerous, act now).
In "changes" describe each logical change in one line with its impact.
In "rollback_commands" give the vendor CLI commands that would undo the change.
""" + COMMON_RULES


def _lower(schema):
    """Gemini-style (uppercase type) schema -> standard JSON schema."""
    if isinstance(schema, dict):
        return {k: (v.lower() if k == "type" and isinstance(v, str) else _lower(v)) for k, v in schema.items()}
    if isinstance(schema, list):
        return [_lower(x) for x in schema]
    return schema


def _limit(p) -> int:
    return MAX_CHARS_BY_KIND.get(p.kind, MAX_CHARS)


def _clean_findings(items) -> list[dict]:
    out = []
    for f in items or []:
        if not isinstance(f, dict) or not f.get("title"):
            continue
        sev = str(f.get("severity", "info")).lower()
        f["severity"] = sev if sev in SEVERITIES else "info"
        out.append(f)
    out.sort(key=lambda f: SEVERITIES.index(f["severity"]))
    return out


# ───────────────────────── public operations ─────────────────────────

def _read(backup) -> str:
    root = Path(settings.backup_root).resolve()
    path = Path(backup.file_path or "").resolve()
    path.relative_to(root)
    return path.read_text(encoding="utf-8", errors="replace")


def validate_config(db, backup, device, user=None) -> dict:
    masker = IPMasker() if store.get(db, K_MASK_IPS, "0") == "1" else None
    raw = _read(backup)
    system = VALIDATE_PROMPT.format(vendor=device.vendor, model=device.model or "unknown model", hostname=device.hostname)
    state = {}

    def call(conn, model, p):
        text, state["truncated"] = prepare(raw, masker, _limit(p))
        return llm.complete_json(conn, model, system, f"Running configuration:\n\n{text}", _lower(VALIDATE_SCHEMA))

    result, prov, used = ai_providers.run(db, user, call)
    truncated = state.get("truncated", False)
    result["_provider"], result["_model"] = prov.name, used
    if masker:
        result = masker.unmask(result)
    result["findings"] = _clean_findings(result.get("findings"))
    try:
        result["score"] = max(0, min(100, int(result.get("score", 0))))
    except (TypeError, ValueError):
        result["score"] = None
    result["truncated"] = truncated
    result["masked_ips"] = bool(masker)
    return result


def review_change(db, backup_a, backup_b, device, user=None) -> dict:
    masker = IPMasker() if store.get(db, K_MASK_IPS, "0") == "1" else None
    a_text, b_text = _read(backup_a), _read(backup_b)
    diff = compare(a_text, b_text, ignore_volatile=True, context=4)
    if not diff["changed"]:
        return {"risk": "low", "summary": "No configuration change between these backups (only timestamps or headers differ).",
                "changes": [], "findings": [], "rollback_commands": "", "no_change": True, "stats": diff["stats"]}
    unified = "\n".join(diff["lines"][2:])   # drop ---/+++ headers (contain file paths)
    system = REVIEW_PROMPT.format(vendor=device.vendor, model=device.model or "unknown model", hostname=device.hostname)
    state = {}

    def call(conn, model, p):
        text, state["truncated"] = prepare(unified, masker, _limit(p))
        return llm.complete_json(conn, model, system, f"Configuration diff:\n\n{text}", _lower(REVIEW_SCHEMA))

    result, prov, used = ai_providers.run(db, user, call)
    truncated = state.get("truncated", False)
    result["_provider"], result["_model"] = prov.name, used
    if masker:
        result = masker.unmask(result)
    risk = str(result.get("risk", "medium")).lower()
    result["risk"] = risk if risk in RISKS else "medium"
    result["findings"] = _clean_findings(result.get("findings"))
    result["truncated"] = truncated
    result["masked_ips"] = bool(masker)
    result["stats"] = diff["stats"]
    return result


def save(db, kind, device_id, backup_a, backup_b, result, user=None):
    from app.models.ai_review import AIReview
    row = AIReview(kind=kind, device_id=device_id, backup_a=backup_a, backup_b=backup_b,
                   model=f"{result.get('_provider', '')}: {result.get('_model', '')}".strip(": ")[:100], risk=result.get("risk"), score=result.get("score"),
                   result=json.dumps(result), created_by=user)
    db.add(row)
    store.set(db, K_LAST_ERROR, None, commit=False)
    db.commit()
    db.refresh(row)
    return row


def record_error(db, message: str):
    store.set(db, K_LAST_ERROR, f"{datetime.utcnow().isoformat()} {message}")


_auto_queue: "queue.Queue[tuple[int, int]]" = queue.Queue()
_auto_worker = {"thread": None}
AUTO_GAP_SECONDS = 15        # keep background reviews well under free-tier per-minute limits


def _auto_review_one(previous_id: int, backup_id: int):
    from app.core.database import SessionLocal
    from app.models.backup import Backup
    from app.models.device import Device
    db = SessionLocal()
    try:
        if store.get(db, K_AUTO, "0") != "1" or not ai_providers.is_configured(db):
            return
        a, b = db.get(Backup, previous_id), db.get(Backup, backup_id)
        if not a or not b:
            return
        device = db.get(Device, b.device_id)
        result = review_change(db, a, b, device)
        if not result.get("no_change"):
            save(db, "review", b.device_id, a.id, b.id, result, user="auto")
    except AIError as exc:
        logger.warning("Auto AI review failed for backup %s: %s", backup_id, exc)
        record_error(db, str(exc))
        if "limit reached" in str(exc):
            time.sleep(60)                   # back off before the next queued review
    except Exception as exc:
        logger.warning("Auto AI review failed for backup %s: %s", backup_id, exc)
    finally:
        db.close()


def _auto_loop():
    while True:
        prev_id, backup_id = _auto_queue.get()
        _auto_review_one(prev_id, backup_id)
        time.sleep(AUTO_GAP_SECONDS)


def auto_review_async(previous_id: int, backup_id: int):
    """Queue a background review of a changed backup. Reviews run one at a time, spaced out."""
    _auto_queue.put((previous_id, backup_id))
    if not _auto_worker["thread"] or not _auto_worker["thread"].is_alive():
        _auto_worker["thread"] = threading.Thread(target=_auto_loop, name="ai-auto-review", daemon=True)
        _auto_worker["thread"].start()


def auto_queue_size() -> int:
    return _auto_queue.qsize()
