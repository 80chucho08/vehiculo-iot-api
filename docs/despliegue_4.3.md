# Despliegue y prueba por equipos · 4.3 ADMINISTRACIÓN (Etapas 6 a 9)

Estado al cerrar el desarrollo: base de producción migrada (V001–V008, sin migraciones nuevas en estas etapas); API 1.2 con 61 operaciones; 94 pruebas unitarias; E2E: autorización 39/39, invitados y WSS 32/32, esquema 38/38.

## 1. Orden de despliegue (no cambiar el orden)

El código nuevo es compatible con el sitio publicado actual mientras `AUTH_OBLIGATORIA=false` y `DISPOSITIVO_TOKEN_OBLIGATORIO=false`. Las banderas se activan al final, cuando todo lo demás funciona.

1. **API (EC2):** `git pull` y reiniciar la tarea. No hay dependencias nuevas.
   ```powershell
   Stop-ScheduledTask -TaskName "Vehiculo IoT API"; Start-ScheduledTask -TaskName "Vehiculo IoT API"
   ```
   Verifica `/docs` (deben aparecer «Catálogo e invitados» y «WebSocket»).
2. **Frontend (GitHub Pages):** push. Espera unos minutos y abre cada página con **Ctrl+Shift+R** (Pages guarda caché 10 min y el navegador podría mezclar módulos viejos y nuevos).
3. **Prueba rápida sin tocar banderas:** CONTROL y MONITOREO de la unidad 1 siguen funcionando sin sesión; ADMINISTRACIÓN funciona; el Catálogo reserva y la prueba termina sola.
4. **Token del ESP8266:** ADMINISTRACIÓN → Inventario → unidad 1 → **Token ESP**. Copia el token (se muestra una sola vez) en `wifi_excavadora/credenciales_dispositivo.h`:
   ```c
   #define DISPOSITIVO_ID_VEHICULO 1
   #define DISPOSITIVO_TOKEN "el-token-copiado"
   ```
   Compila y sube el firmware desde Arduino IDE. En el Monitor Serie debe verse el handshake WSS correcto. Desde que la unidad tiene token, la API rechaza (4401) cualquier conexión de dispositivo sin él.
5. **Activar la seguridad completa** en el `.env` de la EC2 y reiniciar la tarea:
   ```
   AUTH_OBLIGATORIA=true
   DISPOSITIVO_TOKEN_OBLIGATORIO=true
   ```
   Desde aquí: CONTROL/MONITOREO de la unidad de exhibición exigen una prueba de invitado vigente o una sesión de admin; un anónimo recibe 401 y en el WSS «Sin acceso».

**Reversión:** basta poner las dos banderas en `false` y reiniciar. El firmware con token sigue funcionando con la bandera en `false`.

## 2. Unidad vendida del compañero (prueba por equipos)

1. Tu admin: **Modelos** (si su carrito es de otro modelo, regístralo) → **Inventario → Nueva unidad** con tipo **Venta** y su número de serie → **Token ESP** de esa unidad.
2. **Clientes → Nueva cuenta** (rol Comprador) para tu compañero → **Asignaciones → Asignar unidad**.
3. Tu compañero pone en su firmware `DISPOSITIVO_ID_VEHICULO` = id de la unidad nueva, el token y `WS_HOST` = tu dominio (`vehiculo-iot-itp.duckdns.org`).
4. Entra a ADMINISTRACIÓN con su cuenta → **Mis vehículos → Controlar**.

## 3. Guion de la prueba por equipos (tres roles)

Condiciones físicas para cada prueba con el carrito: una persona supervisando, zona despejada, una sola persona controlando a la vez, DETENER a la vista.

| # | Quién | Qué hace | Resultado esperado |
|---|---|---|---|
| 1 | Invitado (sin cuenta) | Catálogo → Probar 5 minutos → mueve la grúa | Solo ve movimientos, luz y demos de fábrica; cuenta regresiva visible |
| 2 | Otro invitado (otro navegador) | Intenta reservar la misma unidad | «En uso por otro invitado» (409) |
| 3 | Admin | Intenta mover la unidad reservada | 409; **DETENER sí funciona** |
| 4 | Invitado | Espera a que termine el tiempo | El carrito se detiene solo; vuelve al catálogo; unidad libre |
| 5 | Comprador A | Mis vehículos → Controlar su unidad; crea una demo privada y la ejecuta; DETENER a mitad | La demo se cancela al instante |
| 6 | Comprador B | Pega en la URL `?vehiculo=` de la unidad de A | «Vehículo no encontrado» (404) en REST y «Sin acceso» en el WSS |
| 7 | Admin | Monitorea la unidad de A sin permiso | 403; luego A autoriza soporte → el admin entra; A lo revoca → en ≤ 15 s el WSS del admin se cierra |
| 8 | Admin | Bitácora | Altas, asignación, soporte, reservas y accesos denegados de las pruebas anteriores |
| 9 | Cualquiera | MONITOREO de su unidad tras recargar | Último movimiento y 10 movimientos de RDS, sin pasos de demo |

## 4. Evidencias sugeridas para la entrega

- Salida de `python -m unittest discover tests` (94 OK) y de los tres scripts E2E contra `vehiculo_iot_prueba`.
- `scripts/migrar.py --bd vehiculo_iot estado` (V001–V008 aplicadas) y el manifiesto del respaldo previo.
- Capturas: catálogo, CONTROL de invitado con cuenta regresiva, editor de demos con un error de validación, Bitácora con accesos denegados, Monitor Serie del ESP8266 rechazando un paso > 10 s.
- `/docs` mostrando los grupos de endpoints.

## 5. Límites conocidos (documentados, no bloquean)

- Estado en memoria (tickets, demos en curso, límites de intentos): un solo proceso uvicorn. Un reinicio cierra los WSS (los clientes se reconectan) y deja de contar intentos.
- El historial no registra quién originó cada movimiento (las columnas `id_usuario`/`id_sesion_invitado` existen desde V007, pero `sp_registrar_historial` no las llena); la Bitácora sí registra las acciones administrativas.
- La API sigue conectándose a RDS con `admin`; `docs/privilegios_rds.sql` tiene la propuesta de usuarios con privilegios mínimos.
- «En línea» del dispositivo significa WSS abierto; la ejecución física sigue sin telemetría propia.
