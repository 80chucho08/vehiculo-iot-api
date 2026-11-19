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

    # ===== Autenticación (4.3) =====
    # Secreto para firmar los tokens (HS256). Generar con:
    #   python -c "import secrets; print(secrets.token_urlsafe(48))"
    # y ponerlo SOLO en el .env de la EC2. Si falta, se genera uno aleatorio al arrancar:
    # funciona, pero todas las sesiones caducan cada vez que se reinicia la API.
    auth_secret: str | None = None
    # Transición: mientras el frontend no tenga inicio de sesión, CONTROL y MONITOREO
    # siguen aceptando peticiones sin token. Con True, todo exige token salvo lo público
    # (salud y catálogo). Las rutas de administración SIEMPRE exigen token de admin.
    auth_obligatoria: bool = False
    token_minutos: int = 30
    sesion_max_horas: int = 8
    login_max_intentos: int = 5
    login_bloqueo_minutos: int = 15

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
