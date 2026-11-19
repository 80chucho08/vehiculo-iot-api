-- V004 DOWN · Solo para entornos de prueba. Las demos privadas creadas después de V004
-- quedarían sin dueño: este DOWN las conserva, pero pierden la marca de privadas.

ALTER TABLE demo_movimientos DROP CHECK ck_mov_pausa;
ALTER TABLE demo_movimientos DROP CHECK ck_mov_duracion;

ALTER TABLE demos DROP CHECK ck_demos_propietario;
ALTER TABLE demos DROP FOREIGN KEY fk_demo_propietario;
ALTER TABLE demos DROP INDEX ix_demos_propietario;
ALTER TABLE demos DROP COLUMN id_propietario, DROP COLUMN tipo;
