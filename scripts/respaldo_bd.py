"""Respaldo lógico (solo lectura) de la base: estructura, datos, vistas, rutinas y triggers.

Genera dos archivos en `respaldos/` (ignorado por git):
  <bd>_<fecha>.sql            restaurable con scripts/restaurar_bd.py o con el cliente mysql
  <bd>_<fecha>.manifest.json  conteos, CHECKSUM TABLE y SHA-256 del .sql para verificarlo

Uso (raíz del repo):
  .venv/Scripts/python scripts/respaldo_bd.py                 # respalda la base del .env
  .venv/Scripts/python scripts/respaldo_bd.py --bd otra_base

Lee todo dentro de una transacción con snapshot consistente: no bloquea a la API
ni escribe nada. Complementa (no sustituye) el snapshot manual de RDS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone

from bd_comun import BD_PRODUCCION, RAIZ, conectar, conteos, sumas_verificacion, validar_nombre_bd

FILAS_POR_INSERT = 200
CARPETA = RAIZ / "respaldos"


def sin_definer(ddl: str) -> str:
    """Quita DEFINER=`usuario`@`host` para que la rutina pertenezca a quien restaura."""
    import re

    return re.sub(r"DEFINER=`[^`]*`@`[^`]*`\s*", "", ddl)


def respaldar(base: str) -> tuple[str, dict]:
    conexion = conectar(base)
    lineas: list[str] = []
    try:
        with conexion.cursor() as cur:
            cur.execute("SET SESSION time_zone = '+00:00'")
            cur.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            cur.execute("SELECT VERSION() AS v")
            version = cur.fetchone()["v"]

            lineas += [
                f"-- Respaldo lógico de `{base}` · MySQL {version} · {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC",
                "-- Restaurar SOLO en una base vacía (scripts/restaurar_bd.py lo comprueba).",
                "SET NAMES utf8mb4;",
                "SET time_zone = '+00:00';",
                "SET FOREIGN_KEY_CHECKS = 0;",
                "SET UNIQUE_CHECKS = 0;",
                "SET SESSION sql_mode = 'NO_AUTO_VALUE_ON_ZERO';",
                "",
            ]

            cur.execute("SHOW FULL TABLES")
            objetos = [tuple(fila.values()) for fila in cur.fetchall()]
            tablas = sorted(nombre for nombre, tipo in objetos if tipo == "BASE TABLE")
            vistas = sorted(nombre for nombre, tipo in objetos if tipo == "VIEW")

            for tabla in tablas:
                cur.execute(f"SHOW CREATE TABLE `{tabla}`")
                lineas += [f"-- Tabla {tabla}", cur.fetchone()["Create Table"] + ";", ""]

                # Las columnas generadas (VIRTUAL/STORED GENERATED) se recalculan solas: no se
                # insertan. Ojo: DEFAULT CURRENT_TIMESTAMP aparece como 'DEFAULT_GENERATED' y
                # esas columnas SÍ deben copiarse, o la fecha original se perdería.
                cur.execute(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                    "AND EXTRA NOT IN ('VIRTUAL GENERATED', 'STORED GENERATED') "
                    "ORDER BY ORDINAL_POSITION",
                    (base, tabla),
                )
                columnas = [fila["COLUMN_NAME"] for fila in cur.fetchall()]
                lista = ", ".join(f"`{c}`" for c in columnas)
                cur.execute(f"SELECT {lista} FROM `{tabla}`")
                filas = cur.fetchall()
                for inicio in range(0, len(filas), FILAS_POR_INSERT):
                    lote = filas[inicio : inicio + FILAS_POR_INSERT]
                    valores = ",".join(
                        "(" + ",".join(conexion.escape(fila[c]) for c in columnas) + ")" for fila in lote
                    )
                    lineas.append(f"INSERT INTO `{tabla}` ({lista}) VALUES {valores};")
                lineas.append("")

            for vista in vistas:
                cur.execute(f"SHOW CREATE VIEW `{vista}`")
                lineas += [f"-- Vista {vista}", sin_definer(cur.fetchone()["Create View"]) + ";", ""]

            cur.execute(
                "SELECT ROUTINE_NAME, ROUTINE_TYPE FROM information_schema.ROUTINES "
                "WHERE ROUTINE_SCHEMA = %s ORDER BY ROUTINE_TYPE, ROUTINE_NAME",
                (base,),
            )
            rutinas = cur.fetchall()
            cur.execute(
                "SELECT TRIGGER_NAME FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = %s ORDER BY 1",
                (base,),
            )
            triggers = [fila["TRIGGER_NAME"] for fila in cur.fetchall()]

            if rutinas or triggers:
                lineas.append("DELIMITER //")
                for rutina in rutinas:
                    tipo = rutina["ROUTINE_TYPE"]
                    cur.execute(f"SHOW CREATE {tipo} `{rutina['ROUTINE_NAME']}`")
                    fila = cur.fetchone()
                    ddl = fila.get("Create Procedure") or fila.get("Create Function")
                    lineas += [f"-- {tipo} {rutina['ROUTINE_NAME']}", sin_definer(ddl) + "//", ""]
                for trigger in triggers:
                    cur.execute(f"SHOW CREATE TRIGGER `{trigger}`")
                    lineas += [f"-- TRIGGER {trigger}", sin_definer(cur.fetchone()["SQL Original Statement"]) + "//", ""]
                lineas.append("DELIMITER ;")

            lineas += ["SET UNIQUE_CHECKS = 1;", "SET FOREIGN_KEY_CHECKS = 1;", ""]

            manifiesto = {
                "base": base,
                "mysql": version,
                "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "tablas": tablas,
                "vistas": vistas,
                "rutinas": [r["ROUTINE_NAME"] for r in rutinas],
                "triggers": triggers,
                "conteos": conteos(conexion, tablas),
                "checksums": sumas_verificacion(conexion, tablas),
            }
        conexion.rollback()  # fin de la transacción de solo lectura
    finally:
        conexion.close()
    return "\n".join(lineas), manifiesto


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bd", default=BD_PRODUCCION, help="base a respaldar (por defecto la del .env)")
    args = parser.parse_args()
    base = validar_nombre_bd(args.bd)

    sql, manifiesto = respaldar(base)
    CARPETA.mkdir(exist_ok=True)
    sello = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archivo_sql = CARPETA / f"{base}_{sello}.sql"
    archivo_sql.write_text(sql, encoding="utf-8", newline="\n")
    manifiesto["archivo"] = archivo_sql.name
    manifiesto["sha256"] = hashlib.sha256(archivo_sql.read_bytes()).hexdigest()
    archivo_manifiesto = archivo_sql.with_suffix(".manifest.json")
    archivo_manifiesto.write_text(json.dumps(manifiesto, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Respaldo: {archivo_sql.relative_to(RAIZ)} ({archivo_sql.stat().st_size:,} bytes)")
    print(f"Manifiesto: {archivo_manifiesto.relative_to(RAIZ)}")
    for tabla, n in manifiesto["conteos"].items():
        print(f"  {tabla:<22} {n:>6} filas")
    print(f"  rutinas: {len(manifiesto['rutinas'])}  vistas: {len(manifiesto['vistas'])}  triggers: {len(manifiesto['triggers'])}")


if __name__ == "__main__":
    main()
