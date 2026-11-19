"""Inventario SOLO LECTURA del esquema RDS: DDL, rutinas, conteos y permisos.
No lee filas de datos ni contrasenas. Uso en la EC2 (raiz del repo): .venv/Scripts/python scripts/inventario_esquema.py esquema_actual.sql
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db.connection import get_connection
c = get_connection()
out = []
with c.cursor() as cur:
    cur.execute("SELECT DATABASE() db, VERSION() v, CURRENT_USER() u"); r = cur.fetchone()
    out.append(f"-- version={r['v']}")
    cur.execute("SHOW GRANTS"); out.append("-- GRANTS: " + " | ".join(list(x.values())[0].split(" IDENTIFIED")[0] for x in cur.fetchall()))
    cur.execute("SHOW FULL TABLES"); tablas = [(list(x.values())[0], list(x.values())[1]) for x in cur.fetchall()]
    for t, tipo in tablas:
        cur.execute(f"SHOW CREATE TABLE `{t}`"); row = cur.fetchone()
        ddl = row.get('Create Table') or row.get('Create View')
        cnt = ''
        if tipo == 'BASE TABLE':
            cur.execute(f"SELECT COUNT(*) n FROM `{t}`"); cnt = cur.fetchone()['n']
        out.append(f"\n-- {t} ({tipo}) filas={cnt}\n{ddl};")
    cur.execute("SELECT ROUTINE_NAME, ROUTINE_TYPE FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA=DATABASE() ORDER BY 1")
    for x in cur.fetchall():
        cur.execute(f"SHOW CREATE {x['ROUTINE_TYPE']} `{x['ROUTINE_NAME']}`"); row = cur.fetchone()
        body = row.get('Create Procedure') or row.get('Create Function')
        out.append(f"\n-- {x['ROUTINE_TYPE']} {x['ROUTINE_NAME']}\n{body};")
    cur.execute("SELECT TRIGGER_NAME FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()"); out.append("\n-- TRIGGERS: " + str([x['TRIGGER_NAME'] for x in cur.fetchall()]))
    cur.execute("SELECT EVENT_NAME FROM information_schema.EVENTS WHERE EVENT_SCHEMA=DATABASE()"); out.append("-- EVENTS: " + str([x['EVENT_NAME'] for x in cur.fetchall()]))
c.close()
destino = sys.argv[1] if len(sys.argv) > 1 else 'esquema_actual.sql'
open(destino, 'w', encoding='utf-8').write("\n".join(out))
print("ok", len(tablas), "tablas")
