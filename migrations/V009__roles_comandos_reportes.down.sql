-- V009 DOWN · Solo para entornos de prueba. Falla a propósito si ya hay comandos propios con
-- código repetido (no se puede volver a crear uq_comando_codigo) o cuentas con rol 'invitado'.

DROP TABLE reportes_falla;

ALTER TABLE vehiculos DROP FOREIGN KEY fk_vehiculo_unidad_fisica;
ALTER TABLE vehiculos DROP INDEX ix_vehiculos_unidad_fisica;
ALTER TABLE vehiculos DROP COLUMN id_unidad_fisica;

ALTER TABLE comandos ADD UNIQUE KEY uq_comando_codigo (id_vehiculo, codigo);
ALTER TABLE comandos DROP CHECK ck_comandos_propietario;
ALTER TABLE comandos DROP FOREIGN KEY fk_comando_propietario;
ALTER TABLE comandos DROP INDEX ix_comandos_propietario;
ALTER TABLE comandos DROP INDEX uq_comando_codigo_fabrica;
ALTER TABLE comandos DROP COLUMN codigo_fabrica, DROP COLUMN id_propietario, DROP COLUMN tipo;

ALTER TABLE usuarios
  MODIFY COLUMN rol ENUM('admin', 'usuario') NOT NULL DEFAULT 'usuario';
