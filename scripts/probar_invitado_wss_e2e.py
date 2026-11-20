"""Prueba de punta a punta de invitados (Etapa 7) y WSS autenticado (Etapa 8) contra una base de PRUEBA.

1. API apuntando a la base de prueba (ver probar_auth_e2e.py).
2. .venv\\Scripts\\python scripts\\probar_invitado_wss_e2e.py --bd vehiculo_iot_prueba --url http://127.0.0.1:8010

Requisitos en la base de prueba: unidad 1 de exhibición (modelo publicado) y al menos una unidad
vendida a un comprador (la crea probar_auth_e2e.py o la interfaz). Crea un admin de prueba propio.
Dura ~45 s porque espera al vigilante (reservas vencidas y revalidación de WSS).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
import time

import httpx
import websockets

from bd_comun import BD_PRODUCCION, conectar, validar_nombre_bd

from app.core.seguridad import hash_password  # noqa: E402

resultados: list[tuple[bool, str]] = []


def caso(ok: bool, texto: str) -> None:
    resultados.append((ok, texto))
    print(("OK    " if ok else "FALLA ") + texto)


async def abrir_ws(url_ws: str, extra_headers=None):
    """Abre el WSS y devuelve (socket, primer_mensaje | None, codigo_cierre | None)."""
    ws = await websockets.connect(url_ws, additional_headers=extra_headers or {}, open_timeout=10)
    try:
        primero = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        return ws, primero, None
    except websockets.ConnectionClosed as cierre:
        return ws, None, cierre.rcvd.code if cierre.rcvd else None


async def esperar_cierre(ws, segundos: float) -> int | None:
    fin = time.monotonic() + segundos
    try:
        while time.monotonic() < fin:
            await asyncio.wait_for(ws.recv(), timeout=max(0.1, fin - time.monotonic()))
    except websockets.ConnectionClosed as cierre:
        return cierre.rcvd.code if cierre.rcvd else None
    except asyncio.TimeoutError:
        return None
    return None


async def principal(base: str, url: str) -> None:
    api = httpx.Client(base_url=url, timeout=20)
    ws_base = url.replace("http", "ws", 1)
    if api.get("/health/db").json().get("base_datos") != base:
        sys.exit("La API no usa la base indicada.")

    clave = secrets.token_urlsafe(18)
    correo = f"admin.wss.{secrets.token_hex(3)}@prueba.test"
    c = conectar(base, autocommit=True)
    with c.cursor() as cur:
        cur.execute("INSERT INTO usuarios (email, nombre, password_hash, rol) VALUES (%s, 'Admin WSS', %s, 'admin')",
                    (correo, hash_password(clave)))
        cur.execute("UPDATE sesiones_invitado SET finalizada_en = UTC_TIMESTAMP(), motivo_fin = 'revocada' WHERE finalizada_en IS NULL")
        cur.execute("SELECT a.id_vehiculo, a.id_usuario FROM asignaciones a WHERE a.fin IS NULL ORDER BY a.id_asignacion DESC LIMIT 1")
        vendida = cur.fetchone()
        cur.execute("SELECT id_comando, codigo, permitido_invitado FROM comandos WHERE id_vehiculo = 1")
        comandos = cur.fetchall()
    c.close()
    if vendida is None:
        sys.exit("No hay unidades vendidas en la base de prueba (corre antes probar_auth_e2e.py).")
    unidad_vendida = vendida["id_vehiculo"]
    permitido = next(x["id_comando"] for x in comandos if x["permitido_invitado"])
    prohibido = next(x["id_comando"] for x in comandos if not x["permitido_invitado"])
    h_admin = {"Authorization": f"Bearer {api.post('/api/auth/login', json={'email': correo, 'password': clave}).json()['access_token']}"}

    print("1. Catálogo y reserva exclusiva")
    cat = api.get("/api/catalogo").json()
    caso(any(u["id_vehiculo"] == 1 and u["disponible"] for u in cat["unidades"]), "catálogo público: unidad 1 disponible")
    caso(all("numero_serie" in u and "propietario" not in u for u in cat["unidades"]), "catálogo sin datos de compradores")
    caso(api.post("/api/invitado/sesiones", json={"id_vehiculo": 1, "acepto_condiciones": False}).status_code == 400,
         "sin aceptar condiciones de seguridad → 400")
    caso(api.post("/api/invitado/sesiones", json={"id_vehiculo": unidad_vendida, "acepto_condiciones": True}).status_code == 404,
         "reservar una unidad vendida → 404")
    r = api.post("/api/invitado/sesiones", json={"id_vehiculo": 1, "acepto_condiciones": True})
    caso(r.status_code == 201, f"reserva de la unidad 1 por {r.json().get('minutos')} min")
    h_inv = {"Authorization": f"Bearer {r.json()['access_token']}"}
    caso(api.post("/api/invitado/sesiones", json={"id_vehiculo": 1, "acepto_condiciones": True}).status_code == 409,
         "segunda reserva simultánea → 409 (exclusiva)")
    caso(not next(u for u in api.get("/api/catalogo").json()["unidades"] if u["id_vehiculo"] == 1)["disponible"],
         "el catálogo la muestra ocupada")

    print("\n2. Lo que el invitado puede y no puede hacer")
    caso(api.get("/api/vehiculos/1/estado", headers=h_inv).status_code == 200, "invitado monitorea su unidad reservada")
    caso(api.get(f"/api/vehiculos/{unidad_vendida}/estado", headers=h_inv).status_code == 404, "invitado → unidad vendida → 404")
    caso(api.get("/api/mis-vehiculos", headers=h_inv).status_code == 401, "invitado → rutas de comprador → 401")
    caso(api.post(f"/api/vehiculos/1/comandos/{prohibido}/ejecutar", headers=h_inv).status_code == 403,
         "invitado → comando no permitido → 403")

    print("\n3. WSS con ticket de un solo uso")
    t = api.post("/api/ws/ticket", json={"id_vehiculo": 1}, headers=h_inv).json()["ticket"]
    ws_inv, primero, _ = await abrir_ws(f"{ws_base}/ws/vehiculos/1?ticket={t}")
    caso(primero is not None and primero.get("tipo") == "estado_inicial", "invitado abre el WSS de su unidad con ticket")
    _, _, codigo = await abrir_ws(f"{ws_base}/ws/vehiculos/1?ticket={t}")
    caso(codigo == 4401, f"reutilizar el ticket → cierre {codigo}")
    _, _, codigo = await abrir_ws(f"{ws_base}/ws/vehiculos/{unidad_vendida}")
    caso(codigo == 4401, f"anónimo sin ticket → WSS de unidad vendida → cierre {codigo}")
    caso(api.post("/api/ws/ticket", json={"id_vehiculo": unidad_vendida}, headers=h_inv).status_code == 404,
         "invitado pide ticket de unidad vendida → 404")
    r = api.post(f"/api/vehiculos/1/comandos/{permitido}/ejecutar", headers=h_inv)
    caso(r.status_code == 200, f"invitado ejecuta un comando permitido → {r.status_code} ({r.json().get('estado')})")
    recibido = json.loads(await asyncio.wait_for(ws_inv.recv(), timeout=10))
    caso(recibido.get("tipo") == "comando_individual", "el comando llega por SU canal WSS")

    print("\n4. Exclusividad frente al administrador")
    caso(api.post(f"/api/vehiculos/1/comandos/{permitido}/ejecutar", headers=h_admin).status_code == 409,
         "admin no puede operar la unidad reservada → 409")
    caso(api.post("/api/vehiculos/1/detener", headers=h_admin).status_code == 200, "admin sí puede DETENERLA → 200")

    print("\n5. Token del ESP8266")
    token = api.post(f"/api/admin/unidades/{unidad_vendida}/token-dispositivo", headers=h_admin).json()["token"]
    _, _, codigo = await abrir_ws(f"{ws_base}/ws/vehiculos/{unidad_vendida}?rol=dispositivo")
    caso(codigo == 4401, f"dispositivo sin token → cierre {codigo}")
    _, _, codigo = await abrir_ws(f"{ws_base}/ws/vehiculos/{unidad_vendida}?rol=dispositivo",
                                  {"X-Dispositivo-Token": "adivinado"})
    caso(codigo == 4401, f"dispositivo con token falso → cierre {codigo}")
    ws_dev, primero, _ = await abrir_ws(f"{ws_base}/ws/vehiculos/{unidad_vendida}?rol=dispositivo",
                                        {"X-Dispositivo-Token": token})
    caso(primero is not None, "dispositivo con el token correcto (cabecera) se conecta")
    await ws_dev.close()

    print("\n6. Fin de la prueba: manual y por tiempo")
    caso(api.delete("/api/invitado/sesion", headers=h_inv).status_code == 204, "el invitado termina su prueba")
    codigo = await esperar_cierre(ws_inv, 5)
    caso(codigo == 4403, f"su WSS se cierra al terminar → {codigo}")
    caso(api.get("/api/vehiculos/1/estado", headers=h_inv).status_code == 401, "su token deja de servir → 401")
    r = api.post("/api/invitado/sesiones", json={"id_vehiculo": 1, "acepto_condiciones": True})
    caso(r.status_code == 201, "la unidad quedó libre: nueva reserva")
    h_inv2 = {"Authorization": f"Bearer {r.json()['access_token']}"}
    t = api.post("/api/ws/ticket", json={"id_vehiculo": 1}, headers=h_inv2).json()["ticket"]
    ws_inv2, _, _ = await abrir_ws(f"{ws_base}/ws/vehiculos/1?ticket={t}")
    c = conectar(base, autocommit=True)
    with c.cursor() as cur:  # simula que pasaron los minutos
        cur.execute("UPDATE sesiones_invitado SET expira_en = UTC_TIMESTAMP() + INTERVAL 2 SECOND "
                    " WHERE finalizada_en IS NULL AND id_vehiculo = 1")
    c.close()
    codigo = await esperar_cierre(ws_inv2, 15)
    caso(codigo == 4403, f"al vencer, el vigilante cierra su WSS → {codigo}")
    caso(api.get("/api/vehiculos/1/estado", headers=h_inv2).status_code == 401, "token vencido → 401")
    c = conectar(base, autocommit=True)
    with c.cursor() as cur:
        cur.execute("SELECT motivo_fin FROM sesiones_invitado WHERE id_vehiculo = 1 ORDER BY id_sesion_invitado DESC LIMIT 1")
        motivo = cur.fetchone()["motivo_fin"]
        cur.execute("SELECT origen FROM historial_comandos WHERE id_vehiculo = 1 ORDER BY id_historial DESC LIMIT 1")
        ultimo = cur.fetchone()
    c.close()
    caso(motivo == "expirada", f"reserva cerrada con motivo «{motivo}»")
    caso(ultimo and ultimo["origen"] == "sistema", "la unidad recibió DETENER automático (historial origen «sistema»)")

    print("\n7. Revocación en vivo por cambio de permisos")
    c = conectar(base, autocommit=True)
    with c.cursor() as cur:
        cur.execute("INSERT INTO autorizaciones_soporte (id_vehiculo, id_usuario, motivo, expira_en) "
                    "VALUES (%s, %s, 'prueba wss', UTC_TIMESTAMP() + INTERVAL 10 MINUTE)", (unidad_vendida, vendida["id_usuario"]))
    c.close()
    t = api.post("/api/ws/ticket", json={"id_vehiculo": unidad_vendida}, headers=h_admin)
    caso(t.status_code == 200, "admin con soporte obtiene ticket de la unidad vendida")
    ws_adm, primero, _ = await abrir_ws(f"{ws_base}/ws/vehiculos/{unidad_vendida}?ticket={t.json()['ticket']}")
    c = conectar(base, autocommit=True)
    with c.cursor() as cur:
        cur.execute("UPDATE autorizaciones_soporte SET revocada_en = UTC_TIMESTAMP() WHERE id_vehiculo = %s AND revocada_en IS NULL",
                    (unidad_vendida,))
    c.close()
    codigo = await esperar_cierre(ws_adm, 25)
    caso(codigo == 4403, f"el comprador revoca el soporte → el WSS del admin se cierra → {codigo}")

    fallas = [t for ok, t in resultados if not ok]
    print(f"\n{len(resultados) - len(fallas)}/{len(resultados)} casos OK (base `{base}`)")
    sys.exit(1 if fallas else 0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bd", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8010")
    args = parser.parse_args()
    base = validar_nombre_bd(args.bd)
    if base == BD_PRODUCCION:
        sys.exit("Rechazado: esta prueba crea datos; úsala con una base de prueba.")
    asyncio.run(principal(base, args.url))


if __name__ == "__main__":
    main()
