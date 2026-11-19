-- V003 · Propiedad de unidades (asignaciones con historial) y autorizaciones de soporte.
-- Exclusividad en BD: una sola asignación ACTIVA por unidad mediante una columna generada
-- + UNIQUE (MySQL no considera NULL en UNIQUE, así que las asignaciones cerradas no chocan).
-- MySQL prohíbe CASCADE/SET NULL en FK sobre columnas base de una columna generada STORED,
-- por eso todas las FK son RESTRICT (los IDs nunca cambian y no se borran filas).
-- Que una unidad de exhibición no pueda asignarse a un comprador se valida en la API.
-- @requiere SELECT COUNT(*) = 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'usuarios'
-- @requiere SELECT COUNT(*) = 0 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ('asignaciones', 'autorizaciones_soporte')

CREATE TABLE asignaciones (
  id_asignacion    INT NOT NULL AUTO_INCREMENT,
  id_vehiculo      INT NOT NULL,
  id_usuario       INT NOT NULL COMMENT 'comprador',
  asignado_por     INT NOT NULL COMMENT 'administrador que registró la venta',
  inicio           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  fin              TIMESTAMP NULL DEFAULT NULL COMMENT 'NULL = asignación vigente',
  finalizado_por   INT NULL,
  motivo_fin       VARCHAR(255) NULL,
  vehiculo_activo  INT GENERATED ALWAYS AS (IF(fin IS NULL, id_vehiculo, NULL)) STORED,
  PRIMARY KEY (id_asignacion),
  UNIQUE KEY uq_asignacion_activa (vehiculo_activo),
  KEY ix_asignaciones_usuario (id_usuario, fin),
  KEY ix_asignaciones_vehiculo (id_vehiculo, inicio),
  KEY ix_asignaciones_asignado_por (asignado_por),
  KEY ix_asignaciones_finalizado_por (finalizado_por),
  CONSTRAINT fk_asig_vehiculo FOREIGN KEY (id_vehiculo) REFERENCES vehiculos (id_vehiculo)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_asig_usuario FOREIGN KEY (id_usuario) REFERENCES usuarios (id_usuario)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_asig_asignado_por FOREIGN KEY (asignado_por) REFERENCES usuarios (id_usuario)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_asig_finalizado_por FOREIGN KEY (finalizado_por) REFERENCES usuarios (id_usuario)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT ck_asig_fechas CHECK (fin IS NULL OR fin >= inicio),
  CONSTRAINT ck_asig_cierre CHECK ((fin IS NULL) = (finalizado_por IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Consentimiento explícito del comprador para que un administrador opere su unidad.
CREATE TABLE autorizaciones_soporte (
  id_autorizacion  INT NOT NULL AUTO_INCREMENT,
  id_vehiculo      INT NOT NULL,
  id_usuario       INT NOT NULL COMMENT 'comprador que otorga el acceso',
  id_admin         INT NULL COMMENT 'NULL = cualquier administrador',
  motivo           VARCHAR(255) NOT NULL,
  otorgada_en      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expira_en        TIMESTAMP NOT NULL,
  revocada_en      TIMESTAMP NULL DEFAULT NULL,
  PRIMARY KEY (id_autorizacion),
  KEY ix_soporte_vigente (id_vehiculo, revocada_en, expira_en),
  KEY ix_soporte_usuario (id_usuario),
  KEY ix_soporte_admin (id_admin),
  CONSTRAINT fk_soporte_vehiculo FOREIGN KEY (id_vehiculo) REFERENCES vehiculos (id_vehiculo)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_soporte_usuario FOREIGN KEY (id_usuario) REFERENCES usuarios (id_usuario)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_soporte_admin FOREIGN KEY (id_admin) REFERENCES usuarios (id_usuario)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT ck_soporte_vigencia CHECK (expira_en > otorgada_en)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- @comprobar SELECT COUNT(*) = 2 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ('asignaciones', 'autorizaciones_soporte')
