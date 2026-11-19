-- V001 · Usuarios (rol admin/usuario) y sesiones revocables del token de acceso (decisión D1).
-- El Invitado NO es un usuario: opera con sesiones_invitado (V005).
-- Solo crea tablas nuevas; no toca las tablas de 4.1/4.2.
-- @requiere SELECT COUNT(*) = 0 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ('usuarios', 'sesiones_usuario')

CREATE TABLE usuarios (
  id_usuario         INT NOT NULL AUTO_INCREMENT,
  email              VARCHAR(120) NOT NULL,
  nombre             VARCHAR(80) NOT NULL,
  password_hash      VARCHAR(255) NOT NULL COMMENT 'Argon2id/bcrypt; nunca la contraseña',
  rol                ENUM('admin', 'usuario') NOT NULL DEFAULT 'usuario',
  activo             TINYINT(1) NOT NULL DEFAULT 1,
  intentos_fallidos  SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  bloqueado_hasta    TIMESTAMP NULL DEFAULT NULL,
  ultimo_acceso      TIMESTAMP NULL DEFAULT NULL,
  creado_en          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  actualizado_en     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id_usuario),
  UNIQUE KEY uq_usuarios_email (email),
  CONSTRAINT ck_usuarios_email CHECK (email LIKE '%_@_%._%'),
  CONSTRAINT ck_usuarios_nombre CHECK (CHAR_LENGTH(TRIM(nombre)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE sesiones_usuario (
  id_sesion          BIGINT NOT NULL AUTO_INCREMENT,
  sid                CHAR(36) NOT NULL COMMENT 'UUID que viaja en el token; se conserva al renovar',
  id_usuario         INT NOT NULL,
  emitida_en         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expira_en          TIMESTAMP NOT NULL COMMENT 'vencimiento del token vigente (renovación deslizante)',
  expira_absoluto    TIMESTAMP NOT NULL COMMENT 'tope de 8 h desde el login',
  revocada_en        TIMESTAMP NULL DEFAULT NULL,
  motivo_revocacion  VARCHAR(40) NULL,
  PRIMARY KEY (id_sesion),
  UNIQUE KEY uq_sesiones_sid (sid),
  KEY ix_sesiones_usuario (id_usuario, revocada_en),
  CONSTRAINT fk_sesion_usuario FOREIGN KEY (id_usuario) REFERENCES usuarios (id_usuario)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT ck_sesiones_tope CHECK (expira_en <= expira_absoluto)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- @comprobar SELECT COUNT(*) = 2 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ('usuarios', 'sesiones_usuario')
