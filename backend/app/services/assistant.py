# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""NCM chat assistant: answers questions about devices, backups and config changes.

The AI model decides which tools to call; every tool reads NCM's own data (secrets
masked). The optional live tool runs read-only show/display commands over SSH
when an admin has enabled it.
"""
from __future__ import annotations

from datetime import timedelta, timezone
from pathlib import Path

from app.core.config import settings
from app.models.backup import Backup
from app.models.device import Device
from app.services import ai_review as ai
from app.services import ai_providers, llm
from app.services import settings_store as store
from app.services.diff import compare
from app.core.timeutil import utcnow

K_LIVE = "ai.live_commands"
MAX_TOOL_ROUNDS = 6
MAX_HISTORY = 16


# ───────────────────────── helpers ─────────────────────────

class Ctx:
    def __init__(self, db, user, tz_offset_min: int, live_allowed: bool, masker):
        self.db, self.user, self.tz, self.live_allowed, self.masker = db, user, tz_offset_min, live_allowed, masker
        self.steps: list[dict] = []

    def t(self, dt):
        if not dt:
            return None
        local = dt.replace(tzinfo=timezone.utc) + timedelta(minutes=self.tz)
        return local.strftime("%Y-%m-%d %H:%M")

    def mask(self, text: str) -> str:
        text = ai.mask_secrets(text)
        return self.masker.mask(text) if self.masker else text


def _read(b: Backup):
    try:
        p = Path(b.file_path or "").resolve()
        p.relative_to(Path(settings.backup_root).resolve())
        return p.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None


def _find_device(ctx: Ctx, name: str | None):
    """Return (device, error_dict)."""
    if not name:
        return None, {"error": "device name is required"}
    q = name.strip().lower()
    devices = ctx.db.query(Device).all()
    exact = [d for d in devices if d.hostname.lower() == q or d.management_ip == name.strip()]
    if exact:
        return exact[0], None
    partial = [d for d in devices if q in d.hostname.lower()]
    if len(partial) == 1:
        return partial[0], None
    if partial:
        return None, {"error": "more than one device matches", "candidates": [d.hostname for d in partial][:20]}
    return None, {"error": f"no device named '{name}'", "known_devices": [d.hostname for d in devices][:50]}


def _ok_backups(ctx: Ctx, device_id: int, limit: int = 50):
    return (ctx.db.query(Backup).filter(Backup.device_id == device_id, Backup.status == "SUCCESS")
            .order_by(Backup.id.desc()).limit(limit).all())


def _latest(ctx: Ctx, device_id: int):
    rows = _ok_backups(ctx, device_id, 1)
    return rows[0] if rows else None


# ───────────────────────── tools ─────────────────────────

def tool_list_devices(ctx: Ctx, site: str = "", vendor: str = ""):
    q = ctx.db.query(Device)
    out = []
    for d in q.order_by(Device.hostname).all():
        if site and (d.site or "").lower() != site.lower():
            continue
        if vendor and (d.vendor or "").lower() != vendor.lower():
            continue
        last = _latest(ctx, d.id)
        out.append({"hostname": d.hostname, "ip": ctx.mask(d.management_ip), "vendor": d.vendor, "model": d.model,
                    "site": d.site, "enabled": d.enabled, "last_successful_backup": ctx.t(last.created_at) if last else None})
    return {"count": len(out), "devices": out}


def tool_backup_status(ctx: Ctx, device: str = ""):
    if device:
        d, err = _find_device(ctx, device)
        if err:
            return err
        devices = [d]
    else:
        devices = ctx.db.query(Device).order_by(Device.hostname).all()
    out = []
    for d in devices:
        last = (ctx.db.query(Backup).filter(Backup.device_id == d.id, Backup.status.in_(["SUCCESS", "FAILED"]))
                .order_by(Backup.id.desc()).first())
        ok = _latest(ctx, d.id)
        out.append({"hostname": d.hostname, "enabled": d.enabled,
                    "last_attempt": ctx.t(last.created_at) if last else None,
                    "last_attempt_status": last.status if last else "never",
                    "last_error": ctx.mask(last.error) if last and last.status == "FAILED" and last.error else None,
                    "last_successful_backup": ctx.t(ok.created_at) if ok else None})
    return {"devices": out}


def tool_recent_changes(ctx: Ctx, device: str = "", days: int = 7, limit: int = 10):
    days = max(1, min(int(days or 7), 90))
    limit = max(1, min(int(limit or 10), 25))
    since = utcnow() - timedelta(days=days)
    q = ctx.db.query(Backup).filter(Backup.status == "SUCCESS")
    if device:
        d, err = _find_device(ctx, device)
        if err:
            return err
        q = q.filter(Backup.device_id == d.id)
    hosts = {d.id: d.hostname for d in ctx.db.query(Device).all()}
    recent = q.filter(Backup.created_at >= since).order_by(Backup.id.desc()).all()
    changes = []
    for b in recent:
        if len(changes) >= limit:
            break
        prev = (ctx.db.query(Backup).filter(Backup.device_id == b.device_id, Backup.status == "SUCCESS",
                                            Backup.id < b.id).order_by(Backup.id.desc()).first())
        if not prev or (prev.sha256 and b.sha256 and prev.sha256 == b.sha256):
            continue
        a_text, b_text = _read(prev), _read(b)
        if a_text is None or b_text is None:
            continue
        res = compare(a_text, b_text, ignore_volatile=True, context=1)
        if not res["changed"]:
            continue
        lines = [ctx.mask(x) for x in res["lines"][2:] if not x.startswith("@@")][:40]
        changes.append({"device": hosts.get(b.device_id), "backup_id": b.id, "previous_backup_id": prev.id,
                        "time": ctx.t(b.created_at), "added": res["stats"]["added"], "removed": res["stats"]["removed"],
                        "diff": lines})
    return {"period_days": days, "changes_found": len(changes), "changes": changes,
            "note": "diff lines: '-' removed, '+' added, ' ' context"}


def tool_get_diff(ctx: Ctx, device: str, backup_a: int = 0, backup_b: int = 0):
    d, err = _find_device(ctx, device)
    if err:
        return err
    rows = _ok_backups(ctx, d.id, 200)
    by_id = {r.id: r for r in rows}
    if backup_a and backup_b:
        a, b = by_id.get(int(backup_a)), by_id.get(int(backup_b))
        if not a or not b:
            return {"error": "backup ids not found for this device", "recent_backup_ids": [r.id for r in rows[:15]]}
        if a.id > b.id:
            a, b = b, a
    else:
        if len(rows) < 2:
            return {"error": "device has fewer than two successful backups"}
        b, a = rows[0], rows[1]
    a_text, b_text = _read(a), _read(b)
    if a_text is None or b_text is None:
        return {"error": "backup file missing"}
    res = compare(a_text, b_text, ignore_volatile=True, context=3)
    lines = [ctx.mask(x) for x in res["lines"][2:]][:300]
    return {"device": d.hostname, "older_backup": {"id": a.id, "time": ctx.t(a.created_at)},
            "newer_backup": {"id": b.id, "time": ctx.t(b.created_at)}, "changed": res["changed"],
            "added": res["stats"]["added"], "removed": res["stats"]["removed"], "diff": lines}


def tool_search_config(ctx: Ctx, text: str, device: str = "", max_results: int = 80):
    if not text or len(text.strip()) < 2:
        return {"error": "search text must be at least 2 characters"}
    needle = text.strip().lower()
    max_results = max(1, min(int(max_results or 80), 200))
    if device:
        d, err = _find_device(ctx, device)
        if err:
            return err
        devices = [d]
    else:
        devices = ctx.db.query(Device).order_by(Device.hostname).all()
    hits, devices_matched = [], 0
    for d in devices:
        b = _latest(ctx, d.id)
        content = _read(b) if b else None
        if not content:
            continue
        found = False
        for i, line in enumerate(content.splitlines(), 1):
            if needle in line.lower():
                found = True
                if len(hits) < max_results:
                    hits.append({"device": d.hostname, "line_no": i, "line": ctx.mask(line.rstrip())})
        devices_matched += found
    return {"searched_devices": len(devices), "devices_with_match": devices_matched, "matches": hits,
            "truncated": len(hits) >= max_results, "source": "latest successful backup of each device"}


def tool_get_config(ctx: Ctx, device: str, section: str = ""):
    d, err = _find_device(ctx, device)
    if err:
        return err
    b = _latest(ctx, d.id)
    content = _read(b) if b else None
    if not content:
        return {"error": "no backup available for this device"}
    lines = content.splitlines()
    if section:
        key = section.strip().lower()
        out, take = [], False
        for line in lines:
            starts_block = line and not line[0].isspace() and line.strip() not in ("#", "!")
            if starts_block:
                take = key in line.lower()
            elif key in line.lower():
                out.append(line.rstrip())
                continue
            if take:
                out.append(line.rstrip())
        lines = out or [f"<no lines matching '{section}'>"]
    text = ctx.mask("\n".join(lines[:500]))
    return {"device": d.hostname, "backup_id": b.id, "backup_time": ctx.t(b.created_at),
            "lines": len(lines), "truncated": len(lines) > 500, "config": text}


def tool_run_show_command(ctx: Ctx, device: str, command: str):
    if not ctx.live_allowed:
        return {"error": "Live commands are turned off, or this user is not allowed to run them. "
                         "Answer from backup data, and tell the user an admin can enable live commands on the AI review page."}
    from app.services.network import run_show_command, LiveCommandError
    from app.services.audit import record_audit
    d, err = _find_device(ctx, device)
    if err:
        return err
    if not d.enabled:
        return {"error": "device is disabled in NCM"}
    try:
        out = run_show_command(d, command)
        record_audit(ctx.db, ctx.user, "AI_LIVE_COMMAND", "device", d.id, details=command)
        return {"device": d.hostname, "command": command, "ran_at": ctx.t(utcnow()), "output": ctx.mask(out)}
    except LiveCommandError as exc:
        return {"error": f"command refused: {exc}"}
    except Exception as exc:
        record_audit(ctx.db, ctx.user, "AI_LIVE_COMMAND", "device", d.id, status="FAILED", details=f"{command}: {exc}")
        return {"error": f"SSH failed: {type(exc).__name__}: {exc}"}


TOOLS = {
    "list_devices": (tool_list_devices, "List devices in NCM with vendor, model, site and last backup time.",
                     {"site": ("STRING", "optional site filter"), "vendor": ("STRING", "optional: huawei, juniper or arista")}, []),
    "backup_status": (tool_backup_status, "Backup health: last attempt, status, error and last successful backup, for one device or all.",
                      {"device": ("STRING", "optional hostname")}, []),
    "recent_changes": (tool_recent_changes, "Configuration changes found between consecutive backups in the last N days, with diff lines. "
                       "Use for questions like 'what changed', 'which VLAN was added', 'who changed what this week'.",
                       {"device": ("STRING", "optional hostname"), "days": ("INTEGER", "look-back period, default 7"),
                        "limit": ("INTEGER", "max changes, default 10")}, []),
    "get_diff": (tool_get_diff, "Full diff between two backups of a device. Without ids, compares the latest two backups.",
                 {"device": ("STRING", "hostname"), "backup_a": ("INTEGER", "optional older backup id"),
                  "backup_b": ("INTEGER", "optional newer backup id")}, ["device"]),
    "search_config": (tool_search_config, "Case-insensitive text search in the latest backup of every device (or one device). "
                      "Use for 'which devices have X', 'where is VLAN 40', 'which port has description Y'.",
                      {"text": ("STRING", "text to find, e.g. 'vlan 40' or 'snmp'"), "device": ("STRING", "optional hostname"),
                       "max_results": ("INTEGER", "default 80")}, ["text"]),
    "get_config": (tool_get_config, "Latest backed-up configuration of a device, optionally only blocks/lines containing a keyword "
                   "(e.g. 'interface GigabitEthernet0/0/1', 'bgp', 'vlan').",
                   {"device": ("STRING", "hostname"), "section": ("STRING", "optional keyword")}, ["device"]),
    "run_show_command": (tool_run_show_command, "LIVE: run one read-only command on the device over SSH. Huawei uses 'display ...', "
                         "Juniper and Arista use 'show ...'. Use only when the question needs current state (interface up/down, "
                         "logs, uptime, MAC/ARP, neighbours) that backups cannot answer.",
                         {"device": ("STRING", "hostname"), "command": ("STRING", "the read-only command")}, ["device", "command"]),
}


def _declarations(live: bool):
    decls = []
    for name, (_, desc, props, req) in TOOLS.items():
        if name == "run_show_command" and not live:
            continue
        decls.append({"name": name, "description": desc, "parameters": {
            "type": "object",
            "properties": {k: {"type": t.lower(), "description": d} for k, (t, d) in props.items()},
            "required": req,
        }})
    return decls


SYSTEM = """You are the NCM assistant for a network operations team. NCM backs up configurations of {vendors} devices over SSH.
Today is {today} (local time, UTC{tzs}). All times you get from tools are already local time.

How to work:
- Always use the tools to get facts. Never guess device names, VLANs, ports, times or commands.
- For "what changed" or "which VLAN/port/command was added or removed", use recent_changes or get_diff and read the '+' and '-' lines.
- For "which devices have ...", use search_config.
- {live_rule}
- Secrets are shown as <MASKED>; never ask for them. If addresses look like IP-1, IP-2, keep them as they are.
- Answer briefly and concretely: device, backup time, the exact config lines. Use short lists or a small table when it helps.
- Reply in the same language and style the user writes in (English, Malayalam, or Malayalam written in English letters).
- You can only read. If asked to change a device, explain the commands but say NCM will not run them.
- If data is missing (no backups, device unknown), say so plainly.

Useful live commands: Huawei: display interface brief, display logbuffer | include DOWN, display vlan, display mac-address, display ip interface brief, display device, display version.
Juniper: show interfaces terse, show log messages | match LINK_ | last 30, show vlans, show system uptime.
Arista: show interfaces status, show logging last 24 hours | include LINEPROTO, show vlan, show version."""


def chat(db, user, message: str, history: list[dict], tz_offset_min: int = 0, can_live: bool = False) -> dict:
    live = can_live and store.get(db, K_LIVE, "0") == "1"
    masker = ai.IPMasker() if store.get(db, ai.K_MASK_IPS, "0") == "1" else None
    ctx = Ctx(db, user, tz_offset_min, live, masker)
    cache: dict = {}      # tool results reused if we fall back to another provider

    vendors = sorted({(d.vendor or "").title() for d in db.query(Device).all()}) or ["Huawei", "Juniper", "Arista"]
    now_local = utcnow() + timedelta(minutes=tz_offset_min)
    sign = "+" if tz_offset_min >= 0 else "-"
    tzs = f"{sign}{abs(tz_offset_min) // 60}:{abs(tz_offset_min) % 60:02d}"
    system = SYSTEM.format(
        vendors=", ".join(vendors), today=now_local.strftime("%A %d %B %Y, %H:%M"), tzs=tzs,
        live_rule=("For current state (port down, interface status, logs, uptime) use run_show_command with a read-only command."
                   if live else
                   "Live device commands are NOT available. For current state questions (port down now, logs), answer from backups "
                   "if possible and say that an admin can enable live commands on the AI review page."))

    base = []
    for h in history[-MAX_HISTORY:]:
        text = str(h.get("text", ""))[:6000]
        if text:
            base.append({"role": "assistant" if h.get("role") == "assistant" else "user",
                         "content": ctx.mask(text) if masker else text})
    base.append({"role": "user", "content": ctx.mask(message) if masker else message})
    tools = _declarations(live)

    def loop(conn, model, provider):
        ctx.steps = []
        msgs = list(base)
        used = model
        for _ in range(MAX_TOOL_ROUNDS + 1):
            res = llm.chat(conn, model, system, msgs, tools=tools, max_tokens=8192)
            used = res["model"]
            if not res["tool_calls"]:
                text = res["text"] or "Sorry, I could not produce an answer."
                return (masker.unmask(text) if masker else text), used
            msgs.append(res["assistant"])
            for call in res["tool_calls"]:
                name, args = call.get("name"), call.get("args") or {}
                key = (name, json_key(args))
                if key in cache:
                    result = cache[key]
                else:
                    fn = TOOLS.get(name, (None,))[0]
                    if not fn or (name == "run_show_command" and not live):
                        result = {"error": f"unknown or unavailable tool {name}"}
                    else:
                        try:
                            result = fn(ctx, **{k: v for k, v in args.items() if k in TOOLS[name][2]})
                        except Exception as exc:
                            result = {"error": f"{type(exc).__name__}: {exc}"}
                    cache[key] = result
                ctx.steps.append({"tool": name, "args": masker.unmask(args) if masker else args, "ok": "error" not in result})
                msgs.append({"role": "tool", "tool_call_id": call.get("id") or name, "name": name, "content": result})
        return "That needed too many lookups. Please ask a more specific question.", used

    reply, provider, model = ai_providers.run(db, user, loop)
    return {"reply": reply, "steps": ctx.steps, "live_enabled": live, "provider": provider.name, "model": model}


def json_key(args: dict) -> str:
    import json
    try:
        return json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(args)
