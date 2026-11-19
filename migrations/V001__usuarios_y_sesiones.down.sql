-- V001 DOWN · Solo para entornos de prueba: borra usuarios y sesiones creados después de V001.
-- Requiere haber bajado antes V003-V007, que referencian usuarios.

DROP TABLE IF EXISTS sesiones_usuario;
DROP TABLE IF EXISTS usuarios;
