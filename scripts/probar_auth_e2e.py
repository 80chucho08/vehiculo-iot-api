"""Prueba de punta a punta de roles y autorización contra una API real y una base de PRUEBA.

1. Levanta la API apuntando a la base de prueba, por ejemplo:
     $env:DB_NAME="vehiculo_iot_prueba"; .venv\\Scripts\\python -m uvicorn app.main:app --port 8010
2. En otra terminal:
     .venv\\Scripts\\python scripts\\probar_auth_e2e.py --bd vehiculo_iot_prueba --url http://127.0.0.1:8010

Crea un administrador de prueba DIRECTAMENTE en la base de prueba, con una contraseña aleatoria
que solo vive en memoria. El resto (unidad, compradores, asignación, soporte) lo hace por la API,
como lo haría el frontend. Se niega a correr contra la base de producción.
Casos: Admin registra y asigna; Usuario ve solo lo suyo; User B no consulta ni ejecuta lo de A;
Admin sin consentimiento no monitorea ni opera la unidad vendida; soporte otorgado y revocado;
logout, desactivación y bloqueo por intentos; auditoría.
"""

from __future__ import annotations

import argparse
import secrets
import sys

import httpx

from bd_comun import BD_PRODUCCION, conectar, validar_nombre_bd

from app.core.seguridad import hash_password  # noqa: E402  (bd_comun ya agregó la raíz al path)

resultados: list[tuple[bool, str]] = []


def caso(ok: bool, texto: str) -> None:
    resultados.append((ok, texto))
    print(("OK    " if ok else "FALLA ") + texto)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bd", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8010")
    args = parser.parse_args()
    base = validar_nombre_bd(args.bd)
    if base == BD_PRODUCCION:
        sys.exit("Rechazado: esta prueba crea usuarios y unidades; úsala con una base de prueba.")

    api = httpx.Client(base_url=args.url, timeout=15)
    salud = api.get("/health/db").json()
    if salud.get("base_datos") != base:
        sys.exit(f"La API en {args.url} usa `{salud.get('base_datos')}`, no `{base}`. Detenido.")

    sufijo = secrets.token_hex(3)
    claves = {k: secrets.token_urlsafe(18) for k in ("admin", "a", "b")}
    correo = {k: f"{k}.{sufijo}@prueba.test" for k in claves}

    conexion = conectar(base, autocommit=True)
    with conexion.cursor() as cur:
        cur.execute(
            "INSERT INTO usuarios (email, nombre, password_hash, rol) VALUES (%s, 'Admin e2e', %s, 'admin')",
            (correo["admin"], hash_password(claves["admin"])),
        )
    conexion.close()

    def login(clave: str) -> httpx.Response:
        return api.post("/api/auth/login", json={"email": correo[clave], "password": claves[clave]})

    def h(token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    print("1. Administrador registra y asigna")
    r = login("admin")
    caso(r.status_code == 200 and r.json()["usuario"]["rol"] == "admin", "login de admin")
    t_admin = r.json()["access_token"]
    r = api.post("/api/vehiculos", headers=h(t_admin), json={"nombre": f"Unidad e2e {sufijo}", "modelo": "Carrito grúa"})
    caso(r.status_code == 201, "admin da de alta una unidad (tipo 'venta' por defecto)")
    unidad = int(r.json()["resultados"][0]["id_vehiculo"])
    r = api.post(f"/api/vehiculos/{unidad}/comandos", headers=h(t_admin), json={"nombre": "Garra arriba", "codigo": 1})
    caso(r.status_code == 201, "admin registra un comando para la unidad")
    for clave, nombre in (("a", "Comprador A"), ("b", "Comprador B")):
        r = api.post("/api/admin/usuarios", headers=h(t_admin),
                     json={"email": correo[clave], "nombre": nombre, "password": claves[clave], "rol": "usuario"})
        caso(r.status_code == 201 and "password_hash" not in r.text, f"admin crea al {nombre} (sin exponer el hash)")
    usuarios = {u["email"]: u["id_usuario"] for u in api.get("/api/admin/usuarios", headers=h(t_admin),
                                                             params={"buscar": sufijo}).json()["items"]}
    id_a, id_b = usuarios[correo["a"]], usuarios[correo["b"]]
    r = api.post("/api/admin/asignaciones", headers=h(t_admin), json={"id_vehiculo": unidad, "id_usuario": id_a})
    caso(r.status_code == 201, "admin asigna la unidad al comprador A")
    id_asignacion = r.json().get("id_asignacion")
    r = api.post("/api/admin/asignaciones", headers=h(t_admin), json={"id_vehiculo": unidad, "id_usuario": id_b})
    caso(r.status_code == 409, "asignar la misma unidad a B → 409 (una asignación vigente)")
    r = api.post("/api/admin/asignaciones", headers=h(t_admin), json={"id_vehiculo": 1, "id_usuario": id_b})
    caso(r.status_code == 409, "asignar la unidad de exhibición → 409")
    r = api.post("/api/admin/asignaciones", headers=h(t_admin), json={"id_vehiculo": unidad, "id_usuario": 999999})
    caso(r.status_code in (404, 409), f"asignar a un usuario inexistente → {r.status_code}")

    print("\n2. Cada comprador ve y controla solo lo suyo")
    t_a = login("a").json()["access_token"]
    t_b = login("b").json()["access_token"]
    mis_a = api.get("/api/mis-vehiculos", headers=h(t_a)).json()["vehiculos"]
    mis_b = api.get("/api/mis-vehiculos", headers=h(t_b)).json()["vehiculos"]
    caso([v["id_vehiculo"] for v in mis_a] == [unidad], "A ve exactamente su unidad en Mis vehículos")
    caso(mis_b == [], "B no ve ninguna unidad")
    caso(api.get(f"/api/vehiculos/{unidad}/historial", headers=h(t_a)).status_code == 200, "A consulta el historial de su unidad")
    r = api.post(f"/api/vehiculos/{unidad}/detener", headers=h(t_a))
    caso(r.status_code not in (401, 403, 404), f"A está autorizado a DETENER su unidad (respuesta {r.status_code}: {r.json().get('detail', r.json().get('estado'))})")
    for metodo, ruta in (("get", f"/api/vehiculos/{unidad}/historial"), ("get", f"/api/vehiculos/{unidad}/estado"),
                         ("post", f"/api/vehiculos/{unidad}/comandos/1/ejecutar"), ("post", f"/api/vehiculos/{unidad}/detener")):
        r = getattr(api, metodo)(ruta, headers=h(t_b))
        caso(r.status_code == 404, f"B → {metodo.upper()} {ruta.replace(str(unidad), '{A}')} → 404")
    caso(api.get(f"/api/vehiculos/{unidad}/historial").status_code == 404, "anónimo → historial de unidad vendida → 404")
    caso(api.get("/api/admin/usuarios", headers=h(t_a)).status_code == 403, "A → rutas de administración → 403")

    print("\n3. Administrador y consentimiento del comprador")
    caso(api.get(f"/api/vehiculos/{unidad}/comandos", headers=h(t_admin)).status_code == 200, "admin ve el inventario (comandos)")
    caso(api.get(f"/api/vehiculos/{unidad}/historial", headers=h(t_admin)).status_code == 403, "admin sin consentimiento → historial → 403")
    caso(api.post(f"/api/vehiculos/{unidad}/detener", headers=h(t_admin)).status_code == 403, "admin sin consentimiento → DETENER → 403")
    caso(api.post(f"/api/mis-vehiculos/{unidad}/soporte", headers=h(t_b), json={"motivo": "intento"}).status_code == 404,
         "B no puede autorizar soporte sobre la unidad de A → 404")
    r = api.post(f"/api/mis-vehiculos/{unidad}/soporte", headers=h(t_a), json={"motivo": "diagnóstico", "minutos": 15})
    caso(r.status_code == 201, "A autoriza soporte por 15 min")
    caso(api.get(f"/api/vehiculos/{unidad}/historial", headers=h(t_admin)).status_code == 200, "admin con soporte vigente → historial → 200")
    caso(api.delete(f"/api/mis-vehiculos/{unidad}/soporte", headers=h(t_a)).status_code == 204, "A revoca el soporte")
    caso(api.get(f"/api/vehiculos/{unidad}/historial", headers=h(t_admin)).status_code == 403, "tras revocar → admin → 403 de nuevo")

    print("\n4. Sesiones")
    r = api.post("/api/auth/renovar", headers=h(t_admin))
    caso(r.status_code == 200 and r.json()["access_token"] != t_admin, "renovar devuelve un token nuevo")
    t_admin = r.json()["access_token"]
    caso(api.post("/api/auth/logout", headers=h(t_a)).status_code == 204, "A cierra sesión")
    caso(api.get("/api/mis-vehiculos", headers=h(t_a)).status_code == 401, "el token de A ya no sirve tras logout → 401")
    r = api.put(f"/api/admin/usuarios/{id_b}", headers=h(t_admin), json={"nombre": "Comprador B", "rol": "usuario", "activo": False})
    caso(r.status_code == 200, "admin desactiva a B")
    caso(api.get("/api/mis-vehiculos", headers=h(t_b)).status_code == 401, "el token de B queda revocado al desactivarlo → 401")
    caso(login("b").status_code == 401, "B desactivado no puede iniciar sesión")
    malos = [api.post("/api/auth/login", json={"email": correo["a"], "password": "incorrecta-x"}).status_code for _ in range(5)]
    caso(malos == [401] * 5, "5 intentos fallidos de A → 401")
    caso(login("a").status_code == 401, "con la cuenta bloqueada, ni la contraseña correcta entra → 401")

    print("\n5. Fin de la venta y auditoría")
    r = api.post(f"/api/admin/asignaciones/{id_asignacion}/finalizar", headers=h(t_admin), json={"motivo": "prueba e2e"})
    caso(r.status_code == 200, "admin finaliza la asignación")
    caso(api.get(f"/api/vehiculos/{unidad}/historial", headers=h(t_admin)).status_code == 200,
         "unidad sin comprador vuelve a inventario: admin la monitorea")
    acciones = {(i["accion"], i["resultado"]) for i in api.get("/api/admin/auditoria", headers=h(t_admin),
                                                                   params={"por_pagina": 100}).json()["items"]}
    esperadas = {("auth.login", "ok"), ("auth.login_fallido", "denegado"), ("vehiculo.alta", "ok"), ("usuario.crear", "ok"),
                 ("asignacion.crear", "ok"), ("acceso.denegado", "denegado"), ("soporte.otorgar", "ok"),
                 ("soporte.monitorear", "ok"), ("soporte.revocar", "ok"), ("usuario.actualizar", "ok"),
                 ("auth.logout", "ok"), ("asignacion.finalizar", "ok")}
    faltan = esperadas - acciones
    caso(not faltan, "auditoría registró: " + ", ".join(sorted(a for a, _ in esperadas)) + (f" · FALTAN {faltan}" if faltan else ""))
    dashboard = api.get("/api/admin/dashboard", headers=h(t_admin))
    caso(dashboard.status_code == 200 and "unidades_vendidas" in dashboard.json(), "dashboard con métricas reales")

    fallas = [t for ok, t in resultados if not ok]
    print(f"\n{len(resultados) - len(fallas)}/{len(resultados)} casos OK (base `{base}`)")
    sys.exit(1 if fallas else 0)


if __name__ == "__main__":
    main()
