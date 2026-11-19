-- V007 DOWN · Solo para entornos de prueba. Conserva todas las filas del historial;
-- solo pierde la autoría (columnas nuevas).

ALTER TABLE historial_comandos DROP CHECK ck_historial_un_actor;
ALTER TABLE historial_comandos DROP FOREIGN KEY fk_historial_sesion_invitado;
ALTER TABLE historial_comandos DROP FOREIGN KEY fk_historial_usuario;
ALTER TABLE historial_comandos DROP INDEX ix_historial_sesion_invitado;
ALTER TABLE historial_comandos DROP INDEX ix_historial_usuario;
ALTER TABLE historial_comandos DROP INDEX ix_historial_vehiculo_id;
ALTER TABLE historial_comandos DROP COLUMN id_sesion_invitado, DROP COLUMN id_usuario;
