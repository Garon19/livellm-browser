import asyncio
import base64
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response

from core.dependencies import PageDep
from core.network_trace import NetworkTraceRecorder
from helpers.playwright import scroll_to_bottom
from models.requests import ContentRequest, OutputAction
from models.responses import ContentWithNetworkTraceResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Content"])


@router.post("/content")
async def get_content(request: ContentRequest, page: PageDep) -> Response:
    """
    Get page content with automatic scrolling.

    Shortcut for: navigate → idle → scroll_to_bottom → output.
    The scroll timeout is ``steps × step_delay`` seconds.

    ``output_action`` controls the response format:
    - ``text`` — inner text (default)
    - ``html`` — full page HTML
    - ``screenshot`` — viewport PNG screenshot
    - ``screenshot_full`` — full-page PNG screenshot

    With ``include_network_trace=true``, returns a JSON envelope containing the
    requested output and bounded full request/response trace metadata, including
    complete URLs, query values, headers, request bodies, timings, and sizes.
    """
    recorder = None
    try:
        if request.include_network_trace:
            recorder = NetworkTraceRecorder(page, max_entries=request.network_trace_limit)
            recorder.start()

        navigation_response = None
        if request.url:
            navigation_response = await page.goto(request.url, wait_until=request.wait_until, timeout=request.timeout)

        response_headers = {}
        if request.url:
            response_headers["X-Final-Url"] = page.url
            navigation_status = getattr(navigation_response, "status", None)
            if isinstance(navigation_status, int):
                response_headers["X-Navigation-Status"] = str(navigation_status)

        if request.idle > 0:
            await asyncio.sleep(request.idle)

        if request.steps > 0:
            scroll_timeout = request.steps * request.step_delay
            await scroll_to_bottom(page, request.step_pixels, request.step_delay, scroll_timeout)
            logger.info(
                f"Scrolled {request.steps} steps ({scroll_timeout:.1f}s), "
                f"pixels={request.step_pixels}, delay={request.step_delay}s"
            )

        if request.output_action in (OutputAction.screenshot, OutputAction.screenshot_full):
            full = request.output_action == OutputAction.screenshot_full
            raw_content = await page.screenshot(full_page=full, type="png")
            media_type = "image/png"
            content_encoding = "base64"
            envelope_content = base64.b64encode(raw_content).decode("ascii")
        elif request.output_action == OutputAction.html:
            raw_content = await page.content()
            media_type = "text/html"
            content_encoding = "utf-8"
            envelope_content = raw_content
        else:
            raw_content = await page.inner_text("body")
            media_type = "text/plain"
            content_encoding = "utf-8"
            envelope_content = raw_content

        if recorder is not None:
            navigation_status = getattr(navigation_response, "status", None)
            if not isinstance(navigation_status, int):
                navigation_status = None
            envelope = ContentWithNetworkTraceResponse(
                content=envelope_content,
                content_type=media_type,
                content_encoding=content_encoding,
                final_url=page.url,
                navigation_status=navigation_status,
                network_trace=await recorder.snapshot(),
            )
            return JSONResponse(content=envelope.model_dump(), headers=response_headers)

        return Response(content=raw_content, media_type=media_type, headers=response_headers)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get content: {str(e)}")
    finally:
        if recorder is not None:
            recorder.stop()
