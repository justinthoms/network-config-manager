from pathlib import Path
from datetime import datetime
import time
from netmiko import ConnectHandler

from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.models.backup import Backup
from app.services.network import SUPPORTED


def _arista_config(conn):
    """
    Arista EOS configuration backup.

    Uses Netmiko timing-based commands instead of prompt-pattern matching.
    This avoids failures when EOS changes the prompt during enable/config
    transitions or when the running-config output is large.
    """

    try:
        # Make sure we are in privileged EXEC mode.
        try:
            if not conn.check_enable_mode():
                conn.enable()
        except Exception:
            # Some Arista users are already privileged and do not require
            # an enable password.
            pass

        # Disable terminal paging.
        conn.send_command_timing(
            "terminal length 0",
            read_timeout=10,
        )

        # Small pause so EOS applies terminal setting.
        time.sleep(0.5)

        # Get the complete running configuration.
        output = conn.send_command_timing(
            "show running-config | no-more",
            read_timeout=60,
            last_read=3.0,
        )

        if not output:
            raise RuntimeError("Arista returned empty configuration output")

        # Remove obvious CLI echo/prompt noise only.
        lines = output.splitlines()

        cleaned = []
        for line in lines:
            stripped = line.strip()

            if stripped in (
                "show running-config | no-more",
                "terminal length 0",
            ):
                continue

            cleaned.append(line)

        output = "\n".join(cleaned).strip()

        if len(output) < 50:
            raise RuntimeError(
                f"Arista configuration output unexpectedly short: "
                f"{len(output)} bytes"
            )

        return output

    except Exception as exc:
        raise RuntimeError(f"Arista configuration collection failed: {exc}")


def _huawei_config(conn):
    conn.send_command("screen-length 0 temporary", read_timeout=10)
    return conn.send_command(
        "display current-configuration",
        read_timeout=60,
        strip_prompt=False,
        strip_command=False,
    )


def _juniper_config(conn):
    return conn.send_command(
        "show configuration | no-more | display set",
        read_timeout=60,
        strip_prompt=False,
        strip_command=False,
    )


def _get_config(device, conn):
    vendor = device.vendor.lower()
    if vendor == "arista":
        return _arista_config(conn)
    if vendor == "huawei":
        return _huawei_config(conn)
    if vendor == "juniper":
        return _juniper_config(conn)
    raise RuntimeError(f"Unsupported vendor: {vendor}")


def run_backup(device, db):
    record = Backup(device_id=device.id, status="RUNNING")
    db.add(record)
    db.commit()
    conn = None
    try:
        vendor = device.vendor.lower()
        if vendor not in SUPPORTED:
            raise RuntimeError(f"Unsupported vendor: {vendor}")

        password = decrypt_secret(device.password_encrypted)
        conn = ConnectHandler(
            device_type=SUPPORTED[vendor],
            host=device.management_ip,
            username=device.username,
            password=password,
            port=device.ssh_port,
            timeout=30,
            conn_timeout=30,
            auth_timeout=30,
            banner_timeout=30,
            fast_cli=False,
        )

        output = _get_config(device, conn)
        if not output or len(output.strip()) < 20:
            raise RuntimeError("Configuration output is empty or unexpectedly short")
        output = output.strip()

        now = datetime.now()
        directory = Path(settings.backup_root) / f"{now:%Y}" / f"{now:%m}" / f"{now:%d}" / device.hostname
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{now:%Y%m%d_%H%M%S}.conf"
        path.write_text(output + "\n", encoding="utf-8")

        record.status = "SUCCESS"
        record.file_path = str(path)
        db.commit()
        return {"success": True, "backup_id": record.id, "file_path": str(path), "bytes": len(output.encode("utf-8"))}
    except Exception as exc:
        record.status = "FAILED"
        record.error = str(exc)
        db.commit()
        return {"success": False, "backup_id": record.id, "error": str(exc)}
    finally:
        if conn is not None:
            try:
                conn.disconnect()
            except Exception:
                pass
