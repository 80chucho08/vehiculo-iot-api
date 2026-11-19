"""Aplica y revierte las migraciones de migrations/ de forma controlada.

  .venv/Scripts/python scripts/migrar.py --bd vehiculo_iot_prueba estado
  .venv/Scripts/python scripts/migrar.py --bd vehiculo_iot_prueba subir [--hasta 3]
  .venv/Scripts/python scripts/migrar.py --bd vehiculo_iot_prueba bajar --hasta 0
  .venv/Scripts/python scripts/migrar.py --bd vehiculo_iot --confirmar-produccion vehiculo_iot subir

Controles:
- Registra cada versión en `schema_migraciones` (versión, nombre, SHA-256, fecha, duración).
  Si un archivo ya aplicado cambia, se detiene.
- Antes de cada versión evalúa sus líneas `-- @requiere <consulta>` y, al terminar, sus
  líneas `-- @comprobar <consulta>`; cada consulta debe devolver 1.
- Cuenta las filas de las 6 tablas de 4.1/4.2 antes y después: si alguna baja, se detiene.
- Sesión con sql_mode estricto y time_zone UTC.
- Producción (la base del .env): exige --confirmar-produccion <nombre> y un respaldo de
  scripts/respaldo_bd.py de menos de 6 h. `bajar` en producción solo con --permitir-bajar.

MySQL hace commit implícito en cada DDL: NO hay rollback transaccional. Si una versión
falla a medias, el runner lo informa y no la registra; la reversión real es el respaldo
(restaurarlo en una base nueva y cambiar DB_NAME).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pymysql

from bd_comun import BD_PRODUCCION, RAIZ, SQL_MODE_ESTRICTO, conectar, conteos, dividir_sql, validar_nombre_bd

CARPETA = RAIZ / "migrations"
RESPALDO_MAX_EDAD = timedelta(hours=6)
# Errores tolerados al BAJAR (el objeto ya no existe): columna/índice/FK, tabla, CHECK.
ERRORES_TOLERADOS_BAJAR = {1091, 1051, 3821}
PATRON = re.compile(r"^V(\d{3})__([a-z0-9_]+)\.(up|down)\.sql$")


@dataclass
class Migracion:
    version: int
    nombre: str
    up: Path
    down: Path | None

    @property
    def sha256(self) -> str:
        # Normaliza CRLF→LF: git en Windows (EC2) puede hacer checkout con CRLF y el
        # mismo archivo no debe parecer modificado según la máquina que lo aplique.
        return hashlib.sha256(self.up.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def cargar_migraciones() -> list[Migracion]:
    encontradas: dict[int, dict] = {}
    for archivo in sorted(CARPETA.glob("*.sql")):
        coincidencia = PATRON.match(archivo.name)
        if not coincidencia:
            sys.exit(f"Nombre de migración no válido: {archivo.name}")
        version, nombre, sentido = int(coincidencia[1]), coincidencia[2], coincidencia[3]
        datos = encontradas.setdefault(version, {"nombre": nombre})
        if datos["nombre"] != nombre:
            sys.exit(f"V{version:03d} tiene archivos up/down con nombres distintos.")
        datos[sentido] = archivo
    migraciones = []
    for version in sorted(encontradas):
        datos = encontradas[version]
        if "up" not in datos:
            sys.exit(f"Falta V{version:03d}__{datos['nombre']}.up.sql")
        migraciones.append(Migracion(version, datos["nombre"], datos["up"], datos.get("down")))
    esperadas = list(range(1, len(migraciones) + 1))
    if [m.version for m in migraciones] != esperadas:
        sys.exit("Las versiones deben ser consecutivas desde V001.")
    return migraciones


def directivas(texto: str, nombre: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(rf"^--\s*@{nombre}\s+(.+)$", texto, re.MULTILINE)]


def preparar_sesion(cur) -> None:
    cur.execute("SET SESSION sql_mode = %s", (SQL_MODE_ESTRICTO,))
    cur.execute("SET SESSION time_zone = '+00:00'")


def asegurar_control(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migraciones (
          version      SMALLINT UNSIGNED NOT NULL,
          nombre       VARCHAR(100) NOT NULL,
          sha256       CHAR(64) NOT NULL,
          aplicada_en  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          duracion_ms  INT UNSIGNED NOT NULL,
          PRIMARY KEY (version)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """
    )


def aplicadas(cur) -> dict[int, dict]:
    cur.execute("SELECT version, nombre, sha256, aplicada_en FROM schema_migraciones ORDER BY version")
    return {int(fila["version"]): fila for fila in cur.fetchall()}


def evaluar(cur, consultas: list[str], etiqueta: str, migracion: Migracion) -> None:
    for consulta in consultas:
        cur.execute(consulta)
        fila = cur.fetchone()
        valor = next(iter(fila.values())) if fila else None
        if valor != 1:
            raise SystemExit(f"V{migracion.version:03d} · @{etiqueta} no se cumple:\n  {consulta}")


def comprobar_conteos(antes: dict, despues: dict, migracion: Migracion) -> None:
    perdidas = {t: (antes[t], despues[t]) for t in antes if despues[t] < antes[t]}
    if perdidas:
        detalle = ", ".join(f"{t}: {a}→{d}" for t, (a, d) in perdidas.items())
        raise SystemExit(f"V{migracion.version:03d} redujo filas de tablas originales ({detalle}). Detenido.")


def respaldo_reciente(base: str) -> Path | None:
    limite = datetime.now(timezone.utc) - RESPALDO_MAX_EDAD
    for manifiesto in sorted((RAIZ / "respaldos").glob(f"{base}_*.manifest.json"), reverse=True):
        datos = json.loads(manifiesto.read_text(encoding="utf-8"))
        if datos.get("base") == base and datetime.fromisoformat(datos["fecha_utc"]) >= limite:
            return manifiesto
    return None


def ejecutar(cur, sentencias: list[str], tolerados: set[int] = frozenset()) -> list[str]:
    avisos = []
    for numero, sentencia in enumerate(sentencias, 1):
        try:
            cur.execute(sentencia)
        except pymysql.MySQLError as error:
            codigo = error.args[0] if error.args else None
            if codigo in tolerados:
                avisos.append(f"  (omitido, ya no existía) sentencia {numero}: {error.args[1]}")
                continue
            primera_linea = sentencia.strip().splitlines()[0][:110]
            raise SystemExit(
                f"Falló la sentencia {numero}/{len(sentencias)}: {primera_linea}\n"
                f"  MySQL {codigo}: {error.args[1] if len(error.args) > 1 else error}\n"
                "  Las sentencias anteriores YA quedaron aplicadas (DDL con commit implícito)."
            )
    return avisos


def subir(conexion, migraciones: list[Migracion], hasta: int | None) -> None:
    with conexion.cursor() as cur:
        hechas = aplicadas(cur)
        for migracion in migraciones:
            if migracion.version in hechas:
                if hechas[migracion.version]["sha256"] != migracion.sha256:
                    raise SystemExit(f"V{migracion.version:03d} cambió después de aplicarse. No se continúa.")
                continue
            if hasta is not None and migracion.version > hasta:
                break
            texto = migracion.up.read_text(encoding="utf-8")
            evaluar(cur, directivas(texto, "requiere"), "requiere", migracion)
            antes = conteos(conexion)
            inicio = time.monotonic()
            ejecutar(cur, dividir_sql(texto))
            duracion = int((time.monotonic() - inicio) * 1000)
            evaluar(cur, directivas(texto, "comprobar"), "comprobar", migracion)
            despues = conteos(conexion)
            comprobar_conteos(antes, despues, migracion)
            cur.execute(
                "INSERT INTO schema_migraciones (version, nombre, sha256, duracion_ms) VALUES (%s, %s, %s, %s)",
                (migracion.version, migracion.nombre, migracion.sha256, duracion),
            )
            print(f"V{migracion.version:03d} {migracion.nombre}: aplicada en {duracion} ms · conteos sin pérdidas")


def bajar(conexion, migraciones: list[Migracion], hasta: int) -> None:
    with conexion.cursor() as cur:
        hechas = aplicadas(cur)
        for migracion in reversed(migraciones):
            if migracion.version <= hasta or migracion.version not in hechas:
                continue
            if migracion.down is None:
                raise SystemExit(f"V{migracion.version:03d} no tiene script DOWN.")
            antes = conteos(conexion)
            avisos = ejecutar(cur, dividir_sql(migracion.down.read_text(encoding="utf-8")), ERRORES_TOLERADOS_BAJAR)
            comprobar_conteos(antes, conteos(conexion), migracion)
            cur.execute("DELETE FROM schema_migraciones WHERE version = %s", (migracion.version,))
            print(f"V{migracion.version:03d} {migracion.nombre}: revertida")
            for aviso in avisos:
                print(aviso)


def estado(conexion, migraciones: list[Migracion]) -> None:
    with conexion.cursor() as cur:
        hechas = aplicadas(cur)
    for migracion in migraciones:
        fila = hechas.get(migracion.version)
        if fila is None:
            marca = "pendiente"
        elif fila["sha256"] != migracion.sha256:
            marca = f"APLICADA {fila['aplicada_en']} · ¡ARCHIVO MODIFICADO!"
        else:
            marca = f"aplicada {fila['aplicada_en']} UTC"
        print(f"V{migracion.version:03d} {migracion.nombre:<32} {marca}")
    print("Conteos:", ", ".join(f"{t}={n}" for t, n in conteos(conexion).items()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bd", required=True)
    parser.add_argument("--confirmar-produccion", metavar="NOMBRE")
    parser.add_argument("--permitir-bajar", action="store_true")
    sub = parser.add_subparsers(dest="accion", required=True)
    sub.add_parser("estado")
    p_subir = sub.add_parser("subir")
    p_subir.add_argument("--hasta", type=int)
    p_bajar = sub.add_parser("bajar")
    p_bajar.add_argument("--hasta", type=int, required=True, help="versión que queda aplicada (0 = ninguna)")
    args = parser.parse_args()

    base = validar_nombre_bd(args.bd)
    migraciones = cargar_migraciones()

    if base == BD_PRODUCCION and args.accion != "estado":
        if args.confirmar_produccion != base:
            sys.exit(f"`{base}` es PRODUCCIÓN. Repite con --confirmar-produccion {base}.")
        manifiesto = respaldo_reciente(base)
        if manifiesto is None:
            sys.exit("No hay respaldo de producción de menos de 6 h. Ejecuta antes scripts/respaldo_bd.py.")
        print(f"Respaldo verificado: {manifiesto.name}")
        if args.accion == "bajar" and not args.permitir_bajar:
            sys.exit("Los DOWN son para pruebas. En producción, revierte restaurando el respaldo en una base nueva.")

    conexion = conectar(base, autocommit=True)
    try:
        with conexion.cursor() as cur:
            preparar_sesion(cur)
            asegurar_control(cur)
        if args.accion == "estado":
            estado(conexion, migraciones)
        elif args.accion == "subir":
            subir(conexion, migraciones, args.hasta)
            estado(conexion, migraciones)
        else:
            bajar(conexion, migraciones, args.hasta)
            estado(conexion, migraciones)
    finally:
        conexion.close()


if __name__ == "__main__":
    main()
