from __future__ import annotations

from datetime import datetime

import pymysql
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.deps import _NO_AUTENTICADO, actor_actual, auditar, ip_hash, requiere_sesion
from app.core import seguridad
from app.repositories import auditoria, sesiones, usuarios
from app.services import auth
from app.services.autorizacion import Actor

router = APIRouter(prefix="/api/auth", tags=["Autenticación"])

PATRON_EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class LoginEntrada(BaseModel):
    email: str = Field(min_length=3, max_length=120, pattern=PATRON_EMAIL)
    password: str = Field(min_length=1, max_length=128)


class UsuarioSalida(BaseModel):
    id_usuario: int
    email: str
    nombre: str
    rol: str


class TokenSalida(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expira_en: datetime
    expira_absoluto: datetime
    usuario: UsuarioSalida


class CambioPassword(BaseModel):
    actual: str = Field(min_length=1, max_length=128)
    nueva: str = Field(min_length=10, max_length=128)


def _salida(sesion: auth.SesionEmitida) -> TokenSalida:
    return TokenSalida(
        access_token=sesion.token,
        expira_en=sesion.expira_en,
        expira_absoluto=sesion.expira_absoluto,
        usuario=UsuarioSalida(**sesion.usuario),
    )


@router.post(
    "/login",
    response_model=TokenSalida,
    responses={401: {"description": "Credenciales inválidas"}, 429: {"description": "Demasiados intentos"}},
)
def login(datos: LoginEntrada, request: Request) -> TokenSalida:
    huella = ip_hash(request)
    email = datos.email.strip().lower()
    try:
        sesion = auth.iniciar_sesion(email, datos.password, huella)
    except auth.DemasiadosIntentos as error:
        auditoria.registrar("auth.login_limitado", "sesion", id_actor=None, rol_actor="sistema",
                            resultado="denegado", ip_hash=huella)
        raise HTTPException(429, "Demasiados intentos. Espera unos minutos.",
                            headers={"Retry-After": str(error.reintentar_en)}) from None
    except auth.CredencialesInvalidas as error:
        auditoria.registrar("auth.login_fallido", "sesion", id_actor=error.id_usuario, rol_actor="sistema",
                            resultado="denegado", detalle={"motivo": error.motivo}, ip_hash=huella)
        # Mismo mensaje para todos los casos: no revela si el email existe o está bloqueado.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email o contraseña incorrectos.",
                            headers=_NO_AUTENTICADO) from None
    except pymysql.MySQLError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No fue posible iniciar sesión.") from None
    auditoria.registrar("auth.login", "sesion", id_actor=sesion.usuario["id_usuario"],
                        rol_actor=sesion.usuario["rol"], ip_hash=huella)
    return _salida(sesion)


@router.post("/renovar", response_model=TokenSalida, responses={401: {"description": "Sesión vencida"}})
def renovar(actor: Actor = Depends(requiere_sesion)) -> TokenSalida:
    """Cambia un token vigente por otro de 30 min, sin pasar el tope de 8 h desde el login."""
    try:
        return _salida(auth.renovar(actor))
    except auth.CredencialesInvalidas:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "La sesión llegó a su duración máxima. Inicia sesión de nuevo.",
                            headers=_NO_AUTENTICADO) from None


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, actor: Actor = Depends(actor_actual)) -> Response:
    """Revoca la sesión en el servidor. Sin token, no hace nada (idempotente)."""
    if actor.autenticado:
        auth.cerrar_sesion(actor)
        auditar(request, actor, "auth.logout", "sesion")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/yo", response_model=UsuarioSalida)
def yo(actor: Actor = Depends(requiere_sesion)) -> UsuarioSalida:
    return UsuarioSalida(id_usuario=actor.id_usuario, email=actor.email, nombre=actor.nombre, rol=actor.rol)


@router.post("/cambiar-password", status_code=status.HTTP_204_NO_CONTENT,
             responses={400: {"description": "Contraseña actual incorrecta"}})
def cambiar_password(datos: CambioPassword, request: Request, actor: Actor = Depends(requiere_sesion)) -> Response:
    """Cambia la contraseña y cierra TODAS las sesiones del usuario (también la actual)."""
    fila = usuarios.por_email_con_hash(actor.email)
    if fila is None or not seguridad.verificar_password(fila["password_hash"], datos.actual):
        auditar(request, actor, "auth.cambiar_password", "usuario", actor.id_usuario, resultado="denegado")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La contraseña actual no es correcta.")
    usuarios.cambiar_password(actor.id_usuario, seguridad.hash_password(datos.nueva))
    sesiones.revocar_de_usuario(actor.id_usuario, "cambio_password")
    auditar(request, actor, "auth.cambiar_password", "usuario", actor.id_usuario)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
