"""Primitivas de seguridad: contraseñas (Argon2id) y tokens de acceso (JWT HS256).

El token es corto (30 min) y lleva el `sid` de una fila de `sesiones_usuario`: la firma
evita falsificarlo y la fila permite revocarlo (logout, cambio de rol, desactivación).
El rol del token NO se usa para autorizar: siempre se relee el usuario de la base.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import settings

logger = logging.getLogger(__name__)

ALGORITMO = "HS256"
EMISOR = "vehiculo-iot-api"

if settings.auth_secret and len(settings.auth_secret) >= 32:
    _SECRETO = settings.auth_secret
else:
    if settings.auth_secret:
        logger.error("AUTH_SECRET es demasiado corto (mínimo 32 caracteres); se ignora.")
    logger.warning(
        "AUTH_SECRET no está configurado: se usa uno aleatorio y las sesiones "
        "caducarán al reiniciar la API."
    )
    _SECRETO = secrets.token_urlsafe(48)

_hasher = PasswordHasher()  # Argon2id con los parámetros recomendados por la librería

# Hash fijo para gastar el mismo tiempo cuando el email no existe (evita enumerar cuentas).
_HASH_SEÑUELO = _hasher.hash(secrets.token_urlsafe(16))


def ahora() -> datetime:
    """Hora UTC sin zona, igual que devuelve PyMySQL para columnas TIMESTAMP en sesión UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verificar_password(hash_guardado: str | None, password: str) -> bool:
    try:
        return _hasher.verify(hash_guardado or _HASH_SEÑUELO, password) and hash_guardado is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def necesita_rehash(hash_guardado: str) -> bool:
    try:
        return _hasher.check_needs_rehash(hash_guardado)
    except InvalidHashError:
        return False


@dataclass(frozen=True)
class DatosToken:
    sid: str
    id_usuario: int
    expira: datetime


def nuevo_sid() -> str:
    return str(uuid.uuid4())


def emitir_token(sid: str, id_usuario: int, expira: datetime) -> str:
    carga = {
        "iss": EMISOR,
        "sub": str(id_usuario),
        "sid": sid,
        "iat": int(ahora().replace(tzinfo=timezone.utc).timestamp()),
        "exp": int(expira.replace(tzinfo=timezone.utc).timestamp()),
    }
    return jwt.encode(carga, _SECRETO, algorithm=ALGORITMO)


def leer_token(token: str) -> DatosToken | None:
    """Devuelve los datos si la firma, el emisor y la expiración son válidos; si no, None."""
    try:
        carga = jwt.decode(
            token,
            _SECRETO,
            algorithms=[ALGORITMO],  # lista fija: rechaza "alg": "none" y algoritmos ajenos
            issuer=EMISOR,
            options={"require": ["exp", "sub", "sid", "iss"]},
        )
        return DatosToken(
            sid=str(carga["sid"]),
            id_usuario=int(carga["sub"]),
            expira=datetime.fromtimestamp(carga["exp"], timezone.utc).replace(tzinfo=None),
        )
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        return None


def vencimientos(inicio_sesion: datetime) -> tuple[datetime, datetime]:
    """(vencimiento del token nuevo, tope absoluto de la sesión)."""
    absoluto = inicio_sesion + timedelta(hours=settings.sesion_max_horas)
    return min(ahora() + timedelta(minutes=settings.token_minutos), absoluto), absoluto


def hash_ip(ip: str | None) -> str | None:
    """Huella de la IP con el secreto como sal: sirve para límites y auditoría sin guardar la IP."""
    if not ip:
        return None
    return hashlib.sha256(f"{_SECRETO}:{ip}".encode()).hexdigest()
