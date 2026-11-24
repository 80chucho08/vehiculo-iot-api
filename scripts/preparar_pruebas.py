"""Prepara el entorno de la prueba por roles (V009) con UN solo carrito físico. Idempotente.

  .venv\\Scripts\\python scripts\\preparar_pruebas.py --bd vehiculo_iot_prueba
  .venv\\Scripts\\python scripts\\preparar_pruebas.py --bd vehiculo_iot --confirmar-produccion vehiculo_iot

Qué deja listo (reutiliza lo que ya exista, no borra nada):
  - Unidad física (--unidad-fisica, por omisión 1): exhibición. Su ESP8266 es el único que se conecta.
  - «· Cliente» (CJ-PRUEBA-0002, venta): asignada al Usuario de prueba; usa el ESP8266 de la física.
  - «· Stock» (CJ-PRUEBA-0003, venta): disponible para vender; usa el ESP8266 de la física.
  - Cuentas: Invitado, Usuario (comprador) y Administrador de prueba.
  - Recursos del Usuario en su unidad: el comando propio «Subir garra suave» y la demo privada
    «Saludo de garra» (bajar y subir la garra dos veces, con el comando propio al final).
  - Una demo de fábrica «Saludo de fábrica» en la unidad Cliente (el Usuario no puede modificarla).

Las contraseñas de las cuentas NUEVAS se generan al azar y se escriben SOLO en --salida (por omisión
cuentas_prueba.local.txt, ignorado por git); nunca en pantalla. Con --restablecer se regeneran todas.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

CUENTAS = (
    ("invitado", "invitado@carrito.test", "Invitado de prueba"),
    ("usuario", "usuario@carrito.test", "Usuario de prueba"),
    ("admin", "admin@carrito.test", "Administrador de prueba"),
)
UNIDADES = (
    ("cliente", "CJ-PRUEBA-0002", "· Cliente", "Unidad vendida al Usuario de prueba (mismo carrito físico)."),
    ("stock", "CJ-PRUEBA-0003", "· Stock", "Unidad en inventario, disponible para vender (mismo carrito físico)."),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bd", required=True)
    parser.add_argument("--confirmar-produccion", default=None)
    parser.add_argument("--unidad-fisica", type=int, default=1)
    parser.add_argument("--salida", default=str(RAIZ / "cuentas_prueba.local.txt"))
    parser.add_argument("--restablecer", action="store_true", help="Regenera la contraseña de las 3 cuentas")
    args = parser.parse_args()

    # La app lee DB_NAME al importarse: se fija ANTES de importar sus módulos. Por eso la base de
    # producción se lee del .env a mano (importar la configuración la fijaría para todo el proceso).
    produccion = os.environ.get("DB_NAME") or next(
        (linea.split("=", 1)[1].strip() for linea in (RAIZ / ".env").read_text(encoding="utf-8").splitlines()
         if linea.strip().startswith("DB_NAME=")), "vehiculo_iot")
    os.environ["DB_NAME"] = args.bd
    sys.path.insert(0, str(RAIZ))
    sys.path.insert(0, str(RAIZ / "scripts"))
    from bd_comun import validar_nombre_bd  # noqa: E402
    from app.core.config import settings  # noqa: E402

    validar_nombre_bd(args.bd)
    assert settings.db_name == args.bd, "la configuración no tomó la base pedida"
    if args.bd == produccion and args.confirmar_produccion != args.bd:
        sys.exit(f"Es la base de producción: repite con --confirmar-produccion {args.bd}")

    from app.core.seguridad import hash_password  # noqa: E402
    from app.repositories import auditoria, inventario  # noqa: E402
    from app.repositories.base import todas, transaccion, una  # noqa: E402

    fisica = una("SELECT id_vehiculo, nombre, id_modelo, tipo_unidad, activo, id_unidad_fisica FROM vehiculos WHERE id_vehiculo = %s",
                 (args.unidad_fisica,))
    if fisica is None or not fisica["activo"] or fisica["id_modelo"] is None:
        sys.exit("La unidad física no existe, está de baja o no tiene modelo.")
    if fisica["tipo_unidad"] != "exhibicion" or fisica["id_unidad_fisica"] is not None:
        sys.exit("La unidad física debe ser de exhibición y tener su propio ESP8266.")
    pasos_log: list[str] = []

    # ---------- Cuentas ----------
    contrasenas: dict[str, str] = {}
    ids: dict[str, int] = {}
    for rol, email, nombre in CUENTAS:
        fila = una("SELECT id_usuario, rol FROM usuarios WHERE email = %s", (email,))
        nueva = f"Prueba-{rol.capitalize()}-{secrets.token_urlsafe(6)}"
        with transaccion() as cur:
            if fila is None:
                cur.execute("INSERT INTO usuarios (email, nombre, password_hash, rol) VALUES (%s, %s, %s, %s)",
                            (email, nombre, hash_password(nueva), rol))
                ids[rol] = int(cur.lastrowid)
                contrasenas[email] = nueva
                pasos_log.append(f"cuenta creada: {email} ({rol})")
            else:
                ids[rol] = int(fila["id_usuario"])
                if fila["rol"] != rol:
                    sys.exit(f"{email} ya existe con rol {fila['rol']}; no se cambia automáticamente.")
                if args.restablecer:
                    cur.execute("UPDATE usuarios SET password_hash = %s, intentos_fallidos = 0, bloqueado_hasta = NULL, activo = 1 "
                                " WHERE id_usuario = %s", (hash_password(nueva), ids[rol]))
                    cur.execute("UPDATE sesiones_usuario SET revocada_en = UTC_TIMESTAMP(), motivo_revocacion = 'password' "
                                " WHERE id_usuario = %s AND revocada_en IS NULL", (ids[rol],))
                    contrasenas[email] = nueva
                    pasos_log.append(f"contraseña restablecida: {email}")

    # ---------- Unidades que comparten el ESP8266 ----------
    unidades: dict[str, int] = {}
    for clave, serie, sufijo, descripcion in UNIDADES:
        fila = una("SELECT id_vehiculo FROM vehiculos WHERE numero_serie = %s", (serie,))
        if fila is None:
            r = inventario.crear_unidad({"nombre": f"{fisica['nombre']} {sufijo}"[:80], "id_modelo": fisica["id_modelo"],
                                         "numero_serie": serie, "tipo_unidad": "venta", "descripcion": descripcion})
            unidades[clave] = r["id_vehiculo"]
            pasos_log.append(f"unidad creada: ID {r['id_vehiculo']} {serie} ({r['comandos_copiados']} comandos, {r['demos_copiadas']} demos)")
        else:
            unidades[clave] = int(fila["id_vehiculo"])
        with transaccion() as cur:
            cur.execute("UPDATE vehiculos SET id_unidad_fisica = %s WHERE id_vehiculo = %s AND id_unidad_fisica IS NULL",
                        (args.unidad_fisica, unidades[clave]))
            if cur.rowcount:
                pasos_log.append(f"unidad {unidades[clave]} usa el ESP8266 de la unidad {args.unidad_fisica}")

    # ---------- Venta de la unidad Cliente al Usuario ----------
    cliente, id_usuario = unidades["cliente"], ids["usuario"]
    vigente = una("SELECT id_usuario FROM asignaciones WHERE vehiculo_activo = %s", (cliente,))
    if vigente is None:
        with transaccion() as cur:
            cur.execute("INSERT INTO asignaciones (id_vehiculo, id_usuario, asignado_por) VALUES (%s, %s, %s)",
                        (cliente, id_usuario, ids["admin"]))
        pasos_log.append(f"unidad {cliente} asignada a usuario@carrito.test")
    elif int(vigente["id_usuario"]) != id_usuario:
        sys.exit(f"La unidad {cliente} ya está asignada a otra cuenta; finaliza esa asignación primero.")

    # ---------- Recursos del Usuario: comando propio y demo privada ----------
    def comando_fabrica(codigo: int) -> int:
        fila = una("SELECT id_comando FROM comandos WHERE id_vehiculo = %s AND codigo_fabrica = %s AND activo = 1", (cliente, codigo))
        if fila is None:
            sys.exit(f"La unidad {cliente} no tiene el movimiento de fábrica {codigo}.")
        return int(fila["id_comando"])

    subir, bajar = comando_fabrica(1), comando_fabrica(3)
    propio = una("SELECT id_comando FROM comandos WHERE id_vehiculo = %s AND tipo = 'usuario' AND id_propietario = %s "
                 "   AND nombre = 'Subir garra suave' AND activo = 1", (cliente, id_usuario))
    if propio is None:
        with transaccion() as cur:
            cur.execute("INSERT INTO comandos (id_vehiculo, nombre, codigo, descripcion, activo, duracion_max_ms, permitido_invitado, "
                        "                      tipo, id_propietario) VALUES (%s, 'Subir garra suave', 1, %s, 1, 1500, 0, 'usuario', %s)",
                        (cliente, "Comando propio: sube la garra con pasos cortos.", id_usuario))
            id_propio = int(cur.lastrowid)
        pasos_log.append("comando propio «Subir garra suave» creado")
    else:
        id_propio = int(propio["id_comando"])

    def asegurar_demo(nombre: str, descripcion: str, tipo: str, pasos: list[tuple[int, int, int]]) -> None:
        if una("SELECT 1 FROM demos WHERE id_vehiculo = %s AND nombre = %s", (cliente, nombre)):
            return
        with transaccion() as cur:
            cur.execute("INSERT INTO demos (id_vehiculo, nombre, descripcion, activo, tipo, id_propietario) VALUES (%s, %s, %s, 1, %s, %s)",
                        (cliente, nombre, descripcion, tipo, id_usuario if tipo == "privada" else None))
            id_demo = int(cur.lastrowid)
            for orden, (id_comando, duracion, pausa) in enumerate(pasos, start=1):
                cur.execute("INSERT INTO demo_movimientos (id_demo, id_comando, orden, duracion_ms, pausa_ms) VALUES (%s, %s, %s, %s, %s)",
                            (id_demo, id_comando, orden, duracion, pausa))
        pasos_log.append(f"demo {tipo} «{nombre}» creada")

    asegurar_demo("Saludo de garra", "Demo privada del Usuario: baja y sube la garra como saludo.", "privada",
                  [(bajar, 800, 200), (subir, 800, 200), (bajar, 800, 200), (subir, 800, 300), (id_propio, 600, 0)])
    asegurar_demo("Saludo de fábrica", "Demo predeterminada: el Usuario puede ejecutarla pero no modificarla.", "fabrica",
                  [(bajar, 700, 200), (subir, 700, 0)])

    auditoria.registrar("pruebas.preparar", "sistema", id_actor=None, rol_actor="sistema", detalle={"pasos": pasos_log[:20]})

    # ---------- Salida ----------
    if contrasenas:
        salida = Path(args.salida)
        previo = salida.read_text(encoding="utf-8") if salida.exists() else ""
        lineas = [f"{email}\t{clave}" for email, clave in contrasenas.items()]
        salida.write_text(previo + f"# {args.bd}\n" + "\n".join(lineas) + "\n", encoding="utf-8")
        print(f"Contraseñas nuevas escritas en {salida} (no se muestran aquí).")
    resumen = todas("SELECT v.id_vehiculo, v.nombre, v.tipo_unidad, v.id_unidad_fisica, u.email AS comprador FROM vehiculos v "
                    " LEFT JOIN asignaciones a ON a.vehiculo_activo = v.id_vehiculo LEFT JOIN usuarios u ON u.id_usuario = a.id_usuario "
                    " WHERE v.id_vehiculo IN (%s, %s, %s)", (args.unidad_fisica, unidades["cliente"], unidades["stock"]))
    for paso in pasos_log or ["sin cambios: todo ya estaba preparado"]:
        print(" -", paso)
    for u in resumen:
        print(f"   unidad {u['id_vehiculo']}: {u['tipo_unidad']}, ESP de {u['id_unidad_fisica'] or 'sí misma'}, comprador {u['comprador'] or '—'}")


if __name__ == "__main__":
    main()
