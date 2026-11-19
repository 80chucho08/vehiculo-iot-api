-- V008 DOWN · Restaura las acciones originales de las FK (ver docs/esquema_base_2026-11-18.sql).
-- Vuelve a permitir el borrado en cascada del historial: solo para entornos de prueba.

ALTER TABLE comandos DROP FOREIGN KEY fk_comando_vehiculo;
ALTER TABLE comandos ADD CONSTRAINT fk_comando_vehiculo FOREIGN KEY (id_vehiculo)
  REFERENCES vehiculos (id_vehiculo) ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE demos DROP FOREIGN KEY fk_demo_vehiculo;
ALTER TABLE demos ADD CONSTRAINT fk_demo_vehiculo FOREIGN KEY (id_vehiculo)
  REFERENCES vehiculos (id_vehiculo) ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE estado_vehiculo DROP FOREIGN KEY fk_estado_vehiculo;
ALTER TABLE estado_vehiculo ADD CONSTRAINT fk_estado_vehiculo FOREIGN KEY (id_vehiculo)
  REFERENCES vehiculos (id_vehiculo) ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE historial_comandos DROP FOREIGN KEY fk_historial_comando;
ALTER TABLE historial_comandos ADD CONSTRAINT fk_historial_comando FOREIGN KEY (id_comando)
  REFERENCES comandos (id_comando) ON DELETE SET NULL ON UPDATE CASCADE;

ALTER TABLE historial_comandos DROP FOREIGN KEY fk_historial_vehiculo;
ALTER TABLE historial_comandos ADD CONSTRAINT fk_historial_vehiculo FOREIGN KEY (id_vehiculo)
  REFERENCES vehiculos (id_vehiculo) ON DELETE CASCADE ON UPDATE CASCADE;
