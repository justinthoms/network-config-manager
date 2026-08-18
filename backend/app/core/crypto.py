from cryptography.fernet import Fernet
from app.core.config import settings

def _fernet():
    if not settings.device_encryption_key:
        raise RuntimeError("DEVICE_ENCRYPTION_KEY is not configured")
    return Fernet(settings.device_encryption_key.encode())

def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()

def decrypt_secret(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()
