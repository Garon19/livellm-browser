from core.network_trace import NetworkTraceRecorder


class FakePage:
    def __init__(self):
        self.handlers = {}

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event, handler):
        self.handlers[event].remove(handler)

    def emit(self, event, payload):
        for handler in list(self.handlers.get(event, [])):
            handler(payload)


class FakeRequest:
    def __init__(self, url, method="GET", resource_type="xhr", post_data=None):
        self.url = url
        self.method = method
        self.resource_type = resource_type
        self.failure = None
        self.headers = {"content-type": "application/json", "x-debug": "visible"}
        self.post_data = post_data
        self.post_data_buffer = post_data.encode("utf-8") if post_data else None
        self.timing = {"startTime": 123.0}
        self.redirected_from = None
        self.frame = type("FakeFrame", (), {"url": "https://example.test/page"})()

    async def all_headers(self):
        return {**self.headers, "cookie": "debug_cookie=full-value"}

    async def sizes(self):
        return {"requestBodySize": len(self.post_data_buffer or b"")}

    def is_navigation_request(self):
        return self.resource_type == "document"


class FakeResponse:
    def __init__(self, request, status=200, content_type="application/json"):
        self.request = request
        self.status = status
        self.headers = {"content-type": content_type}
        self.status_text = "Created" if status == 201 else "OK"
        self.from_service_worker = False

    async def all_headers(self):
        return {**self.headers, "x-response-debug": "full-value"}

    async def server_addr(self):
        return {"ipAddress": "127.0.0.1", "port": 443}

    async def security_details(self):
        return {"protocol": "TLS 1.3"}


async def test_trace_records_full_request_and_response_metadata():
    page = FakePage()
    recorder = NetworkTraceRecorder(page, max_entries=10)
    recorder.start()
    request = FakeRequest(
        "https://api.example.test/items?token=top-secret&page=2",
        method="POST",
        post_data='{"sku":"123","debugSecret":"visible"}',
    )

    page.emit("request", request)
    page.emit("response", FakeResponse(request, status=201))

    snapshot = await recorder.snapshot()
    assert snapshot["dropped"] == 0
    assert snapshot["entries"][0]["url"] == (
        "https://api.example.test/items?token=top-secret&page=2"
    )
    assert snapshot["entries"][0]["query_parameter_names"] == ["token", "page"]
    assert snapshot["entries"][0]["query_parameters"] == [
        {"name": "token", "value": "top-secret"},
        {"name": "page", "value": "2"},
    ]
    assert snapshot["entries"][0]["method"] == "POST"
    assert snapshot["entries"][0]["resource_type"] == "xhr"
    assert snapshot["entries"][0]["request_headers"]["cookie"] == "debug_cookie=full-value"
    assert snapshot["entries"][0]["post_data"] == '{"sku":"123","debugSecret":"visible"}'
    assert snapshot["entries"][0]["post_data_base64"] == (
        "eyJza3UiOiIxMjMiLCJkZWJ1Z1NlY3JldCI6InZpc2libGUifQ=="
    )
    assert snapshot["entries"][0]["request_timing"] == {"startTime": 123.0}
    assert snapshot["entries"][0]["request_sizes"] == {"requestBodySize": 37}
    assert snapshot["entries"][0]["status"] == 201
    assert snapshot["entries"][0]["status_text"] == "Created"
    assert snapshot["entries"][0]["content_type"] == "application/json"
    assert snapshot["entries"][0]["response_headers"]["x-response-debug"] == "full-value"
    assert snapshot["entries"][0]["server_addr"] == {"ipAddress": "127.0.0.1", "port": 443}
    assert snapshot["entries"][0]["security_details"] == {"protocol": "TLS 1.3"}


async def test_trace_keeps_full_url_and_query_values():
    page = FakePage()
    recorder = NetworkTraceRecorder(page, max_entries=10)
    recorder.start()

    page.emit(
        "request",
        FakeRequest("https://user:password@example.test/items?q=visible#private-fragment"),
    )

    entry = (await recorder.snapshot())["entries"][0]
    assert entry["url"] == "https://user:password@example.test/items?q=visible#private-fragment"
    assert entry["query_parameters"] == [{"name": "q", "value": "visible"}]


async def test_trace_is_bounded_and_reports_dropped_requests():
    page = FakePage()
    recorder = NetworkTraceRecorder(page, max_entries=1)
    recorder.start()

    page.emit("request", FakeRequest("https://example.test/one"))
    page.emit("request", FakeRequest("https://example.test/two"))

    snapshot = await recorder.snapshot()
    assert len(snapshot["entries"]) == 1
    assert snapshot["dropped"] == 1


def test_stop_detaches_all_page_event_listeners():
    page = FakePage()
    recorder = NetworkTraceRecorder(page, max_entries=10)
    recorder.start()

    recorder.stop()

    assert page.handlers == {"request": [], "response": [], "requestfailed": []}
