"""Restaura un respaldo de scripts/respaldo_bd.py en una base NUEVA y lo verifica.

Nunca escribe sobre la base de producción: si hiciera falta revertir, se restaura
en una base nueva (p. ej. `vehiculo_iot_restaurada`) y se cambia DB_NAME en el .env.

Uso:
  .venv/Scripts/python scripts/restaurar_bd.py respaldos/<archivo>.sql --bd vehiculo_iot_prueba
  ... --recrear     borra y vuelve a crear la base destino (solo si termina en _prueba)

Verificación: compara conteos y CHECKSUM TABLE contra el manifiesto del respaldo
y que existan las mismas rutinas. Sale con código 1 si algo no coincide.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from bd_comun import BD_PRODUCCION, conectar, conteos, dividir_sql, sumas_verificacion, validar_nombre_bd


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("respaldo", type=Path)
    parser.add_argument("--bd", required=True, help="base destino (nueva o vacía)")
    parser.add_argument("--recrear", action="store_true", help="DROP + CREATE de la base destino *_prueba")
    args = parser.parse_args()

    destino = validar_nombre_bd(args.bd)
    if destino == BD_PRODUCCION:
        sys.exit(f"Rechazado: `{destino}` es la base de producción. Restaura en una base nueva.")
    if args.recrear and not destino.endswith("_prueba"):
        sys.exit("--recrear solo se permite en bases cuyo nombre termina en _prueba.")

    manifiesto_ruta = args.respaldo.with_suffix(".manifest.json")
    manifiesto = json.loads(manifiesto_ruta.read_text(encoding="utf-8"))
    contenido = args.respaldo.read_bytes()
    if hashlib.sha256(contenido).hexdigest() != manifiesto["sha256"]:
        sys.exit("El SHA-256 del respaldo no coincide con su manifiesto: archivo alterado o incompleto.")

    servidor = conectar(None, autocommit=True)
    try:
        with servidor.cursor() as cur:
            if args.recrear:
                cur.execute(f"DROP DATABASE IF EXISTS `{destino}`")
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{destino}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
            cur.execute(
                "SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s",
                (destino,),
            )
            if cur.fetchone()["n"]:
                sys.exit(f"La base `{destino}` no está vacía. Usa otra o --recrear (si es *_prueba).")
    finally:
        servidor.close()

    conexion = conectar(destino, autocommit=True)
    try:
        sentencias = dividir_sql(contenido.decode("utf-8"))
        with conexion.cursor() as cur:
            for sentencia in sentencias:
                cur.execute(sentencia)
            cur.execute(
                "SELECT ROUTINE_NAME FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA = %s",
                (destino,),
            )
            rutinas = sorted(fila["ROUTINE_NAME"] for fila in cur.fetchall())
        tablas = manifiesto["tablas"]
        restaurado = {"conteos": conteos(conexion, tablas), "checksums": sumas_verificacion(conexion, tablas)}
    finally:
        conexion.close()

    errores = []
    for clave in ("conteos", "checksums"):
        for tabla in tablas:
            if restaurado[clave][tabla] != manifiesto[clave][tabla]:
                errores.append(f"{clave} de {tabla}: respaldo={manifiesto[clave][tabla]} restaurado={restaurado[clave][tabla]}")
    if rutinas != sorted(manifiesto["rutinas"]):
        errores.append("Las rutinas restauradas no coinciden con el manifiesto.")

    print(f"{len(sentencias)} sentencias aplicadas en `{destino}`.")
    for tabla in tablas:
        print(f"  {tabla:<22} {restaurado['conteos'][tabla]:>6} filas  checksum {restaurado['checksums'][tabla]}")
    print(f"  rutinas: {len(rutinas)}")
    if errores:
        print("VERIFICACIÓN FALLIDA:")
        for error in errores:
            print("  -", error)
        sys.exit(1)
    print("VERIFICACIÓN OK: conteos, checksums y rutinas coinciden con el respaldo.")


if __name__ == "__main__":
    main()
