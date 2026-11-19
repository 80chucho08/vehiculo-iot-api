import pymysql
from pymysql.cursors import DictCursor

from app.core.config import settings

# RDS tiene sql_mode = NO_ENGINE_SUBSTITUTION (no estricto): un texto demasiado largo se
# trunca y un valor inválido de ENUM se guarda como '' con solo un aviso. Cada sesión de la
# API pide modo estricto para que esos casos fallen en vez de corromper datos (riesgo R15).
# time_zone UTC: las columnas TIMESTAMP se leen y escriben en UTC sin depender del servidor.
SQL_MODE_ESTRICTO = (
    "STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,"
    "ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION"
)
INIT_COMMAND = f"SET SESSION sql_mode = '{SQL_MODE_ESTRICTO}', time_zone = '+00:00'"


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
        init_command=INIT_COMMAND,
    )
