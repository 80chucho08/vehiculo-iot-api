"""E2E de la prueba por roles (V009) contra una API real ya preparada con preparar_pruebas.py.

  .venv\\Scripts\\python scripts\\probar_roles_e2e.py --api http://127.0.0.1:8010 --cuentas cuentas.txt

Solo usa endpoints REST. NO mueve el carrito si no hay ESP8266 conectado (la API responde igual), pero
con el carrito encendido SÍ lo moverá brevemente (garra): hazlo con la zona despejada.
Lo que crea para probar (un comando y una demo temporales) lo borra al final.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

resultados: list[tuple[bool, str]] = []


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def __call__(self, metodo: str, ruta: str, token: str | None = None, cuerpo: dict | None = None) -> tuple[int, object]:
        datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
        peticion = urllib.request.Request(self.base + ruta, data=datos, method=metodo)
        peticion.add_header("Content-Type", "application/json")
        if token:
            peticion.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(peticion, timeout=20) as r:
                texto = r.read().decode() or "null"
                return r.status, json.loads(texto)
        except urllib.error.HTTPError as error:
            texto = error.read().decode() or "null"
            try:
                return error.code, json.loads(texto)
            except json.JSONDecodeError:
                return error.code, texto


def control(respuesta: tuple[int, object]) -> int | str:
    """Para ejecutar/DETENER: 200, o 409 «sin dispositivo» cuenta como PERMITIDO (sin carrito conectado)."""
    status, cuerpo = respuesta
    if status == 200 or (status == 409 and "dispositivo" in json.dumps(cuerpo, ensure_ascii=False).lower()):
        return "permitido"
    return status


def caso(nombre: str, esperado, obtenido) -> None:
    ok = obtenido in esperado if isinstance(esperado, (tuple, list, set)) else obtenido == esperado
    resultados.append((ok, nombre))
    print(("  OK   " if ok else "  FALLA") + f" {nombre}" + ("" if ok else f" (esperado {esperado}, obtenido {obtenido})"))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True)
    parser.add_argument("--cuentas", required=True, help="Archivo email<TAB>contraseña de preparar_pruebas.py")
    parser.add_argument("--fisica", type=int, default=1)
    args = parser.parse_args()
    api = Api(args.api)
    claves = {}
    for linea in open(args.cuentas, encoding="utf-8"):
        if "\t" in linea and not linea.startswith("#"):
            email, clave = linea.rstrip("\n").split("\t", 1)
            claves[email] = clave  # la última aparición gana (restablecer)

    tokens, ids = {}, {}
    for email in ("invitado@carrito.test", "usuario@carrito.test", "admin@carrito.test"):
        status, r = api("POST", "/api/auth/login", cuerpo={"email": email, "password": claves[email]})
        caso(f"login {email}", 200, status)
        tokens[email.split("@")[0]] = r["access_token"]
        ids[email.split("@")[0]] = r["usuario"]["rol"]
    inv, usr, adm = tokens["invitado"], tokens["usuario"], tokens["admin"]
    caso("rol de la cuenta invitado", "invitado", ids["invitado"])

    _, inventario = api("GET", "/api/admin/inventario", adm)
    por_serie = {u["numero_serie"]: u for u in inventario["unidades"]}
    cliente, stock, fisica = por_serie["CJ-PRUEBA-0002"]["id_vehiculo"], por_serie["CJ-PRUEBA-0003"]["id_vehiculo"], args.fisica
    caso("unidades espejo usan el ESP8266 de la física",
         True, por_serie["CJ-PRUEBA-0002"]["id_unidad_fisica"] == fisica == por_serie["CJ-PRUEBA-0003"]["id_unidad_fisica"])

    print("\n1) Invitado")
    _, cmds = api("GET", f"/api/vehiculos/{fisica}/comandos", inv)
    caso("ve los comandos de la exhibición", True, len(cmds["comandos"]) > 0)
    permitido = next(c for c in cmds["comandos"] if c["permitido_invitado"] and c["codigo"] == 3)
    no_permitido = next(c for c in cmds["comandos"] if not c["permitido_invitado"])
    caso("controla la exhibición (garra abajo)", "permitido", control(api("POST", f"/api/vehiculos/{fisica}/comandos/{permitido['id_comando']}/ejecutar", inv)))
    caso("DETENER la exhibición", "permitido", control(api("POST", f"/api/vehiculos/{fisica}/detener", inv)))
    caso("comando no permitido para invitados → 403", 403,
         api("POST", f"/api/vehiculos/{fisica}/comandos/{no_permitido['id_comando']}/ejecutar", inv)[0])
    caso("carrito vendido → 404", 404, api("GET", f"/api/vehiculos/{cliente}/estado", inv)[0])
    caso("carrito en stock → 404", 404, api("POST", f"/api/vehiculos/{stock}/detener", inv)[0])
    caso("no crea demos (ruta de comprador) → 403", 403,
         api("POST", f"/api/mis-vehiculos/{fisica}/demos", inv, {"nombre": "x", "pasos": [{"id_comando": 1, "duracion_ms": 500}]})[0])
    caso("no crea comandos → 403", 403, api("POST", f"/api/mis-vehiculos/{fisica}/comandos", inv, {"nombre": "x", "codigo": 1})[0])
    caso("no crea comandos de fábrica → 403", 403, api("POST", f"/api/vehiculos/{fisica}/comandos", inv, {"nombre": "x", "codigo": 1})[0])
    caso("no elimina demos → 403", 403, api("DELETE", "/api/admin/demos/1", inv)[0])

    print("\n2) Usuario (comprador)")
    _, mis = api("GET", "/api/mis-vehiculos", usr)
    caso("ve solo su unidad", True, [v["id_vehiculo"] for v in mis["vehiculos"]] == [cliente])
    _, propios = api("GET", f"/api/mis-vehiculos/{cliente}/comandos", usr)
    caso("tiene su comando propio «Subir garra suave»", True, any(c["nombre"] == "Subir garra suave" for c in propios["comandos"]))
    status, r = api("POST", f"/api/mis-vehiculos/{cliente}/comandos", usr, {"nombre": "E2E bajar garra", "codigo": 3})
    caso("crea un comando propio", 201, status)
    nuevo = r.get("id_comando") if isinstance(r, dict) else None
    caso("no crea un movimiento no registrado (código 13) → 422", 422,
         api("POST", f"/api/mis-vehiculos/{cliente}/comandos", usr, {"nombre": "Turbo", "codigo": 13})[0])
    caso("no crea un movimiento inexistente (código 99) → 422", 422,
         api("POST", f"/api/mis-vehiculos/{cliente}/comandos", usr, {"nombre": "Volar", "codigo": 99})[0])
    _, todos = api("GET", f"/api/vehiculos/{cliente}/comandos", usr)
    fabrica = next(c for c in todos["comandos"] if c["tipo"] == "fabrica" and c["codigo"] == 3)
    caso("ejecuta su comando propio", "permitido", control(api("POST", f"/api/vehiculos/{cliente}/comandos/{nuevo}/ejecutar", usr)))
    caso("DETENER su unidad", "permitido", control(api("POST", f"/api/vehiculos/{cliente}/detener", usr)))
    caso("no elimina un comando de fábrica → 404", 404, api("DELETE", f"/api/mis-vehiculos/{cliente}/comandos/{fabrica['id_comando']}", usr)[0])
    caso("no modifica un comando de fábrica → 403", 403,
         api("PUT", f"/api/comandos/{fabrica['id_comando']}", usr, {"nombre": "x", "codigo": 3, "activo": True})[0])
    caso("elimina su comando propio", 204, api("DELETE", f"/api/mis-vehiculos/{cliente}/comandos/{nuevo}", usr)[0])

    _, demos = api("GET", f"/api/mis-vehiculos/{cliente}/demos", usr)
    saludo = next(d for d in demos["demos"] if d["nombre"] == "Saludo de garra")
    de_fabrica = next(d for d in demos["demos"] if d["tipo"] == "fabrica")
    caso("su demo «Saludo de garra» es editable", True, saludo["editable"])
    caso("la demo de fábrica no es editable", False, de_fabrica["editable"])
    pasos = [{"id_comando": p["id_comando"], "duracion_ms": p["duracion_ms"], "pausa_ms": p["pausa_ms"]} for p in saludo["pasos"]]
    caso("edita su demo (agrega un paso)", 200, api("PUT", f"/api/mis-vehiculos/{cliente}/demos/{saludo['id_demo']}", usr,
         {"nombre": saludo["nombre"], "descripcion": saludo["descripcion"], "activo": True, "pasos": pasos + [pasos[0]]})[0])
    caso("restaura su demo", 200, api("PUT", f"/api/mis-vehiculos/{cliente}/demos/{saludo['id_demo']}", usr,
         {"nombre": saludo["nombre"], "descripcion": saludo["descripcion"], "activo": True, "pasos": pasos})[0])
    caso("no modifica la demo de fábrica → 404", 404, api("PUT", f"/api/mis-vehiculos/{cliente}/demos/{de_fabrica['id_demo']}", usr,
         {"nombre": "x", "activo": True, "pasos": pasos})[0])
    caso("no elimina la demo de fábrica → 404", 404, api("DELETE", f"/api/mis-vehiculos/{cliente}/demos/{de_fabrica['id_demo']}", usr)[0])
    caso("no usa las rutas de demos del admin → 403", 403, api("DELETE", f"/api/admin/demos/{de_fabrica['id_demo']}", usr)[0])
    status, r = api("POST", f"/api/mis-vehiculos/{cliente}/demos", usr, {"nombre": "E2E temporal", "activo": False, "pasos": pasos[:1]})
    caso("crea una demo nueva", 201, status)
    caso("elimina su demo nueva", 204, api("DELETE", f"/api/mis-vehiculos/{cliente}/demos/{r['id_demo']}", usr)[0])
    caso("no controla la unidad en stock (no es suya) → 404", 404,
         api("POST", f"/api/vehiculos/{stock}/detener", usr)[0])
    caso("no controla la exhibición con su cuenta de comprador → 404", 404,
         api("POST", f"/api/vehiculos/{fisica}/detener", usr)[0])

    print("\n3) Administrador y acceso temporal")
    caso("controla la unidad en stock", "permitido", control(api("POST", f"/api/vehiculos/{stock}/detener", adm)))
    caso("controla la exhibición", "permitido", control(api("POST", f"/api/vehiculos/{fisica}/detener", adm)))
    api("DELETE", f"/api/mis-vehiculos/{cliente}/soporte", usr)
    caso("NO controla la unidad vendida sin autorización → 403", 403, api("POST", f"/api/vehiculos/{cliente}/detener", adm)[0])
    caso("sí gestiona (ve) las demos de la unidad vendida", 200, api("GET", f"/api/admin/unidades/{cliente}/demos", adm)[0])
    status, r = api("POST", f"/api/mis-vehiculos/{cliente}/reportes", usr,
                    {"descripcion": "E2E: la garra no responde", "autorizar_soporte": True, "minutos": 15})
    caso("el usuario reporta la falla y autoriza acceso temporal", 201, status)
    id_reporte = r["id_reporte"]
    caso("con autorización, el admin controla la unidad vendida", "permitido", control(api("POST", f"/api/vehiculos/{cliente}/detener", adm)))
    caso("el admin pone el reporte en revisión", 200, api("PUT", f"/api/admin/reportes/{id_reporte}", adm, {"estado": "en_revision"})[0])
    caso("el admin resuelve (revoca el acceso)", 200,
         api("PUT", f"/api/admin/reportes/{id_reporte}", adm, {"estado": "resuelto", "respuesta": "Prueba E2E"})[0])
    caso("tras resolver, el admin vuelve a 403", 403, api("POST", f"/api/vehiculos/{cliente}/detener", adm)[0])
    caso("el usuario puede otorgar y revocar soporte manualmente", (201, 204),
         api("POST", f"/api/mis-vehiculos/{cliente}/soporte", usr, {"minutos": 15, "motivo": "E2E"})[0])
    api("DELETE", f"/api/mis-vehiculos/{cliente}/soporte", usr)
    caso("el admin ve la bitácora", 200, api("GET", "/api/admin/auditoria?por_pagina=5", adm)[0])
    caso("el admin ve los usuarios", 200, api("GET", "/api/admin/usuarios", adm)[0])

    fallas = [n for ok, n in resultados if not ok]
    print(f"\n{len(resultados) - len(fallas)}/{len(resultados)} casos correctos")
    sys.exit(1 if fallas else 0)


if __name__ == "__main__":
    main()
