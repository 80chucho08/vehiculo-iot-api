
from fastapi import APIRouter, HTTPException, status

from app.services.demo_executor import demo_execution_manager


router = APIRouter(
    prefix="/api/vehiculos",
    tags=["Ejecución de demos"],
)


@router.post(
    "/{id_vehiculo}/demos/{id_demo}/ejecutar",
    status_code=status.HTTP_202_ACCEPTED,
)
async def ejecutar_demo(id_vehiculo: int, id_demo: int):
    try:
        await demo_execution_manager.iniciar(
            id_vehiculo,
            id_demo,
        )

        return {
            "mensaje": "La ejecución de la demo fue iniciada.",
            "id_vehiculo": id_vehiculo,
            "id_demo": id_demo,
            "estado": "en_ejecucion",
        }

    except LookupError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        )

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        )

    except RuntimeError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        )


@router.post("/{id_vehiculo}/demos/cancelar")
async def cancelar_demo(id_vehiculo: int):
    try:
        await demo_execution_manager.cancelar(id_vehiculo)

        return {
            "mensaje": "Se solicitó la cancelación de la demo.",
            "id_vehiculo": id_vehiculo,
            "estado": "cancelada",
        }

    except RuntimeError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        )
