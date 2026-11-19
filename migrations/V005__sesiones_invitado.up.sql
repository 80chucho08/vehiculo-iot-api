-- V005 · Sesiones temporales de Invitado = reserva EXCLUSIVA de una unidad de exhibición.
-- Una sola reserva abierta por unidad (columna generada + UNIQUE). Una reserva vencida sigue
-- "abierta" hasta que alguien la cierra: antes de crear una nueva, la API debe cerrar las
-- vencidas (UPDATE ... SET finalizada_en = NOW(), motivo_fin = 'expirada' WHERE expira_en <= NOW()).
-- Que la unidad sea de tipo 'exhibicion' se valida en la API.
-- @requiere SELECT COUNT(*) = 0 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'sesiones_invitado'

CREATE TABLE sesiones_invitado (
  id_sesion_invitado  BIGINT NOT NULL AUTO_INCREMENT,
  sid                 CHAR(36) NOT NULL COMMENT 'UUID que viaja en el token del invitado',
  id_vehiculo         INT NOT NULL,
  inicio              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expira_en           TIMESTAMP NOT NULL,
  finalizada_en       TIMESTAMP NULL DEFAULT NULL,
  motivo_fin          ENUM('expirada', 'finalizada', 'desconexion', 'revocada') NULL,
  ip_hash             CHAR(64) NULL COMMENT 'SHA-256 con sal de la IP, para límites de tasa',
  vehiculo_reservado  INT GENERATED ALWAYS AS (IF(finalizada_en IS NULL, id_vehiculo, NULL)) STORED,
  PRIMARY KEY (id_sesion_invitado),
  UNIQUE KEY uq_invitado_sid (sid),
  UNIQUE KEY uq_reserva_activa (vehiculo_reservado),
  KEY ix_invitado_vehiculo (id_vehiculo, inicio),
  KEY ix_invitado_ip (ip_hash, inicio),
  CONSTRAINT fk_invitado_vehiculo FOREIGN KEY (id_vehiculo) REFERENCES vehiculos (id_vehiculo)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT ck_invitado_vigencia CHECK (expira_en > inicio),
  CONSTRAINT ck_invitado_cierre CHECK ((finalizada_en IS NULL) = (motivo_fin IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- @comprobar SELECT COUNT(*) = 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'sesiones_invitado'
