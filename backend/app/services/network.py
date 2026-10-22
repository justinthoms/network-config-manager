# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

from netmiko import ConnectHandler

from app.core.crypto import decrypt_secret

SUPPORTED = {
    "huawei": "huawei",
    "juniper": "juniper_junos",
    "arista": "arista_eos",
}


def test_connection(device):
    vendor = (device.vendor or "").lower()

    if vendor not in SUPPORTED:
        return {
            "success": False,
            "message": f"Unsupported vendor: {vendor}",
        }

    conn = None
    try:
        conn = ConnectHandler(
            device_type=SUPPORTED[vendor],
            host=device.management_ip,
            username=device.username,
            password=decrypt_secret(device.password_encrypted),
            port=device.ssh_port,
            timeout=30,
            conn_timeout=30,
            auth_timeout=30,
            banner_timeout=30,
            fast_cli=False,
        )

        prompt = conn.find_prompt()

        # Arista backups require privileged EXEC mode, so test that privilege
        # here as well. Huawei and Juniper do not use this enable workflow.
        if vendor == "arista":
            conn.enable()
            prompt = conn.find_prompt()

        return {
            "success": True,
            "message": "SSH connection successful",
            "prompt": prompt,
            "vendor": vendor,
            "host": device.management_ip,
            "port": device.ssh_port,
        }

    except Exception as exc:
        return {
            "success": False,
            "message": f"{type(exc).__name__}: {str(exc) or repr(exc)}",
            "vendor": vendor,
            "host": device.management_ip,
            "port": device.ssh_port,
        }

    finally:
        if conn is not None:
            try:
                conn.disconnect()
            except Exception:
                pass


# ───────────────────────── read-only live commands ─────────────────────────
import re as _re

_READ_VERB = {"huawei": ("display",), "juniper": ("show",), "arista": ("show",)}
_PIPE_OK = {"match", "except", "count", "last", "no-more", "display", "find", "include", "exclude",
            "begin", "section", "i", "e", "b", "s", "inc", "exc", "grep", "json", "nz", "trim"}
_BLOCKED = _re.compile(r"\b(reload|reboot|reset|delete|erase|format|write|save|copy|request|configure|"
                       r"system-view|commit|clear|debug|undo|shutdown|restart|start|rollback|load|"
                       r"terminal\s+monitor|monitor|ping|traceroute|telnet|ssh|scp|ftp|tftp|bash|"
                       r"file|set|edit|enable)\b", _re.IGNORECASE)


class LiveCommandError(ValueError):
    pass


def validate_show_command(vendor: str, command: str) -> str:
    cmd = " ".join((command or "").split())
    vendor = (vendor or "").lower()
    if not cmd or len(cmd) > 200:
        raise LiveCommandError("Command is empty or too long")
    if any(ch in cmd for ch in (";", ">", "`", "$(", "\n", "&&")):
        raise LiveCommandError("Command contains characters that are not allowed")
    verbs = _READ_VERB.get(vendor)
    if not verbs:
        raise LiveCommandError(f"Unsupported vendor: {vendor}")
    parts = [p.strip() for p in cmd.split("|")]
    if parts[0].split()[0].lower() not in verbs:
        raise LiveCommandError(f"Only read-only '{verbs[0]}' commands are allowed on {vendor}")
    # 'display'/'show' themselves are fine; check the rest of the main command
    if _BLOCKED.search(" ".join(parts[0].split()[1:])):
        raise LiveCommandError("This command is not allowed")
    for p in parts[1:]:
        if not p or p.split()[0].lower() not in _PIPE_OK:
            raise LiveCommandError(f"Output filter '| {p}' is not allowed")
    return cmd


def run_show_command(device, command: str, max_chars: int = 20000) -> str:
    vendor = (device.vendor or "").lower()
    cmd = validate_show_command(vendor, command)
    if vendor == "juniper" and "no-more" not in cmd:
        cmd += " | no-more"
    conn = None
    try:
        conn = ConnectHandler(
            device_type=SUPPORTED[vendor], host=device.management_ip, username=device.username,
            password=decrypt_secret(device.password_encrypted), port=device.ssh_port,
            timeout=30, conn_timeout=30, auth_timeout=30, banner_timeout=30, fast_cli=False,
        )
        if vendor == "huawei":
            conn.send_command("screen-length 0 temporary", read_timeout=10)
        elif vendor == "arista":
            try:
                if not conn.check_enable_mode():
                    conn.enable()
            except Exception:
                pass
            conn.send_command_timing("terminal length 0", read_timeout=10)
        out = conn.send_command(cmd, read_timeout=60)
        return out if len(out) <= max_chars else out[:max_chars] + "\n<OUTPUT TRUNCATED>"
    finally:
        if conn is not None:
            try:
                conn.disconnect()
            except Exception:
                pass


_UNSUPPORTED_RE = _re.compile(
    r"(Error:\s*(Unrecognized|Wrong parameter|Incomplete|Too many)|syntax error|unknown command|"
    r"% ?Invalid input|% ?Incomplete command|% ?Ambiguous command|error: command is not valid)",
    _re.IGNORECASE)


def run_show_commands(device, commands: list[str], max_chars: int = 60000) -> dict:
    """Run several read-only commands in ONE SSH session.

    Returns {"prompt": str, "results": [{"command", "output", "ok", "error"}]}.
    A command the device does not know is reported as ok=False instead of
    failing the whole run. Connection errors raise.
    """
    vendor = (device.vendor or "").lower()
    checked = []
    for c in commands:
        try:
            checked.append((c, validate_show_command(vendor, c), None))
        except LiveCommandError as exc:
            checked.append((c, None, str(exc)))
    conn = None
    try:
        conn = ConnectHandler(
            device_type=SUPPORTED[vendor], host=device.management_ip, username=device.username,
            password=decrypt_secret(device.password_encrypted), port=device.ssh_port,
            timeout=30, conn_timeout=30, auth_timeout=30, banner_timeout=30, fast_cli=False,
        )
        if vendor == "huawei":
            conn.send_command("screen-length 0 temporary", read_timeout=10)
        elif vendor == "arista":
            try:
                if not conn.check_enable_mode():
                    conn.enable()
            except Exception:
                pass
            conn.send_command_timing("terminal length 0", read_timeout=10)
        prompt = conn.find_prompt()
        results = []
        for original, cmd, err in checked:
            if err:
                results.append({"command": original, "output": "", "ok": False, "error": err})
                continue
            run = cmd + " | no-more" if vendor == "juniper" and "no-more" not in cmd else cmd
            try:
                out = conn.send_command(run, read_timeout=90)
            except Exception as exc:
                results.append({"command": cmd, "output": "", "ok": False,
                                "error": f"{type(exc).__name__}: {exc}"})
                continue
            if len(out) > max_chars:
                out = out[:max_chars] + "\n<OUTPUT TRUNCATED>"
            bad = _UNSUPPORTED_RE.search(out[:400])
            results.append({"command": cmd, "output": out, "ok": not bad,
                            "error": "Command not supported on this device" if bad else None})
        return {"prompt": prompt, "results": results}
    finally:
        if conn is not None:
            try:
                conn.disconnect()
            except Exception:
                pass
