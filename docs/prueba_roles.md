# Prueba por roles (V009) · Invitado, Usuario y Administrador

Entorno preparado con `scripts/preparar_pruebas.py` sobre **un solo carrito físico** (ESP8266 de la unidad 1). Las contraseñas están en `cuentas_prueba.local.txt` (raíz del repo de la API, ignorado por git): entrégalas al profesor por un medio privado.

Sitio: <https://80chucho08.github.io/vehiculo-iot-front/> → **Administración** para iniciar sesión.

## Cuentas y recursos

| Cuenta | Rol | Recursos preparados |
|---|---|---|
| `invitado@carrito.test` | Invitado | Unidad 1 (exhibición): todos los movimientos 1-12 (grúa, garra, Control ON/OFF, Sonido, Velocidad x2, luz) y demos de fábrica |
| `usuario@carrito.test` | Usuario (comprador) | Unidad 2 «Excavadora IoT · Cliente» (vendida a él); comando propio «Subir garra suave»; demo privada «Saludo de garra»; demo de fábrica «Saludo de fábrica» (no editable) |
| `admin@carrito.test` | Administrador | Todo el sistema; unidad 3 «Excavadora IoT · Stock» disponible para vender |

**Dispositivo compartido:** las unidades 2 y 3 están registradas con *Dispositivo (ESP8266) = Compartido: el de la unidad 1*. La API reenvía sus órdenes al ESP8266 de la unidad 1 (reescribe `id_vehiculo`), así que el mismo carrito responde a las tres unidades sin reflashear. Se ve y se cambia en Inventario → Editar.

## Guion (resultado esperado)

### 1. Invitado
1. Inicia sesión → «Cuenta de invitado» → **Controlar el carrito en exhibición**: mueve grúa y garra, luz, demo de fábrica. ✔
2. Ve también Control ON/OFF, Sonido y Velocidad x2. Qué puede usar un invitado lo decide el admin en Inventario → Comandos → «Invitados»; uno desmarcado desaparece de su CONTROL y por API responde 403.
3. Cambia la URL a `?vehiculo=2` o `?vehiculo=3`: «Vehículo no encontrado» (404) y el WSS no conecta.
4. No tiene pestañas de administración ni «Mis vehículos»: no puede crear, editar ni eliminar comandos o demos (403).

### 2. Usuario
1. **Mis vehículos** muestra solo la unidad 2. **Controlar** → joysticks, botones y «Mis comandos» (★ Subir garra suave, se mantiene presionado).
2. **Comandos** → **Nuevo comando**: solo puede elegir movimientos ya registrados (1–12). Por API, un código no registrado (13, 99) responde 422. Puede **Eliminar** los suyos; los de fábrica no tienen botón y por API responden 404/403.
3. **Demos** → «Saludo de garra» → **Editar**: agregar/quitar/reordenar pasos (p. ej. «Garra abajo» y «Garra arriba»), guardar, ejecutar en CONTROL; DETENER la cancela. Las de fábrica no tienen Editar/Eliminar.
4. `?vehiculo=1` o `?vehiculo=3`: 404 (no son suyas).
5. **Reportar falla** → describe la falla y marca «Autorizar a la empresa…» (acceso temporal con caducidad). **Revocar soporte** lo retira cuando quiera.

### 3. Administrador
1. **Inventario**: unidades 1, 2 (vendida, comprador usuario@carrito.test) y 3 (disponible). **Comandos** (crear de fábrica, editar, activar/desactivar, invitados, tope; ve los «Propios» del comprador), **Demos** (crea de fábrica y edita/elimina cualquiera, incluso la privada del comprador), **Editar** (datos del carrito y dispositivo compartido), **Token ESP**.
2. CONTROL de la unidad 1 y 3: ✔. Unidad 2 sin autorización: **403** («se requiere su autorización de soporte vigente»).
3. Con el reporte del Usuario autorizado: **Reportes** → **Probar unidad** → controla la unidad 2 ✔ → **Resolver**: se revoca el acceso y vuelve el 403 (el WSS se cierra en el acto).
4. **Clientes**: crear cuentas (Comprador, Invitado o Administrador), cambiar rol, desactivar, restablecer contraseña. **Asignaciones**: vender la unidad 3 a un comprador. **Bitácora**: altas, soporte, reportes y accesos denegados.

## Verificación automática

```powershell
.venv\Scripts\python -m unittest discover tests
.venv\Scripts\python scripts\probar_roles_e2e.py --api https://vehiculo-iot-itp.duckdns.org --cuentas cuentas_prueba.local.txt
```

El E2E (48 casos) mueve brevemente la garra si el carrito está conectado y deja un reporte «E2E» resuelto en la bitácora.

## Despliegue de V009

1. Respaldo (`scripts/respaldo_bd.py`) y `scripts/migrar.py --bd vehiculo_iot --confirmar-produccion vehiculo_iot subir` (aditiva: rol `invitado`, comandos propios, `reportes_falla`, `vehiculos.id_unidad_fisica`). El código anterior de la API sigue funcionando con este esquema.
2. EC2: `git pull` y `Stop-ScheduledTask -TaskName "Vehiculo IoT API"; Start-ScheduledTask -TaskName "Vehiculo IoT API"`.
3. `scripts/preparar_pruebas.py --bd vehiculo_iot --confirmar-produccion vehiculo_iot` (idempotente; `--restablecer` regenera las contraseñas).

## Ensayo del flujo de venta (2026-11-24)

Hecho por la API con cuentas de ensayo (no con las del profesor), 21/21 pasos correctos. Lo creado se conserva:

| Paso | Pantalla | Resultado |
|---|---|---|
| 1 | Clientes → Nueva cuenta (rol Comprador) | `cliente.ensayo@carrito.test` |
| 2 | Inventario → Nueva unidad (tipo Venta) | Unidad 4 «Excavadora IoT · Ensayo», `CJ-PRUEBA-0004`, con 13 comandos y la demo de fábrica copiados de la unidad 1 |
| 3 | Inventario → Editar → Dispositivo (ESP8266) → Compartido: el de la unidad 1 | La unidad 4 usa el carrito físico de la unidad 1 |
| 4 | Asignaciones → Asignar unidad | Unidad 4 vendida al cliente; vender la unidad 1 (exhibición) responde 409 |
| 5 | Sesión del cliente → Mis vehículos → Controlar | Solo ve la unidad 4; luz, DETENER, WebSocket, comando propio e historial correctos |
| 6 | Permisos | El cliente recibe 404 en otra unidad; el admin recibe 403 en la unidad recién vendida |

Una unidad nueva nace con dispositivo **propio**: sin el paso 3 aparece «Sin conexión» y no mueve el carrito.
