-- V007 · Quién originó cada fila del historial (usuario o sesión de invitado) e índice para
-- "los últimos N movimientos de una unidad". Las 896+ filas existentes quedan con NULL
-- (origen desconocido, anterior a 4.3): no se inventa autoría.
-- sp_registrar_historial inserta con lista de columnas: no se rompe.
-- @requiere SELECT COUNT(*) = 0 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'historial_comandos' AND COLUMN_NAME IN ('id_usuario', 'id_sesion_invitado')
-- @requiere SELECT COUNT(*) = 2 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ('usuarios', 'sesiones_invitado')

ALTER TABLE historial_comandos
  ADD COLUMN id_usuario INT NULL AFTER origen,
  ADD COLUMN id_sesion_invitado BIGINT NULL AFTER id_usuario;

ALTER TABLE historial_comandos
  ADD KEY ix_historial_vehiculo_id (id_vehiculo, id_historial),
  ADD KEY ix_historial_usuario (id_usuario),
  ADD KEY ix_historial_sesion_invitado (id_sesion_invitado),
  ADD CONSTRAINT fk_historial_usuario FOREIGN KEY (id_usuario) REFERENCES usuarios (id_usuario)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  ADD CONSTRAINT fk_historial_sesion_invitado FOREIGN KEY (id_sesion_invitado)
    REFERENCES sesiones_invitado (id_sesion_invitado) ON DELETE RESTRICT ON UPDATE RESTRICT,
  ADD CONSTRAINT ck_historial_un_actor CHECK (id_usuario IS NULL OR id_sesion_invitado IS NULL);

-- @comprobar SELECT COUNT(*) = 0 FROM historial_comandos WHERE id_usuario IS NOT NULL OR id_sesion_invitado IS NOT NULL
