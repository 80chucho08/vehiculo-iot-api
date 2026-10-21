# 🚗 API Vehículo IoT

API REST y WebSocket para administrar vehículos IoT, consultar su información y enviar comandos o secuencias de demostración en tiempo real. Está construida con **FastAPI** y **MySQL**.

> La API coordina las operaciones y la comunicación con los vehículos. La base de datos debe estar preparada con los procedimientos almacenados que utiliza el proyecto.

<p align="center">
  <img src="images\writingnotes.png" width="700" alt="Texto alternativo">
</p>

## ✨ Funcionalidades

- Administración de vehículos, comandos, demos y movimientos.
- Consultas de estado, comandos e historial.
- Registro de cambios de estado e historial de comandos.
- Conexiones WebSocket por vehículo para enviar actualizaciones.
- Ejecución y cancelación de demos mediante comandos enviados al vehículo.
- Documentación interactiva y comprobación de conexión a la base de datos.

## 🧰 Requisitos

- Python 3.10 o posterior.
- MySQL accesible desde el equipo donde se ejecuta la API.
- Esquema de base de datos y procedimientos almacenados requeridos por el proyecto.

## 🚀 Instalación y ejecución

Desde la carpeta raíz del proyecto, crea y activa un entorno virtual:

```bash
python -m venv .venv
```

En Windows (PowerShell):

```powershell
.\.venv\Scripts\Activate.ps1
```

En macOS o Linux:

```bash
source .venv/bin/activate
```

Instala las dependencias:

```bash
pip install -r requirements.txt
```

Configura las variables de entorno indicadas en la siguiente sección y arranca el servidor:

```bash
uvicorn app.main:app --reload
```

Por defecto, el servidor queda disponible en `http://127.0.0.1:8000`. La opción `--reload` es útil durante el desarrollo; para otros entornos, ejecútalo según la configuración de despliegue correspondiente.

## 🔐 Configuración

La aplicación carga la configuración desde las variables de entorno del sistema o desde un archivo `.env` en la raíz del proyecto. El archivo `.env` está excluido de Git: **no publiques contraseñas, hosts privados ni credenciales**. Configura localmente estas variables con los datos de tu entorno:

| Variable | Descripción | Valor predeterminado |
| --- | --- | --- |
| `DB_HOST` | Host o dirección del servidor MySQL | — |
| `DB_PORT` | Puerto de MySQL | `3306` |
| `DB_NAME` | Nombre de la base de datos | `vehiculo_iot` |
| `DB_USER` | Usuario de la base de datos | — |
| `DB_PASSWORD` | Contraseña de la base de datos | — |
| `DB_CONNECT_TIMEOUT` | Tiempo máximo de conexión, en segundos | `10` |
| `APP_NAME` | Nombre de la API | `API Vehiculo IoT` |
| `APP_ENV` | Identificador del entorno | `development` |

`DB_HOST`, `DB_USER` y `DB_PASSWORD` son obligatorias. Puedes definirlas en el entorno de tu sistema o en un `.env` local. No compartas ese archivo ni pegues sus valores en documentación, capturas o incidencias públicas. Si una credencial se publica por error, rótala.

## 📖 Documentación y estado

Con el servidor iniciado:

- **Swagger UI:** [`http://127.0.0.1:8000/docs`](http://127.0.0.1:8000/docs)
- **ReDoc:** [`http://127.0.0.1:8000/redoc`](http://127.0.0.1:8000/redoc)
- **Inicio:** `GET /`
- **Comprobación de MySQL:** `GET /health/db`

La comprobación de base de datos devuelve información básica de la conexión si tiene éxito; ante un fallo responde con `503`.

## 🧭 Rutas de la API

Los esquemas de solicitud y las respuestas se pueden consultar en `/docs`.

| Método | Ruta | Uso |
| --- | --- | --- |
| `GET` | `/api/vehiculos` | Listar vehículos |
| `GET` | `/api/vehiculos/{id_vehiculo}` | Consultar un vehículo |
| `GET` | `/api/vehiculos/{id_vehiculo}/comandos` | Consultar sus comandos |
| `GET` | `/api/vehiculos/{id_vehiculo}/estado` | Consultar su estado |
| `GET` | `/api/vehiculos/{id_vehiculo}/historial` | Consultar el historial |
| `GET` | `/api/vehiculos/{id_vehiculo}/demos` | Consultar sus demos |
| `GET` | `/api/demos/{id_demo}/movimientos` | Consultar movimientos de una demo |
| `POST`, `PUT`, `DELETE` | `/api/vehiculos`, `/api/vehiculos/{id_vehiculo}` | Crear, actualizar o eliminar vehículos |
| `POST`, `PUT`, `DELETE` | `/api/vehiculos/{id_vehiculo}/comandos`, `/api/comandos/{id_comando}` | Administrar comandos |
| `POST`, `PUT`, `DELETE` | `/api/vehiculos/{id_vehiculo}/demos`, `/api/demos/{id_demo}` | Administrar demos |
| `POST`, `PUT`, `DELETE` | `/api/demos/{id_demo}/movimientos`, `/api/movimientos-demo/{id_demo_movimiento}` | Administrar movimientos |
| `PUT` | `/api/vehiculos/{id_vehiculo}/estado` | Actualizar estado del vehículo |
| `POST` | `/api/vehiculos/{id_vehiculo}/historial` | Registrar un evento en el historial |
| `POST` | `/api/vehiculos/{id_vehiculo}/demos/{id_demo}/ejecutar` | Iniciar una demo; responde `202` |
| `POST` | `/api/vehiculos/{id_vehiculo}/demos/cancelar` | Cancelar la demo activa |
| WebSocket | `/ws/vehiculos/{id_vehiculo}` | Conectar un cliente al canal del vehículo |

Las solicitudes de creación y actualización usan JSON. La documentación interactiva muestra los campos requeridos, sus límites y ejemplos de respuesta disponibles para cada operación.

## 🔌 Comunicación WebSocket

El cliente del vehículo abre una conexión a `ws://127.0.0.1:8000/ws/vehiculos/{id_vehiculo}`. Al conectar, la API envía el estado almacenado y los cambios se distribuyen a los clientes conectados al mismo vehículo. La demo activa envía mensajes `comando_demo` con el comando, código, orden, duración y pausa; también informa el inicio, la finalización, la cancelación o un error.

Para ejecutar una demo, el vehículo debe estar conectado por WebSocket y la demo debe existir, estar activa y tener movimientos configurados. Al cancelarla, la API intenta enviar el comando de detener (`DETENER`, `STOP` o `ALTO`) si existe uno activo.

## 🗄️ Base de datos

Las rutas delegan las operaciones en procedimientos almacenados con prefijo `sp_`. El ejecutor permite únicamente procedimientos incluidos en una lista interna. Antes de usar la API, prepara el esquema y los procedimientos compatibles, además de los datos de vehículos, comandos y demos necesarios. Las credenciales no bastan si el servidor MySQL no contiene esa estructura.

## 🗂️ Estructura del proyecto

```text
app/
├── api/routes/       # Endpoints REST y WebSocket
├── core/             # Configuración y gestión de conexiones WebSocket
├── db/               # Conexión MySQL y ejecución de procedimientos
├── services/         # Lógica de ejecución de demos
└── main.py           # Configuración y arranque de FastAPI
requirements.txt      # Dependencias de Python
```

## 🛡️ Seguridad

- Mantén `.env` fuera del control de versiones; el repositorio ya lo excluye mediante `.gitignore`.
- Usa credenciales con los permisos mínimos necesarios y evita publicar endpoints de administración sin protección en redes públicas.
- `GET /health/db` y las rutas de la API no sustituyen la autenticación ni los controles de acceso del entorno donde se despliegue.
