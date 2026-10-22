"""Device health checks: read-only show/display commands, parsed into a status.

A check runs a short list of read-only commands in one SSH session
(``display`` on Huawei, ``show`` on Juniper and Arista; every command goes
through the same validator as the assistant's live commands), then parses:

* facts: model, software version, uptime, CPU %, memory %
* interfaces: up / down / admin-down counts, ports with errors or err-disabled
* hardware: fan, power, temperature and active alarms
* logs: every log line with its syslog level (0 emergency ... 7 debug)

The result is OK, Warning, Critical, or Failed (SSH did not work). Parsing is
best effort: anything NCM cannot read is still kept as raw output.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from app.services import settings_store as store
from app.core.timeutil import utcnow

logger = logging.getLogger("ncm.health")

LEVELS = ["emergency", "alert", "critical", "error", "warning", "notice", "info", "debug"]
STATUSES = ["ok", "warning", "critical", "failed"]
STATUS_RANK = {"ok": 0, "warning": 1, "critical": 2, "failed": 3}

DEFAULT_COMMANDS = {
    "huawei": [
        "display version",
        "display cpu-usage",
        "display memory-usage",
        "display device",
        "display fan",
        "display power",
        "display temperature all",
        "display alarm active",
        "display interface brief",
        "display logbuffer",
    ],
    "juniper": [
        "show version",
        "show chassis routing-engine",
        "show chassis alarms",
        "show system alarms",
        "show chassis environment",
        "show interfaces terse",
        "show log messages | last 500",
    ],
    "arista": [
        "show version",
        "show processes top once",
        "show system environment all",
        "show interfaces status",
        "show interfaces counters errors",
        "show logging 500",
    ],
}

K_SCHED = "health.schedule_enabled"
K_INTERVAL = "health.interval_minutes"
K_RETENTION = "health.retention"
K_AI_MODE = "health.ai_mode"            # off | issues | all
K_AI_LANG = "health.ai_language"        # English | Malayalam
K_WINDOW = "health.log_window_hours"
K_CMDS = "health.commands."             # + vendor, JSON list
K_LAST_RUN = "health.last_run"          # JSON summary of the last "check all"

CPU_WARN, CPU_CRIT = 75, 90
MEM_WARN, MEM_CRIT = 80, 90
MAX_LOGS = 600

JOB_ID = "health-all"
_run_state = {"running": False, "total": 0, "done": 0, "started_at": None, "trigger": None}
_run_lock = threading.Lock()


# ───────────────────────── settings ─────────────────────────

def _int(db, key, default, lo, hi):
    try:
        return max(lo, min(hi, int(store.get(db, key, default))))
    except (TypeError, ValueError):
        return default


def commands_for(db, vendor: str) -> list[str]:
    vendor = (vendor or "").lower()
    custom = store.get_json(db, K_CMDS + vendor)
    return custom if isinstance(custom, list) and custom else DEFAULT_COMMANDS.get(vendor, [])


def settings_view(db) -> dict:
    return {
        "schedule_enabled": store.get(db, K_SCHED, "0") == "1",
        "interval_minutes": _int(db, K_INTERVAL, 360, 15, 10080),
        "retention": _int(db, K_RETENTION, 50, 1, 1000),
        "ai_mode": store.get(db, K_AI_MODE, "issues"),
        "ai_language": store.get(db, K_AI_LANG, "English"),
        "log_window_hours": _int(db, K_WINDOW, 24, 1, 720),
        "commands": {v: commands_for(db, v) for v in DEFAULT_COMMANDS},
        "custom_commands": {v: bool(store.get(db, K_CMDS + v)) for v in DEFAULT_COMMANDS},
        "default_commands": DEFAULT_COMMANDS,
        "thresholds": {"cpu_warn": CPU_WARN, "cpu_crit": CPU_CRIT, "mem_warn": MEM_WARN, "mem_crit": MEM_CRIT},
    }


def run_state() -> dict:
    return dict(_run_state)


# ───────────────────────── log parsing ─────────────────────────

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

_TS_RE = re.compile(
    r"^\s*(?P<ts>"
    r"(?P<y1>\d{4})-(?P<mo1>\d\d)-(?P<d1>\d\d)[ T](?P<t1>\d\d:\d\d:\d\d)(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)?"
    r"|(?P<y2>\d{4})\s+(?P<mn2>[A-Za-z]{3})\s+(?P<d2>\d{1,2})\s+(?P<t2>\d\d:\d\d:\d\d)(?:\.\d+)?"
    r"|(?P<mn3>[A-Za-z]{3})\s+(?P<d3>\d{1,2})(?:\s+(?P<y3>\d{4}))?\s+(?P<t3>\d\d:\d\d:\d\d)(?:\.\d+)?(?:[+-]\d\d:\d\d)?"
    r")(?:\s+(?:DST|UTC|[A-Z]{3,4}))?\s+")
# Huawei: %%01IFNET/4/LINK_STATE(l)[12]:msg   or  IFNET/4/LINK_STATE(l):CID=..;msg
_HW_RE = re.compile(r"(?:%%\d{2})?(?P<mod>[A-Z][A-Z0-9_\-]*)/(?P<lvl>[0-7])/(?P<tag>[A-Za-z0-9_\-]+)"
                    r"(?:\([^)]*\))?(?:\[\d+\])?\s*:\s*(?P<msg>.*)")
# Arista / Cisco style, and Junos with explicit-priority: %LINEPROTO-5-UPDOWN: msg
_SYS_RE = re.compile(r"%(?P<mod>[A-Z][A-Z0-9_]*)-(?P<lvl>[0-7])-(?P<tag>[A-Z0-9_]+)\s*:\s*(?P<msg>.*)")
# Junos default format: host process[pid]: TAG: msg
_JN_RE = re.compile(r"(?P<mod>[\w\-./]+)(?:\[\d+\])?:\s+(?P<tag>[A-Z][A-Z0-9_]{3,}):\s*(?P<msg>.*)")
_JN_PLAIN = re.compile(r"(?P<mod>[\w\-./]+)(?:\[\d+\])?:\s+(?P<msg>.+)")

_INFER = [
    (2, re.compile(r"PANIC|CRASH|CORE[_ -]?DUMP|FATAL|OVERHEAT|OVER[_ -]?TEMP|TEMP\w*_(HIGH|CRIT)|FAN\w*_?FAIL|"
                   r"POWER\w*_?FAIL|PSU\w*_?FAIL|FPC\w*_?(OFFLINE|FAULT)|HARDWARE_?FAIL|KERNEL\w*FAIL", re.I)),
    (3, re.compile(r"ERR(OR)?\b|_ERR|FAIL|DENIED|REJECT|INVALID|ABNORMAL|UNREACHABLE|LOSS\b", re.I)),
    (4, re.compile(r"DOWN|FLAP|THRESHOLD|EXCEED|HIGH|TIMEOUT|RETRY|BLOCK|LOOP|DUPLICATE|CONFLICT|STORM|MISMATCH", re.I)),
    (5, re.compile(r"\bUP\b|_UP\b|CHANGE|COMMIT|LOGIN|LOGOUT|CONFIG|RESTART|REBOOT", re.I)),
]


def _infer_level(text: str) -> int:
    for lvl, rx in _INFER:
        if rx.search(text):
            return lvl
    return 6


def _parse_ts(m, now: datetime) -> datetime | None:
    try:
        g = m.groupdict()
        if g["y1"]:
            return datetime.strptime(f"{g['y1']}-{g['mo1']}-{g['d1']} {g['t1']}", "%Y-%m-%d %H:%M:%S")
        if g["y2"]:
            mon, day, year, t = g["mn2"], g["d2"], int(g["y2"]), g["t2"]
        else:
            mon, day, t = g["mn3"], g["d3"], g["t3"]
            year = int(g["y3"]) if g["y3"] else now.year
        dt = datetime(year, _MONTHS[mon.lower()], int(day), *map(int, t.split(":")))
        if not (g.get("y2") or g.get("y3")) and dt > now + timedelta(days=2):
            dt = dt.replace(year=year - 1)   # syslog without a year: "Dec 31" seen in January
        return dt
    except (KeyError, ValueError, TypeError):
        return None


def parse_logs(text: str, window_hours: int = 24, now: datetime | None = None) -> list[dict]:
    """Turn a log buffer into [{ts, time, level, level_name, module, tag, msg, inferred, recent}]."""
    now = now or datetime.now()
    out: list[dict] = []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        tm = _TS_RE.match(line)
        rest = line[tm.end():] if tm else line
        m = _HW_RE.search(rest) or _SYS_RE.search(rest)
        inferred = False
        if m:
            lvl, mod, tag, msg = int(m["lvl"]), m["mod"], m["tag"], m["msg"]
        elif tm:
            body = rest.split(None, 1)[1] if len(rest.split(None, 1)) > 1 else rest   # drop hostname
            jm = _JN_RE.match(body)
            if jm:
                mod, tag, msg = jm["mod"], jm["tag"], jm["msg"]
            else:
                pm = _JN_PLAIN.match(body)
                mod, tag, msg = (pm["mod"], "", pm["msg"]) if pm else ("", "", body)
            lvl, inferred = _infer_level(f"{tag} {msg}"), True
        else:
            # Continuation of the previous message (Huawei wraps long lines), or a header line.
            if out and not re.match(r"^\s*(Logging buffer|Channel number|Dropped messages|Overwritten|"
                                    r"Current messages|Log buffer|Syslog logging|\S+\s+logging:|-{5,})", line, re.I):
                out[-1]["msg"] = (out[-1]["msg"] + " " + line.strip())[:2000]
            continue
        dt = _parse_ts(tm, now) if tm else None
        out.append({
            "time": tm["ts"].strip() if tm else "",
            "ts": dt.isoformat() if dt else None,
            "level": lvl, "level_name": LEVELS[lvl],
            "module": mod, "tag": tag, "msg": msg.strip()[:2000],
            "inferred": inferred,
            "recent": bool(dt and now - dt <= timedelta(hours=window_hours) and dt <= now + timedelta(days=1)),
        })
    if len(out) > MAX_LOGS:
        # keep the newest; buffers are newest-first (Huawei) or oldest-first (Junos, EOS)
        newest_first = len(out) > 1 and out[0]["ts"] and out[-1]["ts"] and out[0]["ts"] > out[-1]["ts"]
        out = out[:MAX_LOGS] if newest_first else out[-MAX_LOGS:]
    return out


# ───────────────────────── output parsing ─────────────────────────

def _num(rx, text, flags=re.I | re.M):
    m = re.search(rx, text or "", flags)
    return float(m.group(1)) if m else None


def _str(rx, text, flags=re.I | re.M):
    m = re.search(rx, text or "", flags)
    return m.group(1).strip() if m else None


def _role(cmd: str) -> set[str]:
    c = cmd.lower()
    roles = set()
    if re.search(r"\bversion\b", c):
        roles |= {"version", "memory"}
    if re.search(r"cpu|routing-engine|processes top", c):
        roles |= {"cpu", "memory", "uptime"}
    if "memory" in c:
        roles.add("memory")
    if re.search(r"\binterfaces?\b", c):
        roles.add("errors" if "error" in c else "interfaces")
    if re.search(r"logbuffer|\blog\b|\blogging\b", c):
        roles.add("logs")
    if "alarm" in c:
        roles.add("alarms")
    if re.search(r"\bdevice\b|\bfan\b|\bpower\b|temperature|environment", c):
        roles.add("env")
    return roles


def _cpu(vendor, text):
    v = _num(r"CPU [Uu]sage\s*(?:is)?\s*:?\s*(\d+(?:\.\d+)?)\s*%", text)
    if v is None:
        v = _num(r"CPU utilization for five seconds:\s*(\d+(?:\.\d+)?)%", text)
    if v is None:
        idle = _num(r"^\s*Idle\s+(\d+)\s+percent", text)                       # Junos RE
        if idle is None:
            idle = _num(r"%?Cpu\(s\):.*?(\d+(?:\.\d+)?)\s*id\b", text)        # EOS top
        v = None if idle is None else 100 - idle
    return None if v is None else int(round(v))


def _memory(vendor, text):
    v = _num(r"Memory Using Percentage(?:\s+Is)?\s*:?\s*(\d+(?:\.\d+)?)\s*%", text)
    if v is None:
        v = _num(r"Memory utilization\s+(\d+)\s+percent", text)
    if v is None:
        total = _num(r"Total memory:\s*(\d+)\s*kB", text)
        free = _num(r"Free memory:\s*(\d+)\s*kB", text)
        if total and free is not None:
            v = (total - free) * 100 / total
    return None if v is None else int(round(v))


def _facts(vendor, text, facts):
    facts.setdefault("version", _str(r"VRP \(R\) software,\s*Version\s+([^\n]+)", text)
                     or _str(r"^Junos:\s*(\S+)", text) or _str(r"JUNOS [^\n]*?\[([^\]]+)\]", text)
                     or _str(r"Software image version:\s*(\S+)", text))
    facts.setdefault("model", _str(r"^\s*(?:HUAWEI|Huawei)\s+(\S+)[^\n]*uptime is", text)
                     or _str(r"^Model:\s*(\S+)", text) or _str(r"^Arista\s+(\S+)", text))
    facts.setdefault("uptime", _str(r"uptime is\s+([^\n]+)", text) or _str(r"^Uptime:\s*([^\n]+)", text)
                     or _str(r"^\s*Uptime\s+([^\n]+)", text))
    for k in [k for k, v in facts.items() if v is None]:
        facts.pop(k)


_HW_SKIP = re.compile(r"^(NULL|LoopBack|InLoopBack|Vlanif|Tunnel|Virtual|Nve|MEth|Ethernet0/0/0$)|\.\d+$", re.I)


def _interfaces(vendor, text, intf):
    for line in (text or "").splitlines():
        t = line.split()
        if len(t) < 3:
            continue
        name = t[0]
        if vendor == "huawei":
            if len(t) < 5 or not re.match(r"^[*^]?(up|down)", t[1], re.I):
                continue
            phy = t[1].lower()
            in_err = int(t[-2]) if len(t) >= 7 and t[-2].isdigit() else 0
            out_err = int(t[-1]) if len(t) >= 7 and t[-1].isdigit() else 0
            if in_err or out_err:
                intf["errors"].append({"name": name, "in": in_err, "out": out_err})
            if _HW_SKIP.search(name):
                continue
            state = "admin_down" if phy.startswith("*") else "up" if phy.startswith("up") else \
                "standby" if phy.startswith("^") else "down"
        elif vendor == "juniper":
            if "." in name or not re.match(r"^(ge|xe|et|fe|mge|ae|xle|me|em)-?\d", name):
                continue
            if t[1] not in ("up", "down"):
                continue
            state = "admin_down" if t[1] == "down" else "up" if t[2] == "up" else "down"
        else:  # arista show interfaces status
            if not re.match(r"^(Et|Po|Ma)\S*\d", name):
                continue
            m = re.search(r"\s(connected|notconnect|disabled|errdisabled|err-disabled|inactive|sfpAbsent|"
                          r"monitoring|xcvrAbsent|noOperMem)\s", f" {line} ")
            if not m:
                continue
            st = m.group(1).lower()
            state = {"connected": "up", "disabled": "admin_down", "errdisabled": "err_disabled",
                     "err-disabled": "err_disabled", "monitoring": "up"}.get(st, "down")
        intf["counts"][state] = intf["counts"].get(state, 0) + 1
        if state == "err_disabled":
            intf["err_disabled"].append(name)
        elif state == "down":
            intf["down"].append(name)


def _arista_errors(text, intf):
    for line in (text or "").splitlines():
        t = line.split()
        if len(t) >= 3 and re.match(r"^(Et|Po|Ma)\S*\d", t[0]) and all(x.isdigit() for x in t[1:]):
            total = sum(int(x) for x in t[1:])
            if total:
                intf["errors"].append({"name": t[0], "in": total, "out": 0})


_ENV_CRIT = re.compile(r"\b(fail(?:ed|ure)?|faulty|fault|abnormal|critical|overheat(?:ed)?|shut ?down|"
                       r"over[- ]?temp\w*|power ?loss|no ?power|not ?powered)\b", re.I)
_HEADER_WORDS = re.compile(r"\b(Description|Alert|Threshold|Measurement|Status|Slot|Sensor|Model|Capacity|"
                           r"Speed|Mode|Present|Registered|Item|Class|State|Temperature|Critical|Power\(W\))\b", re.I)
_ENV_WARN = re.compile(r"\b(check|warning|minor|not ok|high)\b", re.I)
_ENV_HEADER = re.compile(r"(Status\s*$|^\s*-{3,}|Severity|^\s*Slot\s|^\s*Item\s|^\s*Class\s)", re.I)


def _env_issues(text, issues, source):
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or _ENV_HEADER.search(s):
            continue
        # column header rows: no digits and several header words
        if not re.search(r"\d", s) and len(set(w.lower() for w in _HEADER_WORDS.findall(s))) >= 2:
            continue
        if _ENV_CRIT.search(s) and not re.search(r"\bno\s+(fail|fault)|\b0\s+(failures|faults)\b", s, re.I):
            issues.append({"severity": "critical", "area": "hardware", "text": f"{source}: {s}"[:300]})
        elif _ENV_WARN.search(s):
            issues.append({"severity": "warning", "area": "hardware", "text": f"{source}: {s}"[:300]})


def _alarm_issues(text, issues):
    if re.search(r"No alarms currently active|No (active )?alarm", text or "", re.I):
        return
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or re.search(r"Severity|Level|Sequence|^\s*-{3,}|alarms? currently active|Alarm time", s, re.I):
            continue
        if re.search(r"\b(Critical|Major)\b", s):
            issues.append({"severity": "critical", "area": "alarm", "text": s[:300]})
        elif re.search(r"\b(Minor|Warning)\b", s):
            issues.append({"severity": "warning", "area": "alarm", "text": s[:300]})


def analyse(vendor: str, results: list[dict], window_hours: int = 24, now: datetime | None = None) -> dict:
    """Parse command outputs. Returns {facts, cpu, memory, interfaces, issues, logs, log_counts}."""
    vendor = (vendor or "").lower()
    facts: dict = {}
    cpu = mem = None
    intf = {"counts": {}, "down": [], "err_disabled": [], "errors": []}
    issues: list[dict] = []
    logs: list[dict] = []
    for r in results:
        if not r.get("ok") or not r.get("output"):
            continue
        text, roles = r["output"], _role(r["command"])
        if "version" in roles or "uptime" in roles:
            _facts(vendor, text, facts)
        if "cpu" in roles and cpu is None:
            cpu = _cpu(vendor, text)
        if "memory" in roles and mem is None:
            mem = _memory(vendor, text)
        if "interfaces" in roles:
            _interfaces(vendor, text, intf)
        if "errors" in roles:
            _arista_errors(text, intf)
        if "alarms" in roles:
            _alarm_issues(text, issues)
        if "env" in roles:
            _env_issues(text, issues, r["command"].split(None, 1)[-1])
        if "logs" in roles:
            logs.extend(parse_logs(text, window_hours, now))

    if cpu is not None:
        if cpu >= CPU_CRIT:
            issues.append({"severity": "critical", "area": "cpu", "text": f"CPU usage is {cpu}%"})
        elif cpu >= CPU_WARN:
            issues.append({"severity": "warning", "area": "cpu", "text": f"CPU usage is {cpu}%"})
    if mem is not None:
        if mem >= MEM_CRIT:
            issues.append({"severity": "critical", "area": "memory", "text": f"Memory usage is {mem}%"})
        elif mem >= MEM_WARN:
            issues.append({"severity": "warning", "area": "memory", "text": f"Memory usage is {mem}%"})
    if intf["err_disabled"]:
        issues.append({"severity": "warning", "area": "interfaces",
                       "text": f"{len(intf['err_disabled'])} err-disabled port(s): " + ", ".join(intf["err_disabled"][:12])})
    if intf["errors"]:
        top = sorted(intf["errors"], key=lambda e: -(e["in"] + e["out"]))[:8]
        issues.append({"severity": "warning", "area": "interfaces",
                       "text": f"{len(intf['errors'])} port(s) with error counters: "
                               + ", ".join(f"{e['name']} ({e['in'] + e['out']})" for e in top)})

    counts = {name: 0 for name in LEVELS}
    recent = {name: 0 for name in LEVELS}
    for e in logs:
        counts[e["level_name"]] += 1
        if e["recent"]:
            recent[e["level_name"]] += 1
    severe = recent["emergency"] + recent["alert"] + recent["critical"]
    if severe:
        issues.append({"severity": "critical", "area": "logs",
                       "text": f"{severe} emergency/alert/critical log message(s) in the last {window_hours} h"})
    if recent["error"]:
        issues.append({"severity": "warning", "area": "logs",
                       "text": f"{recent['error']} error log message(s) in the last {window_hours} h"})

    # de-duplicate, critical first
    seen, uniq = set(), []
    for i in issues:
        if i["text"] not in seen:
            seen.add(i["text"])
            uniq.append(i)
    uniq.sort(key=lambda i: 0 if i["severity"] == "critical" else 1)
    return {"facts": facts, "cpu": cpu, "memory": mem, "interfaces": intf, "issues": uniq[:60],
            "logs": logs, "log_counts": counts, "recent_log_counts": recent, "log_window_hours": window_hours}


def status_of(analysis: dict) -> str:
    sev = {i["severity"] for i in analysis["issues"]}
    return "critical" if "critical" in sev else "warning" if "warning" in sev else "ok"


# ───────────────────────── running checks ─────────────────────────

def _serialize(row, hostname=None, full=False) -> dict:
    summary = json.loads(row.summary) if row.summary else {}
    d = {
        "id": row.id, "device_id": row.device_id, "hostname": hostname, "status": row.status,
        "trigger": row.trigger, "cpu": row.cpu, "memory": row.memory, "error": row.error,
        "duration_ms": row.duration_ms, "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "facts": summary.get("facts", {}), "issues": summary.get("issues", []),
        "interfaces": summary.get("interfaces", {}), "log_counts": summary.get("log_counts", {}),
        "recent_log_counts": summary.get("recent_log_counts", {}),
        "log_window_hours": summary.get("log_window_hours"),
        "commands_failed": summary.get("commands_failed", []),
        "has_ai": bool(row.ai),
    }
    if full:
        d["logs"] = json.loads(row.logs) if row.logs else []
        d["outputs"] = json.loads(row.outputs) if row.outputs else []
        d["ai"] = json.loads(row.ai) if row.ai else None
    elif row.ai:
        try:
            d["ai_status"] = json.loads(row.ai).get("status")
        except ValueError:
            pass
    return d


def _retention(db, device_id: int, keep: int):
    from app.models.health import HealthCheck
    old = (db.query(HealthCheck.id).filter(HealthCheck.device_id == device_id)
           .order_by(HealthCheck.id.desc()).offset(keep).all())
    if old:
        db.query(HealthCheck).filter(HealthCheck.id.in_([o.id for o in old])).delete(synchronize_session=False)
        db.commit()


def run_check(db, device, user: str | None = None, trigger: str = "manual"):
    """Run one health check and store it. Never raises for device problems: status 'failed' instead."""
    from app.models.health import HealthCheck
    from app.services.network import run_show_commands, SUPPORTED
    started = time.monotonic()
    window = _int(db, K_WINDOW, 24, 1, 720)
    row = HealthCheck(device_id=device.id, trigger=trigger, created_by=user, status="failed")
    vendor = (device.vendor or "").lower()
    try:
        if vendor not in SUPPORTED:
            raise RuntimeError(f"Unsupported vendor: {device.vendor}")
        cmds = commands_for(db, vendor)
        res = run_show_commands(device, cmds)
        results = res["results"]
        a = analyse(vendor, results, window)
        if not any(r["ok"] for r in results):
            raise RuntimeError("None of the health commands worked on this device. Check the command list "
                               "in Health settings. First error: " + (results[0].get("error") or "unknown"))
        failed = [{"command": r["command"], "error": r["error"]} for r in results if not r["ok"]]
        row.status = status_of(a)
        row.cpu, row.memory = a["cpu"], a["memory"]
        row.summary = json.dumps({k: a[k] for k in ("facts", "interfaces", "issues", "log_counts",
                                                    "recent_log_counts", "log_window_hours")}
                                 | {"commands_failed": failed, "prompt": res.get("prompt")})
        row.logs = json.dumps(a["logs"])
        row.outputs = json.dumps(results)
    except Exception as exc:
        row.status = "failed"
        row.error = f"{type(exc).__name__}: {exc}" if not isinstance(exc, RuntimeError) else str(exc)
        row.summary = json.dumps({"issues": [{"severity": "critical", "area": "ssh", "text": row.error[:300]}]})
    row.duration_ms = int((time.monotonic() - started) * 1000)
    db.add(row)
    db.commit()
    db.refresh(row)
    _retention(db, device.id, _int(db, K_RETENTION, 50, 1, 1000))
    return row


def should_ai(db, status: str) -> bool:
    mode = store.get(db, K_AI_MODE, "issues")
    if mode == "all":
        return status != "failed"
    if mode == "issues":
        return status in ("warning", "critical")
    return False


# ───────────────────────── AI summary ─────────────────────────

AI_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ok", "warning", "critical"]},
        "summary": {"type": "string"},
        "issues": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "severity": {"type": "string", "enum": ["critical", "high", "medium", "low", "info"]},
                "title": {"type": "string"},
                "detail": {"type": "string"},
                "recommendation": {"type": "string"},
                "commands": {"type": "string"},
            },
            "required": ["severity", "title", "detail", "recommendation"],
        }},
        "next_checks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "summary", "issues"],
}

AI_PROMPT = """You are a senior NOC engineer reading the health-check output of a {vendor} device ({model}), hostname {hostname}.
You get parsed facts (CPU, memory, uptime, interfaces, hardware and alarm issues, log counts by syslog level),
the most important log lines, and raw output of read-only commands.

Explain the device's health for an on-shift engineer:
- "status": ok, warning or critical.
- "summary": 2 to 4 sentences: overall state and what needs attention first.
- "issues": real problems you can see, most important first. In "detail" quote the evidence (log line, port, value).
  Group repeated log messages (for example one port flapping 40 times) into one issue.
  In "commands" give the vendor's read-only commands to investigate further; if a fix needs a configuration change, say so in "recommendation" and do not put configuration commands in "commands".
- "next_checks": short list of what to check next, if anything.
Rules: do not invent values that are not in the data. Uptime under one day may mean an unplanned reboot; say so.
Log levels: 0 emergency, 1 alert, 2 critical, 3 error, 4 warning, 5 notice, 6 info, 7 debug. A level marked "inferred" was guessed by NCM from the text (Junos logs have no level by default).
Secrets are masked. Addresses may be replaced with placeholders like IP-1.
Write all text values in {language}. Keep command syntax and log lines as they are."""


def _ai_input(device, check: dict) -> str:
    logs = check.get("logs") or []
    important = [e for e in logs if e["level"] <= 4][:150]
    if len(important) < 40:
        important += [e for e in logs if e["level"] > 4][: 40 - len(important)]
    log_text = "\n".join(
        f"[{e['level_name']}{' inferred' if e.get('inferred') else ''}{' recent' if e.get('recent') else ''}] "
        f"{e.get('time', '')} {e.get('module', '')}/{e.get('tag', '')}: {e['msg'][:400]}" for e in important)
    raw = []
    for o in check.get("outputs") or []:
        if not o.get("ok") or "log" in o["command"]:
            continue
        raw.append(f"### {o['command']}\n{o['output'][:6000]}")
    data = {
        "facts": check.get("facts"), "cpu_percent": check.get("cpu"), "memory_percent": check.get("memory"),
        "interfaces": {"counts": (check.get("interfaces") or {}).get("counts"),
                       "down": (check.get("interfaces") or {}).get("down", [])[:60],
                       "err_disabled": (check.get("interfaces") or {}).get("err_disabled"),
                       "errors": (check.get("interfaces") or {}).get("errors", [])[:40]},
        "ncm_issues": check.get("issues"),
        "log_counts_all": check.get("log_counts"),
        f"log_counts_last_{check.get('log_window_hours') or 24}h": check.get("recent_log_counts"),
        "commands_not_supported": check.get("commands_failed"),
    }
    return (f"Parsed data:\n{json.dumps(data, indent=1)}\n\nImportant log lines:\n{log_text or '(none)'}\n\n"
            f"Raw command output:\n" + "\n\n".join(raw))


def ai_summary(db, row, device, user=None) -> dict:
    from app.services import ai_providers, llm
    from app.services.ai_review import IPMasker, prepare, K_MASK_IPS, _limit
    check = _serialize(row, device.hostname, full=True)
    masker = IPMasker() if store.get(db, K_MASK_IPS, "0") == "1" else None
    language = store.get(db, K_AI_LANG, "English")
    system = AI_PROMPT.format(vendor=device.vendor, model=device.model or check["facts"].get("model") or "unknown model",
                              hostname=device.hostname, language=language)
    raw = _ai_input(device, check)
    state = {}

    def call(conn, model, p):
        text, state["truncated"] = prepare(raw, masker, min(_limit(p), 60_000))
        return llm.complete_json(conn, model, system, text, AI_SCHEMA, max_tokens=8192)

    result, prov, used = ai_providers.run(db, user, call)
    if masker:
        result = masker.unmask(result)
    st = str(result.get("status", "")).lower()
    result["status"] = st if st in ("ok", "warning", "critical") else row.status
    order = ["critical", "high", "medium", "low", "info"]
    items = [i for i in (result.get("issues") or []) if isinstance(i, dict) and i.get("title")]
    for i in items:
        i["severity"] = str(i.get("severity", "info")).lower()
        if i["severity"] not in order:
            i["severity"] = "info"
    items.sort(key=lambda i: order.index(i["severity"]))
    result["issues"] = items
    result.update(_provider=prov.name, _model=used, language=language,
                  truncated=state.get("truncated", False), masked_ips=bool(masker),
                  created_at=utcnow().isoformat())
    row.ai = json.dumps(result)
    db.commit()
    return result


# ───────────────────────── check all / schedule ─────────────────────────

def _check_device_id(device_id: int, trigger: str, user: str | None) -> tuple[int | None, str]:
    from app.core.database import SessionLocal
    from app.models.device import Device
    db = SessionLocal()
    try:
        device = db.get(Device, device_id)
        if not device:
            return None, "failed"
        row = run_check(db, device, user, trigger)
        return row.id, row.status
    except Exception:
        logger.exception("Health check crashed for device %s", device_id)
        return None, "failed"
    finally:
        db.close()
        with _run_lock:
            _run_state["done"] += 1


def _ai_after(check_ids: list[int]):
    from app.core.database import SessionLocal
    from app.models.device import Device
    from app.models.health import HealthCheck
    from app.services.ai_review import is_configured
    db = SessionLocal()
    try:
        if not is_configured(db):
            return
        for cid in check_ids:
            row = db.get(HealthCheck, cid)
            if not row or row.ai or not should_ai(db, row.status):
                continue
            try:
                ai_summary(db, row, db.get(Device, row.device_id))
            except Exception as exc:
                logger.warning("AI health summary failed for check %s: %s", cid, exc)
            time.sleep(8)   # stay under free-tier per-minute limits
    finally:
        db.close()


def run_all(trigger: str = "scheduled", user: str | None = None, workers: int = 4) -> bool:
    """Check every enabled device in the background. Returns False if a run is already going."""
    from app.core.database import SessionLocal
    from app.models.device import Device
    with _run_lock:
        if _run_state["running"]:
            return False
        db = SessionLocal()
        try:
            ids = [d.id for d in db.query(Device).filter(Device.enabled.is_(True)).order_by(Device.hostname).all()]
        finally:
            db.close()
        _run_state.update(running=True, total=len(ids), done=0,
                          started_at=utcnow().isoformat(), trigger=trigger)

    def _job():
        counts = {s: 0 for s in STATUSES}
        check_ids = []
        try:
            with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="ncm-health") as pool:
                for cid, st in pool.map(lambda i: _check_device_id(i, trigger, user), ids):
                    counts[st] = counts.get(st, 0) + 1
                    if cid:
                        check_ids.append(cid)
            db2 = SessionLocal()
            try:
                store.set_json(db2, K_LAST_RUN, {"finished_at": utcnow().isoformat(), "trigger": trigger,
                                                 "devices": len(ids), "counts": counts})
            finally:
                db2.close()
        finally:
            with _run_lock:
                _run_state["running"] = False
        _ai_after(check_ids)

    threading.Thread(target=_job, name="ncm-health-all", daemon=True).start()
    return True


_applied_job: dict = {"sig": None}


def run_scheduled():
    from app.services.scheduler import is_leader
    if is_leader():          # with several worker processes, only one runs the checks
        run_all("scheduled")


def schedule_job(db=None, only_if_changed: bool = False):
    """(Re)register the 'check all devices' job from the saved settings."""
    from app.services.scheduler import scheduler
    from apscheduler.triggers.interval import IntervalTrigger
    own = db is None
    if own:
        from app.core.database import SessionLocal
        db = SessionLocal()
    try:
        enabled = store.get(db, K_SCHED, "0") == "1"
        minutes = _int(db, K_INTERVAL, 360, 15, 10080)
    finally:
        if own:
            db.close()
    if only_if_changed and _applied_job["sig"] == (enabled, minutes):
        return
    _applied_job["sig"] = (enabled, minutes)
    if scheduler.get_job(JOB_ID):
        scheduler.remove_job(JOB_ID)
    if enabled:
        scheduler.add_job(run_scheduled, trigger=IntervalTrigger(minutes=minutes), id=JOB_ID, replace_existing=True)


def next_run_at() -> str | None:
    from app.services.scheduler import scheduler
    job = scheduler.get_job(JOB_ID)
    t = getattr(job, "next_run_time", None) if job else None
    return t.isoformat() if t else None
