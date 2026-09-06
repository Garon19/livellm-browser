import asyncio
import base64
import logging
import time
from typing import Any, Dict, List, Set
from urllib.parse import parse_qsl, urlsplit

from patchright.async_api import Page, Request, Response

logger = logging.getLogger(__name__)


class NetworkTraceRecorder:
    """Collect bounded, full request/response metadata for one page operation."""

    def __init__(self, page: Page, max_entries: int = 1000):
        self.page = page
        self.max_entries = max_entries
        self.entries: List[Dict[str, Any]] = []
        self.dropped = 0
        self._pending: Dict[int, Dict[str, Any]] = {}
        self._tasks: Set[asyncio.Task] = set()
        self._started_at = time.monotonic()
        self._is_started = False

    @staticmethod
    def _parse_query(url: str) -> tuple:
        try:
            parsed = urlsplit(url)
            parsed_query = parse_qsl(parsed.query, keep_blank_values=True)
            query_parameters = [
                {"name": name, "value": value}
                for name, value in parsed_query
            ]
            return [item["name"] for item in query_parameters], query_parameters
        except Exception:
            return [], []

    def _schedule(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def start(self) -> None:
        if self._is_started:
            return
        self.page.on("request", self._on_request_safe)
        self.page.on("response", self._on_response_safe)
        self.page.on("requestfailed", self._on_request_failed_safe)
        self._is_started = True

    def stop(self) -> None:
        if not self._is_started:
            return
        for event, handler in (
            ("request", self._on_request_safe),
            ("response", self._on_response_safe),
            ("requestfailed", self._on_request_failed_safe),
        ):
            try:
                self.page.remove_listener(event, handler)
            except Exception as exc:
                logger.debug("Could not remove %s trace listener: %s", event, exc)
        self._is_started = False
        self._pending.clear()
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

    async def snapshot(self) -> dict:
        tasks = list(self._tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return {
            "entries": [dict(entry) for entry in self.entries],
            "dropped": self.dropped,
        }

    def _on_request_safe(self, request: Request) -> None:
        try:
            self._on_request(request)
        except Exception as exc:
            logger.debug("Could not record browser request: %s", exc)

    def _on_response_safe(self, response: Response) -> None:
        try:
            self._on_response(response)
        except Exception as exc:
            logger.debug("Could not record browser response: %s", exc)

    def _on_request_failed_safe(self, request: Request) -> None:
        try:
            self._on_request_failed(request)
        except Exception as exc:
            logger.debug("Could not record failed browser request: %s", exc)

    def _on_request(self, request: Request) -> None:
        if len(self.entries) >= self.max_entries:
            self.dropped += 1
            return

        query_parameter_names, query_parameters = self._parse_query(request.url)
        post_data_buffer = request.post_data_buffer
        entry = {
            "url": request.url,
            "query_parameter_names": query_parameter_names,
            "query_parameters": query_parameters,
            "method": request.method,
            "resource_type": request.resource_type,
            "is_navigation_request": request.is_navigation_request(),
            "frame_url": request.frame.url,
            "redirected_from_url": (
                request.redirected_from.url if request.redirected_from is not None else None
            ),
            "request_headers": dict(request.headers),
            "post_data": request.post_data,
            "post_data_base64": (
                base64.b64encode(post_data_buffer).decode("ascii")
                if post_data_buffer is not None
                else None
            ),
            "request_timing": dict(request.timing),
            "request_sizes": None,
            "status": None,
            "status_text": None,
            "content_type": None,
            "response_headers": {},
            "from_service_worker": None,
            "server_addr": None,
            "security_details": None,
            "failure": None,
            "started_ms": round((time.monotonic() - self._started_at) * 1000, 1),
            "duration_ms": None,
        }
        self.entries.append(entry)
        self._pending[id(request)] = entry
        self._schedule(self._hydrate_request(entry, request))

    def _on_response(self, response: Response) -> None:
        entry = self._pending.pop(id(response.request), None)
        if entry is None:
            return
        entry["status"] = response.status
        entry["status_text"] = response.status_text
        entry["content_type"] = response.headers.get("content-type")
        entry["response_headers"] = dict(response.headers)
        entry["from_service_worker"] = response.from_service_worker
        entry["duration_ms"] = round(
            (time.monotonic() - self._started_at) * 1000 - entry["started_ms"],
            1,
        )
        self._schedule(self._hydrate_response(entry, response))

    def _on_request_failed(self, request: Request) -> None:
        entry = self._pending.pop(id(request), None)
        if entry is None:
            return
        entry["failure"] = request.failure or "request failed"
        entry["duration_ms"] = round(
            (time.monotonic() - self._started_at) * 1000 - entry["started_ms"],
            1,
        )

    async def _hydrate_request(self, entry: dict, request: Request) -> None:
        try:
            entry["request_headers"] = await request.all_headers()
        except Exception as exc:
            logger.debug("Could not read all request headers: %s", exc)

    async def _hydrate_response(self, entry: dict, response: Response) -> None:
        try:
            entry["response_headers"] = await response.all_headers()
        except Exception as exc:
            logger.debug("Could not read all response headers: %s", exc)
        try:
            entry["request_sizes"] = await response.request.sizes()
        except Exception as exc:
            logger.debug("Could not read request sizes: %s", exc)
        try:
            entry["server_addr"] = await response.server_addr()
        except Exception as exc:
            logger.debug("Could not read response server address: %s", exc)
        try:
            entry["security_details"] = await response.security_details()
        except Exception as exc:
            logger.debug("Could not read response security details: %s", exc)
