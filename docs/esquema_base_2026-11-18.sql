-- Esquema base de vehiculo_iot en RDS (solo estructura y rutinas, sin datos).
-- Extraido 2026-11-18 con scripts/inventario_esquema.py. Linea base para las migraciones de 4.3.
-- version=8.4.9

-- comandos (BASE TABLE) filas=13
CREATE TABLE `comandos` (
  `id_comando` int NOT NULL AUTO_INCREMENT,
  `id_vehiculo` int NOT NULL,
  `nombre` varchar(60) NOT NULL,
  `codigo` int NOT NULL,
  `descripcion` varchar(255) DEFAULT NULL,
  `activo` tinyint(1) NOT NULL DEFAULT '1',
  PRIMARY KEY (`id_comando`),
  UNIQUE KEY `uq_comando_codigo` (`id_vehiculo`,`codigo`),
  UNIQUE KEY `uq_comando_nombre` (`id_vehiculo`,`nombre`),
  CONSTRAINT `fk_comando_vehiculo` FOREIGN KEY (`id_vehiculo`) REFERENCES `vehiculos` (`id_vehiculo`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- demo_movimientos (BASE TABLE) filas=11
CREATE TABLE `demo_movimientos` (
  `id_demo_movimiento` int NOT NULL AUTO_INCREMENT,
  `id_demo` int NOT NULL,
  `id_comando` int NOT NULL,
  `orden` int NOT NULL,
  `duracion_ms` int unsigned NOT NULL,
  `pausa_ms` int unsigned NOT NULL DEFAULT '0',
  PRIMARY KEY (`id_demo_movimiento`),
  UNIQUE KEY `uq_demo_orden` (`id_demo`,`orden`),
  KEY `fk_mov_comando` (`id_comando`),
  CONSTRAINT `fk_mov_comando` FOREIGN KEY (`id_comando`) REFERENCES `comandos` (`id_comando`) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT `fk_mov_demo` FOREIGN KEY (`id_demo`) REFERENCES `demos` (`id_demo`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- demos (BASE TABLE) filas=1
CREATE TABLE `demos` (
  `id_demo` int NOT NULL AUTO_INCREMENT,
  `id_vehiculo` int NOT NULL,
  `nombre` varchar(80) NOT NULL,
  `descripcion` varchar(255) DEFAULT NULL,
  `activo` tinyint(1) NOT NULL DEFAULT '1',
  PRIMARY KEY (`id_demo`),
  KEY `fk_demo_vehiculo` (`id_vehiculo`),
  CONSTRAINT `fk_demo_vehiculo` FOREIGN KEY (`id_vehiculo`) REFERENCES `vehiculos` (`id_vehiculo`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- estado_vehiculo (BASE TABLE) filas=1
CREATE TABLE `estado_vehiculo` (
  `id_estado` int NOT NULL AUTO_INCREMENT,
  `id_vehiculo` int NOT NULL,
  `id_comando` int DEFAULT NULL,
  `fecha_actualizacion` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `conectado` tinyint(1) NOT NULL DEFAULT '0',
  PRIMARY KEY (`id_estado`),
  UNIQUE KEY `id_vehiculo` (`id_vehiculo`),
  KEY `fk_estado_comando` (`id_comando`),
  CONSTRAINT `fk_estado_comando` FOREIGN KEY (`id_comando`) REFERENCES `comandos` (`id_comando`) ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT `fk_estado_vehiculo` FOREIGN KEY (`id_vehiculo`) REFERENCES `vehiculos` (`id_vehiculo`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- historial_comandos (BASE TABLE) filas=854
CREATE TABLE `historial_comandos` (
  `id_historial` bigint NOT NULL AUTO_INCREMENT,
  `id_vehiculo` int NOT NULL,
  `id_comando` int DEFAULT NULL,
  `fecha_hora` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `origen` varchar(30) NOT NULL DEFAULT 'MANUAL',
  PRIMARY KEY (`id_historial`),
  KEY `fk_historial_vehiculo` (`id_vehiculo`),
  KEY `fk_historial_comando` (`id_comando`),
  CONSTRAINT `fk_historial_comando` FOREIGN KEY (`id_comando`) REFERENCES `comandos` (`id_comando`) ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT `fk_historial_vehiculo` FOREIGN KEY (`id_vehiculo`) REFERENCES `vehiculos` (`id_vehiculo`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- vehiculos (BASE TABLE) filas=1
CREATE TABLE `vehiculos` (
  `id_vehiculo` int NOT NULL AUTO_INCREMENT,
  `nombre` varchar(80) NOT NULL,
  `modelo` varchar(80) DEFAULT NULL,
  `descripcion` varchar(255) DEFAULT NULL,
  `microcontrolador` varchar(50) DEFAULT NULL,
  `fecha_registro` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `activo` tinyint(1) NOT NULL DEFAULT '1',
  PRIMARY KEY (`id_vehiculo`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- PROCEDURE sp_actualizar_comando
CREATE PROCEDURE `sp_actualizar_comando`(

    IN p_id_comando INT,

    IN p_nombre VARCHAR(60),

    IN p_codigo INT,

    IN p_descripcion VARCHAR(255),

    IN p_activo BOOLEAN

)
BEGIN

    UPDATE comandos

       SET nombre=p_nombre, codigo=p_codigo,

           descripcion=p_descripcion, activo=p_activo

     WHERE id_comando=p_id_comando;

END;

-- PROCEDURE sp_actualizar_demo
CREATE PROCEDURE `sp_actualizar_demo`(

    IN p_id_demo INT,

    IN p_nombre VARCHAR(80),

    IN p_descripcion VARCHAR(255),

    IN p_activo BOOLEAN

)
BEGIN

    UPDATE demos

       SET nombre=p_nombre, descripcion=p_descripcion, activo=p_activo

     WHERE id_demo=p_id_demo;

END;

-- PROCEDURE sp_actualizar_estado
CREATE PROCEDURE `sp_actualizar_estado`(

    IN p_id_vehiculo INT,

    IN p_id_comando INT,

    IN p_conectado BOOLEAN

)
BEGIN

    INSERT INTO estado_vehiculo(id_vehiculo,id_comando,conectado)

    VALUES(p_id_vehiculo,p_id_comando,p_conectado)

    ON DUPLICATE KEY UPDATE

      id_comando=VALUES(id_comando),

      conectado=VALUES(conectado),

      fecha_actualizacion=CURRENT_TIMESTAMP;

END;

-- PROCEDURE sp_actualizar_movimiento_demo
CREATE PROCEDURE `sp_actualizar_movimiento_demo`(

    IN p_id_demo_movimiento INT,

    IN p_id_comando INT,

    IN p_orden INT,

    IN p_duracion_ms INT,

    IN p_pausa_ms INT

)
BEGIN

    UPDATE demo_movimientos

       SET id_comando=p_id_comando, orden=p_orden,

           duracion_ms=p_duracion_ms, pausa_ms=p_pausa_ms

     WHERE id_demo_movimiento=p_id_demo_movimiento;

END;

-- PROCEDURE sp_actualizar_vehiculo
CREATE PROCEDURE `sp_actualizar_vehiculo`(

    IN p_id_vehiculo INT,

    IN p_nombre VARCHAR(80),

    IN p_modelo VARCHAR(80),

    IN p_descripcion VARCHAR(255),

    IN p_microcontrolador VARCHAR(50),

    IN p_activo BOOLEAN

)
BEGIN

    UPDATE vehiculos

       SET nombre=p_nombre, modelo=p_modelo, descripcion=p_descripcion,

           microcontrolador=p_microcontrolador, activo=p_activo

     WHERE id_vehiculo=p_id_vehiculo;

END;

-- PROCEDURE sp_agregar_movimiento_demo
CREATE PROCEDURE `sp_agregar_movimiento_demo`(

    IN p_id_demo INT,

    IN p_id_comando INT,

    IN p_orden INT,

    IN p_duracion_ms INT,

    IN p_pausa_ms INT

)
BEGIN

    INSERT INTO demo_movimientos

      (id_demo,id_comando,orden,duracion_ms,pausa_ms)

    VALUES

      (p_id_demo,p_id_comando,p_orden,p_duracion_ms,p_pausa_ms);

END;

-- PROCEDURE sp_crear_comando
CREATE PROCEDURE `sp_crear_comando`(

    IN p_id_vehiculo INT,

    IN p_nombre VARCHAR(60),

    IN p_codigo INT,

    IN p_descripcion VARCHAR(255)

)
BEGIN

    INSERT INTO comandos(id_vehiculo,nombre,codigo,descripcion)

    VALUES(p_id_vehiculo,p_nombre,p_codigo,p_descripcion);

    SELECT LAST_INSERT_ID() AS id_comando;

END;

-- PROCEDURE sp_crear_demo
CREATE PROCEDURE `sp_crear_demo`(

    IN p_id_vehiculo INT,

    IN p_nombre VARCHAR(80),

    IN p_descripcion VARCHAR(255)

)
BEGIN

    INSERT INTO demos(id_vehiculo,nombre,descripcion)

    VALUES(p_id_vehiculo,p_nombre,p_descripcion);

    SELECT LAST_INSERT_ID() AS id_demo;

END;

-- PROCEDURE sp_crear_vehiculo
CREATE PROCEDURE `sp_crear_vehiculo`(

    IN p_nombre VARCHAR(80),

    IN p_modelo VARCHAR(80),

    IN p_descripcion VARCHAR(255),

    IN p_microcontrolador VARCHAR(50)

)
BEGIN

    INSERT INTO vehiculos(nombre, modelo, descripcion, microcontrolador)

    VALUES(p_nombre, p_modelo, p_descripcion, p_microcontrolador);

    SELECT LAST_INSERT_ID() AS id_vehiculo;

END;

-- PROCEDURE sp_eliminar_comando
CREATE PROCEDURE `sp_eliminar_comando`(IN p_id_comando INT)
BEGIN

    DELETE FROM comandos WHERE id_comando=p_id_comando;

END;

-- PROCEDURE sp_eliminar_demo
CREATE PROCEDURE `sp_eliminar_demo`(IN p_id_demo INT)
BEGIN

    DELETE FROM demos WHERE id_demo=p_id_demo;

END;

-- PROCEDURE sp_eliminar_movimiento_demo
CREATE PROCEDURE `sp_eliminar_movimiento_demo`(IN p_id_demo_movimiento INT)
BEGIN

    DELETE FROM demo_movimientos

     WHERE id_demo_movimiento=p_id_demo_movimiento;

END;

-- PROCEDURE sp_eliminar_vehiculo
CREATE PROCEDURE `sp_eliminar_vehiculo`(IN p_id_vehiculo INT)
BEGIN

    DELETE FROM vehiculos WHERE id_vehiculo=p_id_vehiculo;

END;

-- PROCEDURE sp_obtener_comandos_vehiculo
CREATE PROCEDURE `sp_obtener_comandos_vehiculo`(IN p_id_vehiculo INT)
BEGIN

    SELECT * FROM comandos

     WHERE id_vehiculo=p_id_vehiculo

     ORDER BY codigo;

END;

-- PROCEDURE sp_obtener_demos
CREATE PROCEDURE `sp_obtener_demos`(IN p_id_vehiculo INT)
BEGIN

    SELECT * FROM demos

     WHERE id_vehiculo=p_id_vehiculo

     ORDER BY id_demo;

END;

-- PROCEDURE sp_obtener_estado
CREATE PROCEDURE `sp_obtener_estado`(IN p_id_vehiculo INT)
BEGIN

    SELECT e.id_vehiculo, v.nombre AS vehiculo,

           e.id_comando, c.nombre AS comando, c.codigo,

           e.conectado, e.fecha_actualizacion

      FROM estado_vehiculo e

      JOIN vehiculos v ON v.id_vehiculo=e.id_vehiculo

 LEFT JOIN comandos c ON c.id_comando=e.id_comando

     WHERE e.id_vehiculo=p_id_vehiculo;

END;

-- PROCEDURE sp_obtener_historial
CREATE PROCEDURE `sp_obtener_historial`(IN p_id_vehiculo INT)
BEGIN

    SELECT h.id_historial, h.id_vehiculo,

           c.nombre AS comando, c.codigo,

           h.fecha_hora, h.origen

      FROM historial_comandos h

 LEFT JOIN comandos c ON c.id_comando=h.id_comando

     WHERE h.id_vehiculo=p_id_vehiculo

     ORDER BY h.fecha_hora DESC, h.id_historial DESC;

END;

-- PROCEDURE sp_obtener_movimientos_demo
CREATE PROCEDURE `sp_obtener_movimientos_demo`(IN p_id_demo INT)
BEGIN

    SELECT dm.id_demo_movimiento, dm.orden,

           dm.id_comando, c.nombre AS comando, c.codigo,

           dm.duracion_ms, dm.pausa_ms

      FROM demo_movimientos dm

      JOIN comandos c ON c.id_comando=dm.id_comando

     WHERE dm.id_demo=p_id_demo

     ORDER BY dm.orden;

END;

-- PROCEDURE sp_obtener_vehiculo
CREATE PROCEDURE `sp_obtener_vehiculo`(IN p_id_vehiculo INT)
BEGIN

    SELECT * FROM vehiculos WHERE id_vehiculo = p_id_vehiculo;

END;

-- PROCEDURE sp_obtener_vehiculos
CREATE PROCEDURE `sp_obtener_vehiculos`()
BEGIN

    SELECT * FROM vehiculos ORDER BY id_vehiculo;

END;

-- PROCEDURE sp_registrar_historial
CREATE PROCEDURE `sp_registrar_historial`(

    IN p_id_vehiculo INT,

    IN p_id_comando INT,

    IN p_origen VARCHAR(30)

)
BEGIN

    INSERT INTO historial_comandos(id_vehiculo,id_comando,origen)

    VALUES(p_id_vehiculo,p_id_comando,p_origen);

END;

-- TRIGGERS: []
-- EVENTS: []
