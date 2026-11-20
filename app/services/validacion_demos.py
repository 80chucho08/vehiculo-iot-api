"""Validación de seguridad física de los pasos de una demo (servidor).

Se aplica al GUARDAR una demo privada y otra vez al EJECUTAR cualquier demo (demo_executor),
además de los CHECK de la base (≤ 10 s) y del tope del firmware.
"""

from __future__ import annotations

from typing import Any

PASO_MIN_MS = 100
PAUSA_MAX_MS = 10_000
TOTAL_MAX_MS = 120_000  # una demo completa nunca dura más de 2 minutos


def errores_pasos(pasos: list[dict[str, Any]], contexto: dict[str, Any]) -> list[str]:
    """Lista de errores legibles (vacía = válida). `contexto` = repositories.demos.contexto_unidad()."""
    limites = contexto["limites"]
    comandos = {int(c["id_comando"]): c for c in contexto["comandos"]}
    errores: list[str] = []
    if not pasos:
        return ["La demo necesita al menos un paso."]
    if len(pasos) > limites["pasos_max_demo"]:
        errores.append(f"El modelo permite como máximo {limites['pasos_max_demo']} pasos por demo.")
    total = 0
    for numero, paso in enumerate(pasos, start=1):
        comando = comandos.get(int(paso["id_comando"]))
        if comando is None:
            errores.append(f"Paso {numero}: el comando no pertenece a esta unidad.")
            continue
        if not comando.get("activo"):
            errores.append(f"Paso {numero}: «{comando['nombre']}» está desactivado.")
        if not 1 <= int(comando["codigo"]) <= 12:
            errores.append(f"Paso {numero}: «{comando['nombre']}» no es un movimiento válido para demos.")
        tope = min(int(limites["duracion_max_paso_ms"]), int(comando.get("duracion_max_ms") or 10_000))
        duracion, pausa = int(paso["duracion_ms"]), int(paso["pausa_ms"])
        if not PASO_MIN_MS <= duracion <= tope:
            errores.append(f"Paso {numero}: la duración debe estar entre {PASO_MIN_MS} y {tope} ms.")
        if not 0 <= pausa <= PAUSA_MAX_MS:
            errores.append(f"Paso {numero}: la pausa debe estar entre 0 y {PAUSA_MAX_MS} ms.")
        total += duracion + pausa
    if total > TOTAL_MAX_MS:
        errores.append(f"La demo dura {total / 1000:.1f} s; el máximo es {TOTAL_MAX_MS // 1000} s.")
    return errores
