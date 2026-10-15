from cryptography.fernet import Fernet
from app.core.config import settings

KEY_HELP = ('DEVICE_ENCRYPTION_KEY in backend/.env is not a valid Fernet key. Generate one with: '
            'python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" '
            'then restart NCM.')


def _fernet():
    if not settings.device_encryption_key:
        raise RuntimeError("DEVICE_ENCRYPTION_KEY is not configured. " + KEY_HELP)
    try:
        return Fernet(settings.device_encryption_key.strip().encode())
    except ValueError as exc:
        raise RuntimeError(KEY_HELP) from exc


def check_key() -> str | None:
    """Return an error message if the encryption key is unusable, else None."""
    try:
        _fernet()
        return None
    except RuntimeError as exc:
        return str(exc)


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()
