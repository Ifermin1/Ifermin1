"""Alcance de las consultas de historial: la etapa activa (StageService) recorta fechas y cuentas en el almacén; una
petición con `?scope=all` (o un servicio que necesita todo el historial) lo salta con `stage_bypass()`."""
import contextlib
from contextvars import ContextVar

STAGE_BYPASS: ContextVar[bool] = ContextVar("tpx_stage_bypass", default=False)


@contextlib.contextmanager
def stage_bypass():
    token = STAGE_BYPASS.set(True)
    try:
        yield
    finally:
        STAGE_BYPASS.reset(token)
