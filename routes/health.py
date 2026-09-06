from fastapi import APIRouter, Response

from core.browser import DEFAULT_BROWSER_ID, browser_manager
from models.responses import PingResponse

router = APIRouter(tags=["Health"])


@router.get("/ping")
async def ping() -> PingResponse:
    """Lightweight API liveness check for local callers."""
    return PingResponse()


@router.get("/health")
async def operator_health(response: Response) -> dict[str, str]:
    """Kubernetes Browser operator readiness/liveness contract on port 9000."""
    info = browser_manager.browsers.get(DEFAULT_BROWSER_ID)
    if info is None:
        response.status_code = 503
        return {"status": "unavailable", "detail": "default browser is not running"}

    try:
        connected = info.browser is not None and info.browser.is_connected()
    except Exception:
        connected = False
    if not connected:
        response.status_code = 503
        return {"status": "unavailable", "detail": "default browser is disconnected"}

    return {"status": "ok"}
