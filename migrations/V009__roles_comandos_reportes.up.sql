-- V009 · Requerimientos de la prueba por roles:
--   1. Cuenta de Invitado (rol 'invitado' en usuarios): solo controla unidades de exhibición.
--   2. Comandos propios del usuario: alias de un movimiento físico YA registrado (mismo código),
--      con su nombre. Solo los de fábrica conservan un código único por unidad.
--   3. Reportes de falla del comprador sobre su unidad (el soporte temporal ya existe desde V003).
--   4. Dispositivo compartido: una unidad registrada puede usar el ESP8266 de otra unidad
--      (un solo carrito físico registrado varias veces para pruebas). Que no se apunte a sí
--      misma lo valida la API: MySQL no admite CHECK sobre una columna AUTO_INCREMENT.
-- No borra filas ni cambia IDs: los comandos existentes quedan como 'fabrica'.
-- @requiere SELECT COUNT(*) = 0 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'comandos' AND COLUMN_NAME IN ('tipo', 'id_propietario', 'codigo_fabrica')
-- @requiere SELECT COUNT(*) = 0 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'vehiculos' AND COLUMN_NAME = 'id_unidad_fisica'
-- @requiere SELECT COUNT(*) = 0 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'reportes_falla'

ALTER TABLE usuarios
  MODIFY COLUMN rol ENUM('admin', 'usuario', 'invitado') NOT NULL DEFAULT 'usuario';

ALTER TABLE comandos
  ADD COLUMN tipo ENUM('fabrica', 'usuario') NOT NULL DEFAULT 'fabrica' AFTER permitido_invitado,
  ADD COLUMN id_propietario INT NULL COMMENT 'usuario dueño de un comando propio' AFTER tipo,
  ADD COLUMN codigo_fabrica INT GENERATED ALWAYS AS (IF(tipo = 'fabrica', codigo, NULL)) STORED;

-- Un código físico por unidad entre los de fábrica; los comandos propios lo repiten a propósito.
ALTER TABLE comandos
  ADD UNIQUE KEY uq_comando_codigo_fabrica (id_vehiculo, codigo_fabrica),
  ADD KEY ix_comandos_propietario (id_propietario),
  ADD CONSTRAINT fk_comando_propietario FOREIGN KEY (id_propietario) REFERENCES usuarios (id_usuario)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  ADD CONSTRAINT ck_comandos_propietario CHECK (
    (tipo = 'usuario' AND id_propietario IS NOT NULL) OR (tipo = 'fabrica' AND id_propietario IS NULL)
  );

ALTER TABLE comandos DROP INDEX uq_comando_codigo;

ALTER TABLE vehiculos
  ADD COLUMN id_unidad_fisica INT NULL COMMENT 'usa el dispositivo (ESP8266) de esta otra unidad' AFTER tipo_unidad;

ALTER TABLE vehiculos
  ADD KEY ix_vehiculos_unidad_fisica (id_unidad_fisica),
  ADD CONSTRAINT fk_vehiculo_unidad_fisica FOREIGN KEY (id_unidad_fisica) REFERENCES vehiculos (id_vehiculo)
    ON DELETE RESTRICT ON UPDATE RESTRICT;

CREATE TABLE reportes_falla (
  id_reporte      INT NOT NULL AUTO_INCREMENT,
  id_vehiculo     INT NOT NULL,
  id_usuario      INT NOT NULL COMMENT 'comprador que reporta',
  descripcion     VARCHAR(500) NOT NULL,
  estado          ENUM('abierto', 'en_revision', 'resuelto') NOT NULL DEFAULT 'abierto',
  creado_en       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  actualizado_en  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  atendido_por    INT NULL,
  respuesta       VARCHAR(500) NULL,
  resuelto_en     TIMESTAMP NULL DEFAULT NULL,
  PRIMARY KEY (id_reporte),
  KEY ix_reportes_vehiculo (id_vehiculo, estado),
  KEY ix_reportes_usuario (id_usuario),
  KEY ix_reportes_estado (estado, creado_en),
  KEY ix_reportes_atendido_por (atendido_por),
  CONSTRAINT fk_reporte_vehiculo FOREIGN KEY (id_vehiculo) REFERENCES vehiculos (id_vehiculo)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_reporte_usuario FOREIGN KEY (id_usuario) REFERENCES usuarios (id_usuario)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_reporte_atendido_por FOREIGN KEY (atendido_por) REFERENCES usuarios (id_usuario)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT ck_reporte_descripcion CHECK (CHAR_LENGTH(TRIM(descripcion)) >= 5),
  CONSTRAINT ck_reporte_resuelto CHECK ((estado = 'resuelto') = (resuelto_en IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- @comprobar SELECT COUNT(*) = 0 FROM comandos WHERE tipo <> 'fabrica' OR id_propietario IS NOT NULL
-- @comprobar SELECT COUNT(*) = 0 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'comandos' AND INDEX_NAME = 'uq_comando_codigo'
-- @comprobar SELECT COUNT(*) = 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'reportes_falla'
