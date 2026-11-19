-- V004 · Demos de fábrica y privadas, y límites de seguridad física en los pasos.
-- demos.id_vehiculo sigue siendo NOT NULL: una demo de fábrica pertenece a una unidad y se
-- clona al dar de alta otra unidad del mismo modelo. Las demos existentes quedan como 'fabrica'.
-- Los topes de 10 s por paso y por pausa protegen aunque la API o el firmware fallen
-- (el firmware hoy no limita duracionMs en pasos de demo). Datos actuales: 1000 ms y 0 ms.
-- @requiere SELECT COUNT(*) = 0 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'demos' AND COLUMN_NAME IN ('tipo', 'id_propietario')
-- @requiere SELECT COUNT(*) = 0 FROM demo_movimientos WHERE duracion_ms > 10000 OR pausa_ms > 10000

ALTER TABLE demos
  ADD COLUMN tipo ENUM('fabrica', 'privada') NOT NULL DEFAULT 'fabrica' AFTER descripcion,
  ADD COLUMN id_propietario INT NULL COMMENT 'usuario dueño de una demo privada' AFTER tipo;

ALTER TABLE demos
  ADD KEY ix_demos_propietario (id_propietario),
  ADD CONSTRAINT fk_demo_propietario FOREIGN KEY (id_propietario) REFERENCES usuarios (id_usuario)
    ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE demos
  ADD CONSTRAINT ck_demos_propietario CHECK (
    (tipo = 'privada' AND id_propietario IS NOT NULL) OR (tipo = 'fabrica' AND id_propietario IS NULL)
  );

ALTER TABLE demo_movimientos
  ADD CONSTRAINT ck_mov_duracion CHECK (duracion_ms <= 10000),
  ADD CONSTRAINT ck_mov_pausa CHECK (pausa_ms <= 10000);

-- @comprobar SELECT COUNT(*) = 0 FROM demos WHERE tipo <> 'fabrica' OR id_propietario IS NOT NULL
