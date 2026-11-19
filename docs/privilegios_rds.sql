-- Usuarios de MySQL con privilegios mínimos para vehiculo_iot (propuesta, NO aplicada).
-- Ejecutar como `admin` DESPUÉS de aplicar las migraciones V001-V008.
-- Sustituye <CONTRASEÑA_...> por contraseñas largas y aleatorias, p. ej.:
--   python -c "import secrets; print(secrets.token_urlsafe(32))"
-- No guardes las contraseñas en git: van solo en el .env de la EC2.
--
-- Hoy la API usa `admin` (master de RDS, ALL PRIVILEGES con GRANT OPTION y CREATE USER).
-- Si la API sufriera una inyección SQL, el atacante tendría poder total sobre la instancia.

-- 1) Usuario de la APLICACIÓN: lee todo, escribe lo necesario, sin DDL ni DELETE.
CREATE USER IF NOT EXISTS 'vehiculo_app'@'%' IDENTIFIED BY '<CONTRASEÑA_APP>';
-- Opcional (recomendado): REQUIRE SSL. Antes hay que pasar ssl={"ca": ...} a pymysql con el
-- certificado de RDS (global-bundle.pem); si no, la API no podrá conectarse.
-- ALTER USER 'vehiculo_app'@'%' REQUIRE SSL;

-- Los 21 procedimientos son SQL SECURITY DEFINER: basta EXECUTE para llamarlos.
GRANT EXECUTE ON `vehiculo_iot`.* TO 'vehiculo_app'@'%';
GRANT SELECT ON `vehiculo_iot`.* TO 'vehiculo_app'@'%';

-- Tablas que la API crea y actualiza (bajas lógicas con UPDATE activo = 0).
GRANT INSERT, UPDATE ON `vehiculo_iot`.`vehiculos` TO 'vehiculo_app'@'%';
GRANT INSERT, UPDATE ON `vehiculo_iot`.`comandos` TO 'vehiculo_app'@'%';
GRANT INSERT, UPDATE ON `vehiculo_iot`.`estado_vehiculo` TO 'vehiculo_app'@'%';
GRANT INSERT, UPDATE ON `vehiculo_iot`.`demos` TO 'vehiculo_app'@'%';
GRANT INSERT, UPDATE ON `vehiculo_iot`.`demo_movimientos` TO 'vehiculo_app'@'%';
GRANT INSERT, UPDATE ON `vehiculo_iot`.`modelos_vehiculo` TO 'vehiculo_app'@'%';
GRANT INSERT, UPDATE ON `vehiculo_iot`.`credenciales_dispositivo` TO 'vehiculo_app'@'%';
GRANT INSERT, UPDATE ON `vehiculo_iot`.`usuarios` TO 'vehiculo_app'@'%';
GRANT INSERT, UPDATE ON `vehiculo_iot`.`sesiones_usuario` TO 'vehiculo_app'@'%';
GRANT INSERT, UPDATE ON `vehiculo_iot`.`asignaciones` TO 'vehiculo_app'@'%';
GRANT INSERT, UPDATE ON `vehiculo_iot`.`autorizaciones_soporte` TO 'vehiculo_app'@'%';
GRANT INSERT, UPDATE ON `vehiculo_iot`.`sesiones_invitado` TO 'vehiculo_app'@'%';

-- Solo se agrega: el historial y la auditoría no se editan ni se borran desde la API.
GRANT INSERT ON `vehiculo_iot`.`historial_comandos` TO 'vehiculo_app'@'%';
GRANT INSERT ON `vehiculo_iot`.`auditoria` TO 'vehiculo_app'@'%';

-- 2) Usuario de MIGRACIONES y RESPALDOS: DDL solo sobre estas bases, sin poder global.
CREATE USER IF NOT EXISTS 'vehiculo_migrador'@'%' IDENTIFIED BY '<CONTRASEÑA_MIGRADOR>';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES,
      CREATE ROUTINE, ALTER ROUTINE, EXECUTE, SHOW VIEW, TRIGGER
   ON `vehiculo_iot`.* TO 'vehiculo_migrador'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES,
      CREATE ROUTINE, ALTER ROUTINE, EXECUTE, SHOW VIEW, TRIGGER
   ON `vehiculo_iot_prueba`.* TO 'vehiculo_migrador'@'%';
-- Para leer el cuerpo de procedimientos de otro DEFINER en el respaldo:
GRANT SHOW_ROUTINE ON *.* TO 'vehiculo_migrador'@'%';
-- restaurar_bd.py --recrear necesita crear/borrar la base de prueba:
GRANT CREATE, DROP ON `vehiculo_iot_prueba`.* TO 'vehiculo_migrador'@'%';

-- 3) Comprobación
SHOW GRANTS FOR 'vehiculo_app'@'%';
SHOW GRANTS FOR 'vehiculo_migrador'@'%';

-- 4) Después: en el .env de la EC2, DB_USER=vehiculo_app y su contraseña; reiniciar la tarea
--    `Vehiculo IoT API`. Para migrar o respaldar, usar un .env aparte con vehiculo_migrador.
--    `admin` queda solo para emergencias desde la consola de AWS.
