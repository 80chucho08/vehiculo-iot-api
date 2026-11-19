"""Utilidades compartidas por los scripts de respaldo y migración.

Usan las credenciales del `.env` (nunca se imprimen) y permiten elegir la base
de datos de destino, para trabajar sobre `vehiculo_iot_prueba` sin tocar la real.
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import pymysql  # noqa: E402
from pymysql.cursors import DictCursor  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.connection import INIT_COMMAND, SQL_MODE_ESTRICTO  # noqa: E402,F401

# Tablas originales de 4.1/4.2: ninguna migración puede reducir sus filas.
TABLAS_ORIGINALES = (
    "vehiculos",
    "comandos",
    "estado_vehiculo",
    "historial_comandos",
    "demos",
    "demo_movimientos",
)

BD_PRODUCCION = settings.db_name


def conectar(base_datos: str | None, *, autocommit: bool = False) -> pymysql.connections.Connection:
    """Abre una conexión; `base_datos=None` conecta sin base seleccionada."""
    return pymysql.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        database=base_datos,
        connect_timeout=settings.db_connect_timeout,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=autocommit,
        init_command=INIT_COMMAND,  # mismo modo estricto y UTC que la API
    )


def validar_nombre_bd(nombre: str) -> str:
    """Los nombres de base se interpolan en SQL: solo letras, dígitos y guion bajo."""
    if not nombre or not all(c.isalnum() or c == "_" for c in nombre) or len(nombre) > 64:
        raise SystemExit(f"Nombre de base de datos no válido: {nombre!r}")
    return nombre


def conteos(conexion, tablas=TABLAS_ORIGINALES) -> dict[str, int]:
    resultado = {}
    with conexion.cursor() as cursor:
        for tabla in tablas:
            cursor.execute(f"SELECT COUNT(*) AS n FROM `{tabla}`")
            resultado[tabla] = int(cursor.fetchone()["n"])
    return resultado


def sumas_verificacion(conexion, tablas=TABLAS_ORIGINALES) -> dict[str, int | None]:
    """CHECKSUM TABLE de cada tabla original (iguales = mismo contenido y estructura de filas)."""
    resultado = {}
    with conexion.cursor() as cursor:
        for tabla in tablas:
            cursor.execute(f"CHECKSUM TABLE `{tabla}`")
            resultado[tabla] = cursor.fetchone()["Checksum"]
    return resultado


def dividir_sql(texto: str) -> list[str]:
    """Divide un script en sentencias respetando `DELIMITER` (como el cliente mysql).

    Ignora líneas que solo son comentarios `--`. Las cadenas del respaldo nunca
    contienen saltos de línea (se escapan), así que basta con mirar el final de línea.
    Divide solo por LF (no usa splitlines) para conservar los CR que haya dentro del
    cuerpo de una rutina: así se restaura byte a byte igual que el original.
    """
    sentencias: list[str] = []
    delimitador = ";"
    actual: list[str] = []
    for linea in texto.split("\n"):
        limpia = linea.strip()
        if not actual and (not limpia or limpia.startswith("--")):
            continue
        if limpia.upper().startswith("DELIMITER "):
            delimitador = limpia.split(None, 1)[1]
            continue
        actual.append(linea)
        if limpia.endswith(delimitador):
            sentencia = "\n".join(actual).rstrip()
            sentencia = sentencia[: len(sentencia) - len(delimitador)].strip()
            if sentencia:
                sentencias.append(sentencia)
            actual = []
    resto = "\n".join(actual).strip()
    if resto:
        sentencias.append(resto)
    return sentencias
