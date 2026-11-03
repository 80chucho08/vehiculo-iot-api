# API Vehículo IoT

API REST y WebSocket para administrar vehículos IoT, consultar su información y enviar comandos o secuencias de demostración en tiempo real. Está construida con FastAPI, PyMySQL y MySQL.

La API coordina las operaciones; el esquema, los datos y los procedimientos almacenados de MySQL deben existir previamente. La comunicación en tiempo real se realiza con WebSocket y requiere que el cliente del vehículo mantenga una conexión abierta.

## Requisitos

- Python 3.10 o posterior.
- MySQL accesible desde el equipo donde corre la API.
- Esquema y procedimientos almacenados compatibles (ver [Base de datos](#base-de-datos)).

## Instalación y ejecución

```bash
python -m venv .venv
```

Activa el entorno:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS o Linux
source .venv/bin/activate
```

Instala dependencias y arranca el servidor:

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Por defecto escucha en `http://127.0.0.1:8000`. Para exponerlo en la red local, configura el host de Uvicorn según el despliegue, por ejemplo `--host 0.0.0.0`. `--reload` es para desarrollo.

La especificación interactiva está en [Swagger UI](http://127.0.0.1:8000/docs) y [ReDoc](http://127.0.0.1:8000/redoc).

## Configuración

La configuración se lee de las variables de entorno o de `.env` en la raíz. El archivo `.env` está excluido de Git. No publiques credenciales.

| Variable | Descripción | Valor predeterminado |
| --- | --- | --- |
| `DB_HOST` | Host del servidor MySQL | Obligatoria |
| `DB_PORT` | Puerto MySQL | `3306` |
| `DB_NAME` | Base de datos | `vehiculo_iot` |
| `DB_USER` | Usuario MySQL | Obligatoria |
| `DB_PASSWORD` | Contraseña MySQL | Obligatoria |
| `DB_CONNECT_TIMEOUT` | Tiempo máximo de conexión, segundos | `10` |
| `APP_NAME` | Título de la API en OpenAPI | `API Vehiculo IoT` |
| `APP_ENV` | Entorno reportado por `GET /` | `development` |

Ejemplo de archivo local (reemplaza los valores; no lo subas al repositorio):

```dotenv
DB_HOST=localhost
DB_PORT=3306
DB_NAME=vehiculo_iot
DB_USER=usuario
DB_PASSWORD=contraseña
DB_CONNECT_TIMEOUT=10
APP_NAME=API Vehiculo IoT
APP_ENV=development
```

## Funcionamiento

- Las rutas HTTP consultan o modifican información mediante procedimientos almacenados. Las modificaciones confirman la transacción si concluyen correctamente y hacen rollback ante error MySQL.
- Las consultas y escrituras devuelven JSON. Un error de validación de FastAPI normalmente responde `422`; los errores de base de datos suelen responder `500`.
- El executor acepta únicamente los procedimientos incluidos en una lista permitida interna.
- La API no autentica ni autoriza solicitudes. Protege el acceso con controles apropiados antes de exponerla fuera de un entorno confiable.
- `GET /health/db` comprueba MySQL; no comprueba que todos los procedimientos requeridos estén instalados.

## Endpoints HTTP

Todos los endpoints siguientes usan el prefijo indicado; no se requiere autenticación. Los campos se validan con los modelos descritos en las tablas. Los campos opcionales pueden omitirse o enviarse como `null`.

### General

| Método y ruta | Descripción | Respuesta y errores |
| --- | --- | --- |
| `GET /` | Indica que la API está activa. | `200`: `mensaje`, `entorno`, `documentacion`. |
| `GET /health/db` | Consulta la base de datos y su versión. | `200`: `conexion`, `base_datos`, `version_mysql`; `503` si falla la conexión. |

### Consultas

| Método y ruta | Descripción | Respuesta |
| --- | --- | --- |
| `GET /api/vehiculos` | Lista vehículos. | `total`, `vehiculos`. |
| `GET /api/vehiculos/{id_vehiculo}` | Obtiene un vehículo por ID. | Objeto del vehículo; `404` si no existe. |
| `GET /api/vehiculos/{id_vehiculo}/comandos` | Lista comandos del vehículo. | `total`, `comandos`. |
| `GET /api/vehiculos/{id_vehiculo}/estado` | Consulta el estado almacenado y la conexión WebSocket en vivo. | `total`, `estado`, `conectado_websocket`. |
| `GET /api/vehiculos/{id_vehiculo}/historial` | Consulta el historial del vehículo. | `total`, `historial`. |
| `GET /api/vehiculos/{id_vehiculo}/demos` | Lista demos asociadas al vehículo. | `total`, `demos`. |
| `GET /api/demos/{id_demo}/movimientos` | Lista los movimientos de una demo. | `total`, `movimientos`. |

### Administración

Las respuestas de escritura contienen `mensaje` y `resultados` (filas del primer conjunto de resultados que devuelva el procedimiento). Un resultado vacío no implica necesariamente que el ID exista: esa regla depende del procedimiento de la base.

| Método y ruta | Campos del cuerpo JSON | Procedimiento |
| --- | --- | --- |
| `POST /api/vehiculos` (201) | `nombre` requerido (1–80); `modelo` opcional (máx. 80); `descripcion` opcional (máx. 255); `microcontrolador` opcional (máx. 50). | `sp_crear_vehiculo` |
| `PUT /api/vehiculos/{id_vehiculo}` | Los mismos campos, además de `activo` booleano requerido. | `sp_actualizar_vehiculo` |
| `DELETE /api/vehiculos/{id_vehiculo}` | Sin cuerpo. | `sp_eliminar_vehiculo` |
| `POST /api/vehiculos/{id_vehiculo}/comandos` (201) | `nombre` requerido (1–60); `codigo` entero requerido; `descripcion` opcional (máx. 255). | `sp_crear_comando` |
| `PUT /api/comandos/{id_comando}` | Los mismos campos, además de `activo` booleano requerido. | `sp_actualizar_comando` |
| `DELETE /api/comandos/{id_comando}` | Sin cuerpo. | `sp_eliminar_comando` |
| `POST /api/vehiculos/{id_vehiculo}/demos` (201) | `nombre` requerido (1–80); `descripcion` opcional (máx. 255). | `sp_crear_demo` |
| `PUT /api/demos/{id_demo}` | `nombre` requerido (1–80); `descripcion` opcional (máx. 255); `activo` booleano requerido. | `sp_actualizar_demo` |
| `DELETE /api/demos/{id_demo}` | Sin cuerpo. | `sp_eliminar_demo` |
| `POST /api/demos/{id_demo}/movimientos` (201) | `id_comando` entero; `orden` entero ≥ 1; `duracion_ms` y `pausa_ms` enteros ≥ 0. Todos requeridos. | `sp_agregar_movimiento_demo` |
| `PUT /api/movimientos-demo/{id_demo_movimiento}` | Mismos campos que al crear movimiento. | `sp_actualizar_movimiento_demo` |
| `DELETE /api/movimientos-demo/{id_demo_movimiento}` | Sin cuerpo. | `sp_eliminar_movimiento_demo` |

Ejemplo para crear vehículo:

```json
{
  "nombre": "Vehiculo 01",
  "modelo": "Prototipo",
  "descripcion": "Vehículo de pruebas",
  "microcontrolador": "ESP32"
}
```

### Estado e historial

| Método y ruta | Cuerpo JSON | Comportamiento |
| --- | --- | --- |
| `PUT /api/vehiculos/{id_vehiculo}/estado` | `conectado` booleano requerido; `id_comando` entero opcional o `null`. | Llama a `sp_actualizar_estado`. Si se proporciona `id_comando`, registra además historial con origen `api`. Después emite `estado_actualizado` a los WebSocket de ese vehículo. |
| `POST /api/vehiculos/{id_vehiculo}/historial` (201) | `origen` requerido (1–30); `id_comando` entero opcional o `null`. | Registra un evento mediante `sp_registrar_historial`. |

Ejemplo:

```json
{
  "id_comando": 3,
  "conectado": true
}
```

### Ejecución de demos

| Método y ruta | Descripción | Respuestas relevantes |
| --- | --- | --- |
| `POST /api/vehiculos/{id_vehiculo}/comandos/{id_comando}/ejecutar` | Ejecuta un comando individual y espera ACK del firmware. | `200`: firmware despachó la acción; `404`: vehículo/comando inexistente o ajeno; `409`: inactivo, demo ocupada o dispositivo desconectado; `504`: no llegó ACK. |
| `POST /api/vehiculos/{id_vehiculo}/detener` | Envía el código físico `0` para apagar todas las salidas. | `200` con ACK; `409` si falta el dispositivo o hay una demo activa; `504` si vence la espera del ACK. |
| `POST /api/vehiculos/{id_vehiculo}/demos/{id_demo}/ejecutar` | Inicia la demo en segundo plano y espera ACK por inicio y movimiento. | `202`: estado `en_ejecucion`; `404` si la demo no existe o no pertenece al vehículo; `409` si está inactiva, no tiene movimientos, no hay dispositivo conectado o hay otra operación incompatible. |
| `POST /api/vehiculos/{id_vehiculo}/demos/cancelar` | Cancela la demo activa para ese vehículo. | `200`: estado `cancelada`; `409` si no hay demo en ejecución. |

La secuencia se ordena por `orden`. Para cada movimiento se envía el comando, se espera el ACK y se mantienen los tiempos `duracion_ms` y `pausa_ms`. La ejecución vive en memoria del proceso y no persiste su estado. Una demo y un comando individual no pueden ejecutarse a la vez en el mismo proceso.

## WebSocket

Conecta el ESP8266 con rol de dispositivo. El firmware del vehículo 1 usa:

```text
wss://vehiculo-iot-itp.duckdns.org/ws/vehiculos/1?rol=dispositivo
```

Los clientes de interfaz se conectan con `?rol=observador` (o sin parámetro). El servidor solo entrega mensajes físicos al rol `dispositivo`. Al aceptar la conexión, consulta MySQL y envía `estado_inicial`. El dispositivo envía ACK como mensajes de texto JSON; los ACK indican despacho en el firmware, no una confirmación de sensores físicos.

| `tipo` | Cuándo se envía | Campos principales |
| --- | --- | --- |
| `estado_inicial` | Al conectar. | `id_vehiculo`, `estado` (filas de estado), `demos` (demos del vehículo). |
| `estado_actualizado` | Tras actualizar estado por REST. | `id_vehiculo`, `estado.id_comando`, `estado.conectado`. |
| `demo_iniciada` | Al comenzar una demo. | `id_vehiculo`, `id_demo`, `nombre`, `total_movimientos`. |
| `comando_demo` | Por cada movimiento de demo. | `id_demo_movimiento`, `id_solicitud`, `orden`, `id_comando`, `comando`, `codigo`, `duracion_ms`, `pausa_ms`. |
| `demo_completada` | Al terminar todos los movimientos. | `id_vehiculo`, `id_demo`, `total_movimientos`. |
| `demo_cancelada` | Al cancelar. | `id_vehiculo`, `id_demo`. |
| `demo_error` | Si se interrumpe la ejecución. | `id_vehiculo`, `id_demo`, `mensaje`. |
| `error` | Si no se puede leer el estado inicial desde MySQL. | `mensaje`; luego se cierra con código 1011. |

El mensaje `comando_individual` contiene `id_vehiculo`, `id_solicitud`, `id_comando`, `comando` y el `codigo` entero. No lleva duración. `codigo: 0` apaga todas las salidas; `codigo: 13` se rechaza como orden individual. Las demos conservan `comando_demo` y sus tiempos.

Conversión aplicada por el firmware: códigos `1`–`9` se convierten en caracteres `'1'`–`'9'`; `10` en `'a'`; `11` en `'b'`; `12` en `'c'`; `13` en `'d'`. Los códigos 10–13 no se convierten en bytes ambiguos: se validan como enteros y se mapean explícitamente. El 13 solo representa la demo local y no se admite en `comando_individual` ni como movimiento normal de una demo del servidor.

El dispositivo responde a una orden con `{"tipo":"comando_ack","id_vehiculo":1,"id_solicitud":"...","estado":"procesado"}` o `estado:"rechazado"`. Los movimientos de demo incluyen también `id_demo` e `id_demo_movimiento`; su ACK incluye esos identificadores. El inicio de demo recibe `demo_ack` con `id_demo` y `estado:"aceptado"` o `"rechazado"`. `procesado` significa que se llamó a la lógica de salida del firmware, no que un sensor confirmara el movimiento.

Al cancelar una demo, la API manda el código `0` verificado en `carro2.ino` y luego notifica `demo_cancelada`. El firmware aplica también código `0` al perder WSS. El transporte requiere una CA raíz válida: `WSS_ROOT_CA_PEM` vacío mantiene WSS deshabilitado. Configura ahí el PEM de la CA raíz que valide la cadena de Caddy; no uses `setInsecure()` ni copies el certificado hoja como raíz.

## Base de datos

La aplicación necesita procedimientos almacenados compatibles con las operaciones listadas abajo. Los procedimientos deben aceptar los parámetros en el orden usado por la API y devolver filas con los nombres de columnas que consumen las consultas y el ejecutor de demos.

| Procedimientos requeridos |
| --- |
| `sp_crear_vehiculo`, `sp_obtener_vehiculos`, `sp_obtener_vehiculo`, `sp_actualizar_vehiculo`, `sp_eliminar_vehiculo` |
| `sp_crear_comando`, `sp_obtener_comandos_vehiculo`, `sp_actualizar_comando`, `sp_eliminar_comando` |
| `sp_actualizar_estado`, `sp_obtener_estado`, `sp_registrar_historial`, `sp_obtener_historial` |
| `sp_crear_demo`, `sp_obtener_demos`, `sp_actualizar_demo`, `sp_eliminar_demo` |
| `sp_agregar_movimiento_demo`, `sp_obtener_movimientos_demo`, `sp_actualizar_movimiento_demo`, `sp_eliminar_movimiento_demo` |

El repositorio no incluye un script de esquema o instalación de procedimientos; deben prepararse en MySQL antes de usar las rutas que los requieren.

## Estructura

```text
app/
├── api/routes/       # Endpoints REST y WebSocket
├── core/             # Configuración y gestión de conexiones WebSocket
├── db/               # Conexión MySQL y ejecución de procedimientos
├── services/         # Lógica de ejecución de demos
└── main.py           # Configuración y arranque de FastAPI
requirements.txt      # Dependencias fijadas
README.md             # Guía de ejecución y referencia de API
```
