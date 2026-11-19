-- V002 DOWN · Solo para entornos de prueba. Quita columnas y tablas nuevas;
-- vehiculos.modelo (texto) nunca se modificó, así que no se pierde información original.

ALTER TABLE comandos DROP CHECK ck_comandos_duracion;
ALTER TABLE comandos DROP COLUMN permitido_invitado, DROP COLUMN duracion_max_ms;

DROP TABLE IF EXISTS credenciales_dispositivo;

ALTER TABLE vehiculos DROP FOREIGN KEY fk_vehiculo_modelo;
ALTER TABLE vehiculos DROP INDEX uq_vehiculos_serie;
ALTER TABLE vehiculos DROP INDEX ix_vehiculos_modelo;
ALTER TABLE vehiculos DROP COLUMN tipo_unidad, DROP COLUMN numero_serie, DROP COLUMN id_modelo;

DROP TABLE IF EXISTS modelos_vehiculo;
