# Autenticación y autorización (4.3 · Etapas 3, 4, 6, 7 y 8)

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
| Invitado, unidad de SU reserva vigente | ✔ | ✔ | ✔ (solo comandos `permitido_invitado` y demos de fábrica) |
| Invitado, cualquier otra unidad | 404 | 404 | 404 |

- **Acción DETENER** (`/detener` y `/demos/cancelar`): mismas reglas que OPERAR, pero la reserva de un invitado no la bloquea. Mientras hay una reserva vigente, **nadie más puede OPERAR** la unidad de exhibición (409), ni el admin; DETENER sí, siempre.
- **Demos privadas:** solo las ve, ve sus pasos y ejecuta su dueño (404 para cualquier otro, también para el admin con soporte). Las de fábrica, quien pueda ver u operar la unidad.

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
| `GET /api/admin/inventario` | admin | 200 (unidad, modelo, serie, tipo, comprador, dispositivo en línea) |
| `GET /api/admin/modelos` · `POST` · `PUT /{id}` | admin | 200/201 · 404 · 409 nombre repetido · 422 límites (100–10 000 ms, 1–50 pasos) |
| `POST /api/admin/unidades` `{nombre, id_modelo, numero_serie, tipo_unidad}` | admin | 201 `{id_vehiculo, plantilla, comandos_copiados, demos_copiadas}` · 404 modelo · 409 serie repetida o modelo inactivo · 422 |
| `PUT /api/admin/unidades/{id}` | admin | 200 · 404 · 409 (unidad vendida: no cambia de tipo ni se da de baja) |
| `PUT /api/admin/comandos/{id}` `{activo, permitido_invitado, duracion_max_ms}` | admin | 200 · 404 · 409 (solo códigos 1–12 para invitados) |
| `GET /api/admin/asignaciones?pagina&…&vigentes` | admin | 200 paginado |
| `POST /api/admin/asignaciones` `{id_vehiculo, id_usuario}` | admin | 201 · 404 · 409 (ya asignada, exhibición, baja, no es comprador) |
| `POST /api/admin/asignaciones/{id}/finalizar` `{motivo}` | admin | 200 · 404 · 409 (revoca el soporte vigente) |
| `GET /api/admin/dashboard` | admin | 200 métricas reales de la base y del WSS |
| `GET /api/admin/auditoria?pagina&accion&recurso&id_actor&resultado&desde&hasta` | admin | 200 paginado |

**Recuperación de contraseña:** no hay correo saliente, así que un administrador fija una contraseña temporal (`PUT /api/admin/usuarios/{id}/password`), lo que cierra las sesiones del usuario, y el usuario la cambia con `cambiar-password`. Para el propio admin, `scripts/crear_admin.py --restablecer`.

## Endpoints de las Etapas 6 a 8

| Método y ruta | Quién | Respuestas |
|---|---|---|
| `GET /api/mis-vehiculos/{id}/demos` | comprador dueño | 200 `{demos (fábrica + mis privadas, con pasos), limites, comandos}` · 404 |
| `POST /api/mis-vehiculos/{id}/demos` `{nombre, descripcion?, activo, pasos:[{id_comando, duracion_ms, pausa_ms}]}` | comprador dueño | 201 · 404 · 422 (detalle por paso) |
| `PUT /api/mis-vehiculos/{id}/demos/{id_demo}` (lista completa: agregar, quitar, editar y reordenar) | comprador dueño | 200 · 404 · 409 demo en curso · 422 |
| `DELETE /api/mis-vehiculos/{id}/demos/{id_demo}` | comprador dueño | 204 · 404 · 409 |
| `GET /api/catalogo` | público | 200 modelos publicados con su unidad de exhibición y disponibilidad (sin datos de compradores) |
| `POST /api/invitado/sesiones` `{id_vehiculo, acepto_condiciones}` | público | 201 `{access_token, expira_en, minutos}` · 400 sin aceptar · 404 no es exhibición · 409 ocupada · 429 límite por IP |
| `GET /api/invitado/sesion` · `DELETE /api/invitado/sesion` | invitado | 200 · 204 (libera y DETIENE la unidad) |
| `POST /api/ws/ticket` `{id_vehiculo}` | quien pueda MONITOREAR la unidad (incl. anónimo en transición) | 200 `{ticket, vigencia_s: 30}` · 401 · 403 · 404 · 409 |
| `POST /api/admin/unidades/{id}/token-dispositivo` | admin | 201 `{token}` (se muestra una vez; se guarda su SHA-256) · 404 |

**Seguridad física de las demos** (tres capas): la API valida al guardar y otra vez al ejecutar (límites del modelo, tope por comando, 100 ms ≤ paso, pausa ≤ 10 s, demo ≤ 120 s, códigos 1–12); la base tiene CHECK de 10 s (V004); el firmware rechaza pasos fuera de 100–10 000 ms.

**Invitados:** reserva de `INVITADO_MINUTOS` (5) con exclusividad garantizada por la base (UNIQUE sobre la reserva abierta). Termina y **DETIENE la unidad** al pulsar «Terminar prueba», al vencer (vigilante cada 5 s) o si el invitado se desconecta más de 15 s. Límite de `INVITADO_RESERVAS_POR_IP` (3) cada 15 min.

**WSS por unidad:**
- Navegador: pide un ticket de un solo uso (30 s) con su token y abre `/ws/vehiculos/{id}?ticket=…`; el token de sesión nunca va en la URL. Sin ticket solo entra un anónimo donde la regla lo permita (transición, unidad sin comprador). Rechazo: cierre **4401**.
- Cada 15 s todas las conexiones pasan otra vez por la regla con datos frescos: logout, cambio de rol, fin de asignación, retiro de soporte o fin de reserva cierran el canal con **4403**.
- ESP8266: cabecera `X-Dispositivo-Token` (o `?token=`), comparada con SHA-256 en tiempo constante. Un observador ya no puede hacerse pasar por el carrito ni desplazarlo.

## Pruebas

- `tests/test_autorizacion.py` (26, sin base): matriz de la regla; User A contra User B en 8 rutas; admin sin y con soporte; token inválido, alterado (`alg: none`, otra firma), vencido, revocado o con `sid` ajeno; rol inyectado en el token, la cabecera o la query; rutas de admin; login con mensaje uniforme, bloqueo y 429. Con una mutación que desactiva la regla, 16 de ellas fallan.
- `scripts/probar_auth_e2e.py` (39 casos contra la API real y MySQL de prueba): el flujo completo de admin → asignación → compradores → soporte → sesiones → auditoría.
- `tests/test_demos_privadas.py` (14) y `tests/test_invitados_wss.py` (16): privacidad de demos, validación física, regla del invitado, reservas, tickets, WSS con y sin credencial, revalidación y vigilante. Total de la suite: **94**.
- `scripts/probar_invitado_wss_e2e.py` (32 casos, ~45 s, WebSockets reales): catálogo, reserva exclusiva, límites del invitado, tickets de un solo uso, token del ESP8266, DETENER automático al vencer (historial con origen «sistema») y revocación en vivo del WSS al retirar el soporte.

## Despliegue en la EC2 (Etapas 3-4; para 6-8 ver `docs/despliegue_4.3.md`)

1. `git pull`
2. `.venv\Scripts\python -m pip install -r requirements.txt` (nuevas: PyJWT y argon2-cffi).
3. Agregar al `.env` de la EC2 una línea `AUTH_SECRET=` con un valor generado por `python -c "import secrets; print(secrets.token_urlsafe(48))"`. No va a git. Sin ella la API arranca, pero las sesiones caducan en cada reinicio.
4. `Stop-ScheduledTask -TaskName "Vehiculo IoT API"; Start-ScheduledTask -TaskName "Vehiculo IoT API"`
5. Crear el primer administrador: `.venv\Scripts\python scripts\crear_admin.py tu@correo --nombre "Tu nombre"`
6. En `/docs`: `POST /api/auth/login` → **Authorize** con el token → `GET /api/admin/dashboard`.

`AUTH_OBLIGATORIA` se queda en `false` hasta que el frontend tenga login (Etapas 5–7).
