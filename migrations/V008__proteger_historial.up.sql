-- V008 · Cierra el riesgo R12/R13 también en la base: un DELETE físico de un vehículo o de
-- un comando con historial ya no se propaga, MySQL lo rechaza (error 1451).
-- Antes: vehiculos → comandos/demos/estado/historial en CASCADE, y comandos → historial en
-- SET NULL (NULL significa DETENER). Ahora: RESTRICT al borrar; ON UPDATE CASCADE se conserva.
-- No cambia datos. Se cambia cada FK en dos sentencias (quitar y volver a crear con el mismo
-- nombre); entre ambas hay un instante sin esa FK, aceptable con la API en baja lógica.
-- demo_movimientos → demos sigue en CASCADE: borrar una demo borra sus pasos, no historial.
-- @requiere SELECT COUNT(*) = 5 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE() AND CONSTRAINT_NAME IN ('fk_comando_vehiculo', 'fk_demo_vehiculo', 'fk_estado_vehiculo', 'fk_historial_vehiculo', 'fk_historial_comando') AND DELETE_RULE IN ('CASCADE', 'SET NULL')

ALTER TABLE historial_comandos DROP FOREIGN KEY fk_historial_vehiculo;
ALTER TABLE historial_comandos ADD CONSTRAINT fk_historial_vehiculo FOREIGN KEY (id_vehiculo)
  REFERENCES vehiculos (id_vehiculo) ON DELETE RESTRICT ON UPDATE CASCADE;

ALTER TABLE historial_comandos DROP FOREIGN KEY fk_historial_comando;
ALTER TABLE historial_comandos ADD CONSTRAINT fk_historial_comando FOREIGN KEY (id_comando)
  REFERENCES comandos (id_comando) ON DELETE RESTRICT ON UPDATE CASCADE;

ALTER TABLE estado_vehiculo DROP FOREIGN KEY fk_estado_vehiculo;
ALTER TABLE estado_vehiculo ADD CONSTRAINT fk_estado_vehiculo FOREIGN KEY (id_vehiculo)
  REFERENCES vehiculos (id_vehiculo) ON DELETE RESTRICT ON UPDATE CASCADE;

ALTER TABLE demos DROP FOREIGN KEY fk_demo_vehiculo;
ALTER TABLE demos ADD CONSTRAINT fk_demo_vehiculo FOREIGN KEY (id_vehiculo)
  REFERENCES vehiculos (id_vehiculo) ON DELETE RESTRICT ON UPDATE CASCADE;

ALTER TABLE comandos DROP FOREIGN KEY fk_comando_vehiculo;
ALTER TABLE comandos ADD CONSTRAINT fk_comando_vehiculo FOREIGN KEY (id_vehiculo)
  REFERENCES vehiculos (id_vehiculo) ON DELETE RESTRICT ON UPDATE CASCADE;

-- @comprobar SELECT COUNT(*) = 5 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE() AND CONSTRAINT_NAME IN ('fk_comando_vehiculo', 'fk_demo_vehiculo', 'fk_estado_vehiculo', 'fk_historial_vehiculo', 'fk_historial_comando') AND DELETE_RULE = 'RESTRICT'
