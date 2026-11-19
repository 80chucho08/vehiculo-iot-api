"""Crea el PRIMER administrador (o restablece la contraseña de un admin existente).

  .venv\\Scripts\\python scripts\\crear_admin.py tu.correo@dominio.com --nombre "Tu nombre"
  .venv\\Scripts\\python scripts\\crear_admin.py tu.correo@dominio.com --restablecer

La contraseña se pide dos veces con getpass: no aparece en pantalla, ni en el historial de la
terminal, ni en argumentos del proceso. Mínimo 12 caracteres. Se guarda solo su hash Argon2id.
Usa la base del .env (o --bd). Deja constancia en `auditoria` como actor 'sistema'.
Los demás usuarios se crean desde la API (POST /api/admin/usuarios) con este administrador.
"""

from __future__ import annotations

import argparse
import getpass
import re
import sys

from bd_comun import BD_PRODUCCION, conectar, validar_nombre_bd

from app.core.seguridad import hash_password  # noqa: E402

MINIMO = 12


def pedir_password() -> str:
    for _ in range(3):
        primera = getpass.getpass(f"Contraseña nueva (mínimo {MINIMO} caracteres): ")
        if len(primera) < MINIMO:
            print("Demasiado corta.")
            continue
        if getpass.getpass("Repítela: ") != primera:
            print("No coinciden.")
            continue
        return primera
    sys.exit("Sin contraseña válida; no se hizo ningún cambio.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("email")
    parser.add_argument("--nombre", default="Administrador")
    parser.add_argument("--bd", default=BD_PRODUCCION)
    parser.add_argument("--restablecer", action="store_true", help="cambiar la contraseña de un admin existente")
    args = parser.parse_args()

    email = args.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        sys.exit("Email no válido.")
    base = validar_nombre_bd(args.bd)

    conexion = conectar(base, autocommit=False)
    try:
        with conexion.cursor() as cur:
            cur.execute("SELECT id_usuario, rol FROM usuarios WHERE email = %s", (email,))
            existente = cur.fetchone()
            if args.restablecer:
                if existente is None or existente["rol"] != "admin":
                    sys.exit("No existe un administrador con ese email.")
            elif existente is not None:
                sys.exit("Ya existe un usuario con ese email. Usa --restablecer para cambiar su contraseña.")

            password_hash = hash_password(pedir_password())
            if args.restablecer:
                id_usuario = existente["id_usuario"]
                cur.execute(
                    "UPDATE usuarios SET password_hash = %s, intentos_fallidos = 0, bloqueado_hasta = NULL, activo = 1 "
                    " WHERE id_usuario = %s",
                    (password_hash, id_usuario),
                )
                cur.execute(
                    "UPDATE sesiones_usuario SET revocada_en = UTC_TIMESTAMP(), motivo_revocacion = 'password_restablecida' "
                    " WHERE id_usuario = %s AND revocada_en IS NULL",
                    (id_usuario,),
                )
                accion = "usuario.restablecer_password"
            else:
                cur.execute(
                    "INSERT INTO usuarios (email, nombre, password_hash, rol) VALUES (%s, %s, %s, 'admin')",
                    (email, args.nombre.strip()[:80] or "Administrador", password_hash),
                )
                id_usuario = cur.lastrowid
                accion = "usuario.crear"
            cur.execute(
                "INSERT INTO auditoria (id_actor, rol_actor, accion, recurso, id_recurso, detalle) "
                "VALUES (NULL, 'sistema', %s, 'usuario', %s, JSON_OBJECT('via', 'scripts/crear_admin.py', 'rol', 'admin'))",
                (accion, str(id_usuario)),
            )
        conexion.commit()
    finally:
        conexion.close()
    print(f"Listo: administrador {email} (id {id_usuario}) en `{base}`.")


if __name__ == "__main__":
    main()
