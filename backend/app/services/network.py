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
