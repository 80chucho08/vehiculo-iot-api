"""Dependencias de autenticación y autorización para las rutas.

Uso:
  actor: Actor = Depends(actor_actual)            # anónimo si no hay cabecera Authorization
  actor: Actor = Depends(requiere_admin)          # 401 sin sesión, 403 si no es admin
  actor: Actor = Depends(acceso_vehiculo(Accion.OPERAR))   # lee {id_vehiculo} de la ruta
"""

from __future__ import annotations

from typing import Any, Callable

import pymysql
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core import seguridad
from app.repositories import auditoria, propiedad
from app.repositories.base import una
from app.services import auth
from app.services.autorizacion import ANONIMO, Accion, Actor, evaluar

# auto_error=False: sin cabecera el actor es anónimo; Swagger muestra el botón "Authorize".
_esquema_bearer = HTTPBearer(auto_error=False, description="Token de POST /api/auth/login")

_NO_AUTENTICADO = {"WWW-Authenticate": "Bearer"}


def ip_hash(request: Request) -> str | None:
    """Caddy añade la IP del cliente al final de X-Forwarded-For; sin proxy, la del socket."""
    reenviada = request.headers.get("x-forwarded-for")
    ip = reenviada.split(",")[-1].strip() if reenviada else (request.client.host if request.client else None)
    return seguridad.hash_ip(ip)


def actor_actual(
    credenciales: HTTPAuthorizationCredentials | None = Depends(_esquema_bearer),
) -> Actor:
    if credenciales is None:
        return ANONIMO
    try:
        actor = auth.actor_desde_token(credenciales.credentials)
    except pymysql.MySQLError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No fue posible validar la sesión.") from None
    if actor is None:
        # Un token presente pero inválido NO degrada a anónimo: se rechaza.
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Sesión inválida o vencida. Inicia sesión de nuevo.", headers=_NO_AUTENTICADO
        )
    return actor


def requiere_sesion(actor: Actor = Depends(actor_actual)) -> Actor:
    if not actor.autenticado:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Inicia sesión.", headers=_NO_AUTENTICADO)
    return actor


def requiere_admin(request: Request, actor: Actor = Depends(requiere_sesion)) -> Actor:
    if actor.rol != "admin":
        auditar(request, actor, "acceso.denegado", "ruta", request.url.path, resultado="denegado",
                detalle={"motivo": "requiere_admin", "metodo": request.method})
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Solo el administrador puede realizar esta acción.")
    return actor


def requiere_usuario(request: Request, actor: Actor = Depends(requiere_sesion)) -> Actor:
    if actor.rol != "usuario":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Disponible solo para cuentas de comprador.")
    return actor


def auditar(
    request: Request,
    actor: Actor,
    accion: str,
    recurso: str,
    id_recurso: Any = None,
    *,
    resultado: str = "ok",
    detalle: dict[str, Any] | None = None,
) -> None:
    auditoria.registrar(
        accion,
        recurso,
        id_actor=actor.id_usuario,
        rol_actor=actor.rol if actor.rol in ("admin", "usuario") else "invitado",
        id_recurso=id_recurso,
        resultado=resultado,
        detalle=detalle,
        ip_hash=ip_hash(request),
    )


def autorizar_unidad(request: Request, actor: Actor, id_vehiculo: int, accion: Accion) -> None:
    try:
        unidad = propiedad.unidad(id_vehiculo)
        soporte = (
            actor.rol == "admin"
            and accion is not Accion.VER
            and unidad is not None
            and unidad.get("id_propietario") is not None
            and propiedad.soporte_vigente(id_vehiculo, actor.id_usuario) is not None
        )
    except pymysql.MySQLError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No fue posible verificar permisos.") from None
    decision = evaluar(actor, unidad, accion, soporte_vigente=soporte)
    if decision.permitido:
        if decision.via == "soporte":
            auditar(request, actor, f"soporte.{accion.value}", "vehiculo", id_vehiculo,
                    detalle={"metodo": request.method, "ruta": request.url.path})
        return
    if accion is not Accion.VER and unidad is not None:
        auditar(request, actor, "acceso.denegado", "vehiculo", id_vehiculo, resultado="denegado",
                detalle={"accion": accion.value, "status": decision.status, "ruta": request.url.path})
    headers = _NO_AUTENTICADO if decision.status == 401 else None
    raise HTTPException(decision.status, decision.detalle, headers=headers)


def admin_auditado(accion: str, recurso: str, parametro: str | None = None) -> Callable[..., Any]:
    """Exige admin y deja constancia en la auditoría del resultado de la operación.

    Con `yield`, el código posterior corre cuando la ruta ya terminó: si lanzó una excepción,
    se registra como 'error' (con el código HTTP) y se vuelve a lanzar.
    """

    def dependencia(request: Request, actor: Actor = Depends(requiere_admin)):
        id_recurso = request.path_params.get(parametro) if parametro else None
        detalle = {"metodo": request.method, "ruta": request.url.path}
        try:
            yield actor
        except HTTPException as error:
            auditar(request, actor, accion, recurso, id_recurso, resultado="error",
                    detalle={**detalle, "status": error.status_code})
            raise
        except Exception:
            auditar(request, actor, accion, recurso, id_recurso, resultado="error", detalle=detalle)
            raise
        auditar(request, actor, accion, recurso, id_recurso, detalle=detalle)

    return dependencia


def acceso_vehiculo(accion: Accion) -> Callable[..., Actor]:
    """Dependencia para rutas con {id_vehiculo}."""

    def dependencia(id_vehiculo: int, request: Request, actor: Actor = Depends(actor_actual)) -> Actor:
        autorizar_unidad(request, actor, id_vehiculo, accion)
        return actor

    return dependencia


def acceso_demo(accion: Accion) -> Callable[..., Actor]:
    """Dependencia para rutas con {id_demo}: autoriza sobre la unidad dueña de la demo."""

    def dependencia(id_demo: int, request: Request, actor: Actor = Depends(actor_actual)) -> Actor:
        try:
            fila = una("SELECT id_vehiculo FROM demos WHERE id_demo = %s", (id_demo,))
        except pymysql.MySQLError:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No fue posible verificar permisos.") from None
        if fila is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Demo no encontrada.")
        autorizar_unidad(request, actor, int(fila["id_vehiculo"]), accion)
        return actor

    return dependencia
