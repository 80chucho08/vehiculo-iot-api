-- V002 · Modelo comercial separado de la unidad física, credencial del dispositivo y
-- límites/permisos por comando.
-- `vehiculos` sigue siendo la unidad física: no cambia ningún id_vehiculo.
-- Todas las columnas nuevas aceptan NULL o tienen DEFAULT, así que sp_crear_vehiculo y
-- sp_crear_comando (INSERT con lista de columnas) siguen funcionando. Por el SELECT * de
-- los SP de lectura, las columnas nuevas aparecerán en GET /api/vehiculos y /comandos:
-- ninguna es secreta. El hash del token del dispositivo va en una tabla aparte (R14).
-- @requiere SELECT COUNT(*) = 0 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'vehiculos' AND COLUMN_NAME IN ('id_modelo', 'numero_serie', 'tipo_unidad')
-- @requiere SELECT COUNT(*) = 0 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'comandos' AND COLUMN_NAME IN ('duracion_max_ms', 'permitido_invitado')
-- @requiere SELECT COUNT(*) = 0 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ('modelos_vehiculo', 'credenciales_dispositivo')

CREATE TABLE modelos_vehiculo (
  id_modelo             INT NOT NULL AUTO_INCREMENT,
  nombre                VARCHAR(80) NOT NULL,
  descripcion           VARCHAR(255) NULL,
  microcontrolador      VARCHAR(50) NULL,
  duracion_max_paso_ms  INT UNSIGNED NOT NULL DEFAULT 3000 COMMENT 'tope por paso de demo para este modelo',
  pasos_max_demo        SMALLINT UNSIGNED NOT NULL DEFAULT 20,
  publicado             TINYINT(1) NOT NULL DEFAULT 0 COMMENT '1 = visible en el catálogo público',
  activo                TINYINT(1) NOT NULL DEFAULT 1,
  creado_en             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id_modelo),
  UNIQUE KEY uq_modelos_nombre (nombre),
  CONSTRAINT ck_modelos_duracion CHECK (duracion_max_paso_ms BETWEEN 100 AND 10000),
  CONSTRAINT ck_modelos_pasos CHECK (pasos_max_demo BETWEEN 1 AND 50)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE vehiculos
  ADD COLUMN id_modelo INT NULL AFTER modelo,
  ADD COLUMN numero_serie VARCHAR(40) NULL AFTER id_modelo,
  ADD COLUMN tipo_unidad ENUM('venta', 'exhibicion', 'simulada') NOT NULL DEFAULT 'venta' AFTER numero_serie;

ALTER TABLE vehiculos
  ADD UNIQUE KEY uq_vehiculos_serie (numero_serie),
  ADD KEY ix_vehiculos_modelo (id_modelo),
  ADD CONSTRAINT fk_vehiculo_modelo FOREIGN KEY (id_modelo) REFERENCES modelos_vehiculo (id_modelo)
    ON DELETE RESTRICT ON UPDATE CASCADE;

-- Datos: un modelo por cada texto distinto de vehiculos.modelo (hoy hay uno).
INSERT INTO modelos_vehiculo (nombre, descripcion, microcontrolador, publicado)
SELECT v.modelo, MIN(v.descripcion), MIN(v.microcontrolador), 1
  FROM vehiculos v
 WHERE v.modelo IS NOT NULL AND TRIM(v.modelo) <> ''
 GROUP BY v.modelo;

UPDATE vehiculos v
  JOIN modelos_vehiculo m ON m.nombre = v.modelo
   SET v.id_modelo = m.id_modelo
 WHERE v.id_modelo IS NULL;

UPDATE vehiculos
   SET numero_serie = CONCAT('UNI-', LPAD(id_vehiculo, 4, '0'))
 WHERE numero_serie IS NULL;

-- Decisión D4: la unidad 1 (carrito propio) es la de exhibición.
UPDATE vehiculos SET tipo_unidad = 'exhibicion' WHERE id_vehiculo = 1;

CREATE TABLE credenciales_dispositivo (
  id_vehiculo   INT NOT NULL,
  token_hash    CHAR(64) NOT NULL COMMENT 'SHA-256 hex del token del ESP8266; el token nunca se guarda',
  creado_en     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  rotado_en     TIMESTAMP NULL DEFAULT NULL,
  revocado_en   TIMESTAMP NULL DEFAULT NULL,
  PRIMARY KEY (id_vehiculo),
  CONSTRAINT fk_credencial_vehiculo FOREIGN KEY (id_vehiculo) REFERENCES vehiculos (id_vehiculo)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE comandos
  ADD COLUMN duracion_max_ms INT UNSIGNED NULL AFTER descripcion,
  ADD COLUMN permitido_invitado TINYINT(1) NOT NULL DEFAULT 0 AFTER duracion_max_ms;

ALTER TABLE comandos
  ADD CONSTRAINT ck_comandos_duracion CHECK (duracion_max_ms IS NULL OR duracion_max_ms BETWEEN 100 AND 10000);

-- Invitados: movimientos y luz. Quedan fuera 5 (Control ON/OFF), 6 (Sonido), 7 (Velocidad x2)
-- y 13 (demo local del firmware). El administrador puede cambiarlo después.
UPDATE comandos SET permitido_invitado = 1 WHERE codigo IN (1, 2, 3, 4, 8, 9, 10, 11, 12);

-- @comprobar SELECT COUNT(*) = 0 FROM vehiculos WHERE numero_serie IS NULL
-- @comprobar SELECT COUNT(*) = 0 FROM vehiculos WHERE modelo IS NOT NULL AND TRIM(modelo) <> '' AND id_modelo IS NULL
-- @comprobar SELECT COUNT(*) = 0 FROM vehiculos WHERE id_vehiculo = 1 AND tipo_unidad <> 'exhibicion'
