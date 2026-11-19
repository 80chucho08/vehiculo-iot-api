# Autenticación y autorización (4.3 · Etapas 3 y 4)

## Modelo

- **Token de acceso corto** (decisión D1): JWT HS256 de 30 min firmado con `AUTH_SECRET`. Lleva `sub` (id de usuario) y `sid` (fila de `sesiones_usuario`). **No lleva rol**: el rol se relee de la base en cada petición, así que un cambio de rol surte efecto de inmediato.
- **Renovación deslizante** con `POST /api/auth/renovar`, con un tope absoluto de 8 h desde el login. No hay token de refresco.
- **Revocación real**: `logout`, cambio de contraseña, cambio de rol o desactivación marcan la sesión como revocada, y el token deja de servir aunque no haya vencido.
- **Contraseñas** con Argon2id. Login con el mismo mensaje para email inexistente, contraseña errónea, cuenta inactiva o bloqueada. Bloqueo de 15 min tras 5 fallos y límite de 10 intentos cada 5 min por IP (429 con `Retry-After`).
- **Sin cookies**: el token viaja en `Authorization: Bearer …` y el frontend lo guarda en `sessionStorage` (Etapa 5). CORS permite `Authorization` y `DELETE` solo desde `https://80chucho08.github.io`; **CORS no sustituye la autorización**.

## Regla de autorización por unidad (`app/services/autorizacion.py`)

| Actor | VER (detalle, comandos, demos) | MONITOREAR (estado, historial) | OPERAR (ejecutar, DETENER, demos) |
|---|---|---|---|
| Comprador, unidad propia (asignación vigente) | ✔ | ✔ | ✔ |
| Comprador, unidad ajena | 404 | 404 | 404 |
| Admin, unidad sin comprador (inventario, exhibición) | ✔ | ✔ | ✔ |
| Admin, unidad vendida | ✔ | 403 sin soporte · ✔ con soporte vigente (auditado) | 403 sin soporte · ✔ con soporte |
| Anónimo con `AUTH_OBLIGATORIA=false` (transición) | ✔ sin comprador · 404 vendida | igual | igual |
| Anónimo con `AUTH_OBLIGATORIA=true` | 401 | 401 | 401 |

- La unidad ajena responde **404, idéntico al de una inexistente**, para que no se pueda averiguar qué IDs están vendidos (BOLA/IDOR).
- `id_usuario`, rol y propietario **nunca** se toman del cliente: el actor sale del token y la base; el propietario sale de `asignaciones`.
- Rutas solo para admin, siempre (aunque sea la transición): CRUD de vehículos, comandos, demos y pasos, `GET /api/vehiculos`, `PUT /estado`, `POST /historial` y todo `/api/admin/*`. Cada operación queda en `auditoria` con su resultado.
- Pendiente para la Etapa 8: el WebSocket todavía no exige token (ticket de un solo uso y token del ESP8266).

## Endpoints nuevos

| Método y ruta | Quién | Respuestas |
|---|---|---|
| `POST /api/auth/login` `{email, password}` | público | 200 `{access_token, token_type, expira_en, expira_absoluto, usuario}` · 401 · 422 · 429 |
| `POST /api/auth/renovar` | sesión | 200 (token nuevo) · 401 |
| `POST /api/auth/logout` | cualquiera | 204 |
| `GET /api/auth/yo` | sesión | 200 · 401 |
| `POST /api/auth/cambiar-password` `{actual, nueva}` | sesión | 204 (cierra todas las sesiones) · 400 · 401 · 422 |
| `GET /api/mis-vehiculos` | comprador | 200 `{total, vehiculos}` · 401 · 403 |
| `POST /api/mis-vehiculos/{id}/soporte` `{minutos 15–1440, motivo, id_admin?}` | comprador dueño | 201 · 404 · 422 |
| `DELETE /api/mis-vehiculos/{id}/soporte` | comprador dueño | 204 · 404 |
| `GET /api/admin/usuarios?pagina&por_pagina&rol&activo&buscar` | admin | 200 `{total, pagina, por_pagina, items}` |
| `POST /api/admin/usuarios` `{email, nombre, password, rol}` | admin | 201 · 409 email repetido · 422 |
| `PUT /api/admin/usuarios/{id}` `{nombre, rol, activo}` | admin | 200 · 404 · 409 último admin |
| `PUT /api/admin/usuarios/{id}/password` `{password}` | admin | 204 (recuperación de acceso) · 404 |
| `GET /api/admin/inventario` | admin | 200 |
| `GET /api/admin/asignaciones?pagina&…&vigentes` | admin | 200 paginado |
| `POST /api/admin/asignaciones` `{id_vehiculo, id_usuario}` | admin | 201 · 404 · 409 (ya asignada, exhibición, baja, no es comprador) |
| `POST /api/admin/asignaciones/{id}/finalizar` `{motivo}` | admin | 200 · 404 · 409 (revoca el soporte vigente) |
| `GET /api/admin/dashboard` | admin | 200 métricas reales de la base y del WSS |
| `GET /api/admin/auditoria?pagina&accion&recurso&id_actor&resultado&desde&hasta` | admin | 200 paginado |

**Recuperación de contraseña:** no hay correo saliente, así que un administrador fija una contraseña temporal (`PUT /api/admin/usuarios/{id}/password`), lo que cierra las sesiones del usuario, y el usuario la cambia con `cambiar-password`. Para el propio admin, `scripts/crear_admin.py --restablecer`.

## Pruebas

- `tests/test_autorizacion.py` (26, sin base): matriz de la regla; User A contra User B en 8 rutas; admin sin y con soporte; token inválido, alterado (`alg: none`, otra firma), vencido, revocado o con `sid` ajeno; rol inyectado en el token, la cabecera o la query; rutas de admin; login con mensaje uniforme, bloqueo y 429. Con una mutación que desactiva la regla, 16 de ellas fallan.
- `scripts/probar_auth_e2e.py` (39 casos contra la API real y MySQL de prueba): el flujo completo de admin → asignación → compradores → soporte → sesiones → auditoría.

## Despliegue en la EC2

1. `git pull`
2. `.venv\Scripts\python -m pip install -r requirements.txt` (nuevas: PyJWT y argon2-cffi).
3. Agregar al `.env` de la EC2 una línea `AUTH_SECRET=` con un valor generado por `python -c "import secrets; print(secrets.token_urlsafe(48))"`. No va a git. Sin ella la API arranca, pero las sesiones caducan en cada reinicio.
4. `Stop-ScheduledTask -TaskName "Vehiculo IoT API"; Start-ScheduledTask -TaskName "Vehiculo IoT API"`
5. Crear el primer administrador: `.venv\Scripts\python scripts\crear_admin.py tu@correo --nombre "Tu nombre"`
6. En `/docs`: `POST /api/auth/login` → **Authorize** con el token → `GET /api/admin/dashboard`.

`AUTH_OBLIGATORIA` se queda en `false` hasta que el frontend tenga login (Etapas 5–7).
