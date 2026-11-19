-- V006 · Bitácora de auditoría: altas, asignaciones, cambios de rol, demos, soporte,
-- sesiones de invitado y accesos denegados. Solo se inserta: el usuario de la aplicación
-- recibe INSERT y SELECT sobre esta tabla, nunca UPDATE ni DELETE (ver docs/privilegios_rds.sql).
-- @requiere SELECT COUNT(*) = 0 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'auditoria'

CREATE TABLE auditoria (
  id_auditoria  BIGINT NOT NULL AUTO_INCREMENT,
  fecha         TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  id_actor      INT NULL COMMENT 'NULL si actúa un invitado, el sistema o el dispositivo',
  rol_actor     ENUM('admin', 'usuario', 'invitado', 'sistema', 'dispositivo') NOT NULL,
  accion        VARCHAR(60) NOT NULL COMMENT 'p. ej. vehiculo.alta, asignacion.crear, auth.login_fallido',
  recurso       VARCHAR(40) NOT NULL,
  id_recurso    VARCHAR(40) NULL,
  resultado     ENUM('ok', 'denegado', 'error') NOT NULL DEFAULT 'ok',
  detalle       JSON NULL COMMENT 'sin contraseñas, tokens ni datos personales innecesarios',
  ip_hash       CHAR(64) NULL,
  PRIMARY KEY (id_auditoria),
  KEY ix_auditoria_fecha (fecha),
  KEY ix_auditoria_actor (id_actor, fecha),
  KEY ix_auditoria_recurso (recurso, id_recurso),
  KEY ix_auditoria_accion (accion, fecha),
  CONSTRAINT fk_auditoria_actor FOREIGN KEY (id_actor) REFERENCES usuarios (id_usuario)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- @comprobar SELECT COUNT(*) = 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'auditoria'
