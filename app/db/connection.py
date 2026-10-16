import pymysql
from pymysql.cursors import DictCursor

from app.core.config import settings


def get_connection():
    """Abre una conexión a la base de datos vehiculo_iot en RDS."""
    return pymysql.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.db_name,
        connect_timeout=settings.db_connect_timeout,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
    )
