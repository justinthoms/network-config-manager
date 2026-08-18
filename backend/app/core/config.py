from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    app_name: str = "Network Config Manager"

    secret_key: str = "CHANGE_ME"
    device_encryption_key: str = ""

    database_url: str = "sqlite:///./ncm.db"
    backup_root: str = "./backups"

    access_token_expire_minutes: int = 480

    default_admin_username: str = "admin"
    default_admin_password: str = "admin123"

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
