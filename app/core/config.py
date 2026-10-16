from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    db_host: str
    db_port: int = 3306
    db_name: str = "vehiculo_iot"
    db_user: str
    db_password: str
    db_connect_timeout: int = 10

    app_name: str = "API Vehiculo IoT"
    app_env: str = "development"

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
