# LiveLLM Browser

A headless browser automation API running in Docker with VNC access. Control Chrome programmatically via REST API while watching the browser in real-time through VNC.

Built with FastAPI, Patchright (undetectable Playwright fork), and runs in a Docker container with XFCE desktop environment.

## Features

- **REST API Control** — Navigate, click, scroll, fill forms, extract content
- **Multi-Browser Support** — Create multiple isolated browser instances with persistent profiles
- **Proxy Support** — Configure HTTP/HTTPS/SOCKS proxy per browser instance
- **HTTP Authentication** — Set HTTP Basic Auth credentials for protected pages
- **Undetectable Automation** — Uses Patchright with native Playwright locators
- **VNC Access** — Watch browser actions in real-time via VNC or noVNC web interface
- **Session Management** — Multiple isolated browser tabs via `X-Session-Id` header
- **Page Interactions** — Scroll, mouse move/click, idle, login, CSS/XPath selectors — all via unified `/interact` endpoint with configurable `output_action`
- **Attribute Extraction** — Extract elements/attributes from page HTML using CSS or XPath selectors via `/attribute` endpoint (powered by BeautifulSoup + lxml)
- **Network Trace** — Capture full request/response metadata (URLs, query params, headers, bodies, timings, sizes) alongside page content via `include_network_trace` on `/content`
- **CDP Mode** — Optional browser launch mode (`BROWSER_CDP_MODE=1`): Chrome starts as a regular X11 application with minimal flags and Patchright attaches over the DevTools port, for sites whose WAF flags browsers started with the full Playwright argument set
- **Storefront Endpoints** — Dedicated `/lenta/*` and `/utkonos/*` routes that bootstrap a real storefront session and fetch product/catalog data through the site's own API

## Quick Start

### Docker Compose (Recommended)

```bash
docker compose up -d
```

Access points:
- **API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs
- **noVNC Web**: http://localhost:6901 (password: `headless`)
- **VNC**: localhost:5901 (password: `headless`)

### Local Development

```bash
# Install dependencies
uv sync

# Install Chrome
uv run patchright install chrome

# Run server
uv run main.py
```

## API Reference

All endpoints accept these headers:
- `X-Browser-Id` — Target a specific browser (defaults to the default browser)
- `X-Session-Id` — Target a specific session/tab (auto-creates if not provided)

---

### Health

#### `GET /ping`

Health check. Returns `{"status": "ok", "message": "Controller API is running"}`.

---

### Browser Management

Browsers are isolated Chrome instances with their own profile data. A default browser is always available.

#### `GET /browsers`

List all active browsers with their IDs, profile paths, and session counts.

#### `POST /browsers`

Create a new browser instance.

```json
{
  "profile_uid": "my-profile",
  "proxy": {
    "server": "http://myproxy.com:3128",
    "username": "proxyuser",
    "password": "proxypass",
    "bypass": "localhost,*.example.com"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `profile_uid` | No | If provided, used as `browser_id` with persistent profile in `./profiles/{uid}`. If omitted, generates random UUID (ephemeral). |
| `proxy` | No | Proxy settings (only configurable at browser creation). |
| `proxy.server` | Yes (if proxy) | Proxy server URL (e.g., `http://proxy:3128`, `socks5://127.0.0.1:1080`). |
| `proxy.username` | No | Proxy auth username. |
| `proxy.password` | No | Proxy auth password. |
| `proxy.bypass` | No | Comma-separated hosts to bypass proxy. |

**Examples:**

```bash
# Named persistent profile
curl -X POST http://localhost:8000/browsers \
  -H "Content-Type: application/json" \
  -d '{"profile_uid": "work-profile"}'

# Ephemeral browser (incognito)
curl -X POST http://localhost:8000/browsers

# Browser with proxy
curl -X POST http://localhost:8000/browsers \
  -H "Content-Type: application/json" \
  -d '{"proxy": {"server": "socks5://127.0.0.1:1080"}}'
```

#### `DELETE /browsers/{browser_id}`

Close a browser and all its sessions. Cannot delete the default browser.

---

### Session Management

Sessions are browser tabs. Use `X-Session-Id` header to target specific sessions.

#### `POST /start_session`

Create a new tab and return a `session_id`.

```json
{
  "browser_id": "my-profile"
}
```

`browser_id` is optional — defaults to the default browser. Can also use `X-Browser-Id` header.

#### `DELETE /end_session`

Close a session tab.

```
Header: X-Session-Id: <session_id>
Header: X-Browser-Id: <browser_id>  (optional)
```

---

### Content

#### `POST /content`

Get page content with automatic scrolling. This is a shortcut that:
1. Navigates to the URL
2. Waits `idle` seconds
3. Scrolls to bottom (`steps` × `step_delay` seconds)
4. Returns content based on `output_action`

```json
{
  "url": "https://example.com",
  "output_action": "text",
  "idle": 2,
  "steps": 8,
  "step_delay": 1.5,
  "step_pixels": 1500
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `url` | *(current page)* | URL to navigate to. |
| `output_action` | `"text"` | `"text"`, `"html"`, `"screenshot"`, or `"screenshot_full"`. |
| `wait_until` | `"commit"` | `"commit"`, `"domcontentloaded"`, `"load"`, or `"networkidle"`. |
| `timeout` | `30000` | Navigation timeout in milliseconds. |
| `idle` | `2` | Seconds to wait after page loads. |
| `steps` | `8` | Number of scroll steps (0 = no scroll, 4-12 recommended). |
| `step_delay` | `1.5` | Seconds between scroll steps. |
| `step_pixels` | `1500` | Pixels per scroll step. |
| `include_network_trace` | `false` | Return content plus a bounded network trace in a JSON envelope (see below). |
| `network_trace_limit` | `1000` | Maximum network entries returned when tracing is enabled (1–5000). |

##### Network Trace

Set `include_network_trace: true` to capture what the browser actually sent and received while loading the page. Instead of raw content, `/content` returns a JSON envelope:

```json
{
  "content": "...",
  "content_type": "text/plain",
  "content_encoding": "utf-8",
  "final_url": "https://example.com/",
  "navigation_status": 200,
  "network_trace": [ ... ]
}
```

Each `network_trace` entry contains full request/response metadata: complete URL, parsed query parameters, method, resource type, redirect chain, request/response headers, request bodies (`post_data` / `post_data_base64`), timings, sizes, and TLS `security_details`. Screenshots are returned base64-encoded in the envelope. The trace is capped by `network_trace_limit` (default 1000, max 5000) — entries beyond the limit are dropped, not truncated.

This is useful for reverse-engineering a site's internal APIs: load a page through the real browser (with its WAF-passing fingerprint), then read the XHR/fetch calls it made directly from the trace.

**Examples:**

```bash
# Get text content with scrolling
curl -X POST http://localhost:8000/content \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'

# Get full HTML
curl -X POST http://localhost:8000/content \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "output_action": "html"}'

# Take screenshot after scrolling
curl -X POST http://localhost:8000/content \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "output_action": "screenshot"}' \
  --output page.png

# Full-page screenshot after scrolling
curl -X POST http://localhost:8000/content \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "output_action": "screenshot_full"}' \
  --output page_full.png

# Quick fetch (no scrolling)
curl -X POST http://localhost:8000/content \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "steps": 0, "idle": 0}'

# Custom scroll (e.g. for Ozon product page)
curl -X POST http://localhost:8000/content \
  -H "X-Session-Id: my-session" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.ozon.ru/product/...",
    "idle": 2,
    "steps": 8,
    "step_delay": 1.5,
    "step_pixels": 1500,
    "output_action": "html"
  }'
```

---

### Interact

#### `POST /interact`

Unified endpoint for page interactions. Executes all `actions` in order, then returns a result based on `output_action`.

Selectors are DOM manipulation actions — they click, fill, or remove elements. They don't produce their own output. Use `output_action` to get the final page state.

```json
{
  "url": "https://example.com",
  "actions": [
    {"action": "selector", "type": "css", "value": ".ad", "do": "remove", "args": {"nth": null}},
    {"action": "scroll_to_bottom", "step_pixels": 1500, "step_delay": 1.5, "timeout": 10}
  ],
  "output_action": "html"
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `url` | *(current page)* | URL to navigate to. |
| `wait_until` | `"commit"` | Navigation wait condition. |
| `timeout` | `30000` | Navigation timeout in ms. |
| `idle` | `0` | Seconds to wait after page loads. |
| `actions` | `[]` | List of actions to perform in order. |
| `output_action` | `"text"` | `"text"`, `"html"`, `"screenshot"`, or `"screenshot_full"`. |

**Available actions:**

| Action | Description | Parameters |
|--------|-------------|------------|
| `scroll` | Scroll page by delta | `x`, `y` |
| `scroll_to_bottom` | Scroll to bottom in steps | `step_pixels`, `step_delay`, `timeout` |
| `move` | Move mouse cursor | `x`, `y`, `steps` |
| `mouse_click` | Click at coordinates | `x`, `y`, `button`, `click_count`, `delay` |
| `idle` | Wait/sleep | `duration` (seconds) |
| `login` | Set HTTP Basic Auth | `username`, `password` |
| `selector` | Act on elements matching CSS/XPath | `type`, `value`, `do`, `args` |

**Selector action fields:**

| Field | Default | Description |
|-------|---------|-------------|
| `type` | `"css"` | `"css"` or `"xml"` (xpath). |
| `value` | *(required)* | The selector string. |
| `do` | *(required)* | `"click"`, `"fill"`, or `"remove"`. |
| `args` | `{}` | Arguments matching the `do` action type (see below). |

**Selector args by action type:**

| `do` | Args type | Fields |
|------|-----------|--------|
| `"click"` | `ClickArgs` | `nth` (default `0`: first, `-1`: last, `null`: all) |
| `"fill"` | `FillArgs` | `value` *(required)*, `nth` (default `0`) |
| `"remove"` | `RemoveArgs` | `nth` (default `0`: first, `-1`: last, `null`: all) |

Validation ensures `args` matches the `do` type. For example, `do: "fill"` requires `args.value`.

**Examples:**

```bash
# Get page text (simplest call — no actions needed)
curl -X POST http://localhost:8000/interact \
  -H "Content-Type: application/json" \
  -d '{}'

# Navigate and get HTML
curl -X POST http://localhost:8000/interact \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "output_action": "html"}'

# Take a full-page screenshot
curl -X POST http://localhost:8000/interact \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "output_action": "screenshot_full"}' \
  --output screenshot.png

# Scroll down, wait, then get text
curl -X POST http://localhost:8000/interact \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "actions": [
      {"action": "scroll_to_bottom", "step_pixels": 1000, "step_delay": 1, "timeout": 10},
      {"action": "idle", "duration": 2}
    ],
    "output_action": "text"
  }'

# Remove ads, then get clean page text
curl -X POST http://localhost:8000/interact \
  -H "Content-Type: application/json" \
  -d '{
    "actions": [
      {"action": "selector", "value": ".advertisement", "do": "remove", "args": {"nth": null}}
    ],
    "output_action": "text"
  }'

# Fill a form and click submit
curl -X POST http://localhost:8000/interact \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/login",
    "actions": [
      {"action": "selector", "value": "input[name=email]", "do": "fill", "args": {"value": "me@example.com"}},
      {"action": "selector", "value": "input[name=password]", "do": "fill", "args": {"value": "secret123"}},
      {"action": "selector", "value": "button[type=submit]", "do": "click"},
      {"action": "idle", "duration": 2}
    ],
    "output_action": "screenshot"
  }' --output result.png

# Click button by XPath
curl -X POST http://localhost:8000/interact \
  -H "Content-Type: application/json" \
  -d '{
    "actions": [
      {"action": "selector", "type": "xml", "value": "//button[contains(text(), \"Accept\")]", "do": "click"}
    ],
    "output_action": "html"
  }'

# Click at coordinates, then screenshot
curl -X POST http://localhost:8000/interact \
  -H "Content-Type: application/json" \
  -d '{
    "actions": [
      {"action": "mouse_click", "x": 100, "y": 200},
      {"action": "idle", "duration": 1}
    ],
    "output_action": "screenshot"
  }' --output clicked.png

# Set HTTP Basic Auth and access protected page
curl -X POST http://localhost:8000/interact \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://protected.example.com/admin",
    "actions": [
      {"action": "login", "username": "admin", "password": "secret123"}
    ],
    "output_action": "html"
  }'

# Clear HTTP credentials
curl -X POST http://localhost:8000/interact \
  -H "Content-Type: application/json" \
  -d '{"actions": [{"action": "login", "username": "", "password": ""}]}'

# Double-click
curl -X POST http://localhost:8000/interact \
  -H "Content-Type: application/json" \
  -d '{"actions": [{"action": "mouse_click", "x": 100, "y": 200, "click_count": 2}]}'

# Right-click
curl -X POST http://localhost:8000/interact \
  -H "Content-Type: application/json" \
  -d '{"actions": [{"action": "mouse_click", "x": 100, "y": 200, "button": "right"}]}'
```

---

### Attribute

#### `POST /attribute`

Extract structured data from a page using CSS or XPath selectors. Works like `/content` (navigate → idle → scroll) but instead of returning the full page, it parses the HTML with **BeautifulSoup** (CSS) or **lxml** (XPath) and returns a JSON list of extracted values.

Parsing runs in a background thread (`asyncio.to_thread`) so it does not block the event loop.

```json
{
  "url": "https://example.com/products",
  "selectors": [
    {"name": "titles",  "selector": "h2.product-title"},
    {"name": "prices",  "selector": "span.price"},
    {"name": "links",   "selector": "a.product-link", "attribute": "href"},
    {"name": "header",  "selector": "//h1", "type": "xpath"}
  ]
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `url` | *(current page)* | URL to navigate to. |
| `wait_until` | `"commit"` | Navigation wait condition. |
| `timeout` | `30000` | Navigation timeout in ms. |
| `idle` | `2` | Seconds to wait after page loads. |
| `steps` | `8` | Number of scroll steps (0 = no scroll). |
| `step_delay` | `1.5` | Seconds between scroll steps. |
| `step_pixels` | `1500` | Pixels per scroll step. |
| `selectors` | *(required)* | List of selectors (at least 1). |

**Selector fields:**

| Field | Default | Description |
|-------|---------|-------------|
| `name` | *(required)* | Identifier for this result group. |
| `selector` | *(required)* | CSS selector string (or XPath if `type` is `"xpath"`). |
| `type` | `"css"` | `"css"` or `"xpath"`. |
| `attribute` | `null` | Attribute to extract (e.g. `"href"`, `"src"`). If `null`, extracts text content. |

**Response:**

```json
[
  {"name": "titles", "values": ["Product A", "Product B"]},
  {"name": "prices", "values": ["$9.99", "$19.99"]},
  {"name": "links", "values": ["https://example.com/a", "https://example.com/b"]},
  {"name": "header", "values": ["Our Products"]}
]
```

**Examples:**

```bash
# Extract all links from a page
curl -X POST http://localhost:8000/attribute \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "steps": 0,
    "selectors": [
      {"name": "links", "selector": "a", "attribute": "href"}
    ]
  }'

# Extract product info (text + attributes)
curl -X POST http://localhost:8000/attribute \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://shop.example.com",
    "selectors": [
      {"name": "names",   "selector": "h2.product-name"},
      {"name": "prices",  "selector": "span.price"},
      {"name": "images",  "selector": "img.product-image", "attribute": "src"}
    ]
  }'

# XPath: extract attribute values directly
curl -X POST http://localhost:8000/attribute \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "steps": 0,
    "selectors": [
      {"name": "hrefs", "selector": "//a/@href", "type": "xpath"}
    ]
  }'

# Mix CSS and XPath selectors
curl -X POST http://localhost:8000/attribute \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "selectors": [
      {"name": "titles",  "selector": "h1"},
      {"name": "meta",    "selector": "//meta[@name=\"description\"]/@content", "type": "xpath"}
    ]
  }'
```

---

### Lenta

Session-scoped endpoints for [lenta.com](https://lenta.com). `bootstrap` opens the real storefront in the browser session, waits for the region/anti-bot context to settle, and caches it; item fetches then go through the site's own JSON API with the browser's cookies and forwarded client headers.

| Endpoint | Description |
|----------|-------------|
| `POST /lenta/bootstrap` | Bootstrap a storefront context for the current session (call once per session). |
| `POST /lenta/items/{product_id}` | Fetch one canonical product by ID. `url` must be a canonical `https://lenta.com/product/...-<id>/` link. |
| `GET /lenta/metrics` | Cache metrics for the bootstrapped contexts. |

### Utkonos

Session-scoped endpoints for [utkonos.ru](https://utkonos.ru), same pattern: `bootstrap` establishes the real session, catalog and item fetches use the site's own API.

| Endpoint | Description |
|----------|-------------|
| `POST /utkonos/bootstrap` | Bootstrap a storefront context for the current session. |
| `GET /utkonos/categories` | List catalog categories (leaf categories are the API-drivable ones). |
| `POST /utkonos/catalog/items` | Fetch one bounded catalog page: `{ "category_id": "...", "limit": 40, "offset": 0 }`. |
| `POST /utkonos/items/{product_id}` | Fetch one canonical product by SKU (`https://utkonos.ru/item/<sku>/`). |
| `GET /utkonos/metrics` | Cache metrics. |

### Search

#### `POST /search`

Search the web using Google and return structured results with optional wiki data.
Only real external links are returned (internal Google links like `#` or `/search...` are filtered out).

```json
{
  "query": "fastapi tutorial",
  "count": 10,
  "idle": 3,
  "max_pages": 5
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `query` | *(required)* | Search query string. |
| `count` | `5` | Maximum number of results. |
| `idle` | `3.0` | Seconds to wait after page loads before parsing. |
| `max_pages` | `10` | Maximum number of Google result pages to paginate through. |

**Response structure:**

```json
{
  "wiki": {
    "desc": "...",
    "wiki_links": ["https://en.wikipedia.org/..."],
    "related_links": [],
    "misc_links": []
  },
  "results": [
    {
      "link": "https://...",
      "title": "...",
      "snippet": "...",
      "favicon": "data:image/...",
      "metadata": {
        "rating": {"rating": 4.9, "reviews": 1234, "description": "..."},
        "thumbnail": "data:image/..."
      }
    }
  ]
}
```

---

## Complete Workflow Example

```bash
# 1. Create a browser with persistent profile
curl -X POST http://localhost:8000/browsers \
  -H "Content-Type: application/json" \
  -d '{"profile_uid": "work-profile"}'

# 2. Start a session in that browser
curl -X POST http://localhost:8000/start_session \
  -H "X-Browser-Id: work-profile"
# Returns: {"session_id": "abc-123", "browser_id": "work-profile", ...}

# 3. Navigate and get content with scrolling
curl -X POST http://localhost:8000/content \
  -H "X-Browser-Id: work-profile" \
  -H "X-Session-Id: abc-123" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "output_action": "html", "steps": 6}'

# 4. Remove cookie banners and get clean HTML
curl -X POST http://localhost:8000/interact \
  -H "X-Browser-Id: work-profile" \
  -H "X-Session-Id: abc-123" \
  -H "Content-Type: application/json" \
  -d '{
    "actions": [
      {"action": "selector", "value": ".cookie-banner", "do": "remove"},
      {"action": "selector", "value": ".advertisement", "do": "remove", "args": {"nth": null}}
    ],
    "output_action": "html"
  }'

# 5. Take screenshot
curl -X POST http://localhost:8000/interact \
  -H "X-Browser-Id: work-profile" \
  -H "X-Session-Id: abc-123" \
  -H "Content-Type: application/json" \
  -d '{"output_action": "screenshot"}' \
  --output screenshot.png

# 6. End session
curl -X DELETE http://localhost:8000/end_session \
  -H "X-Browser-Id: work-profile" \
  -H "X-Session-Id: abc-123"

# 7. Close browser (optional — profile persists for next time)
curl -X DELETE "http://localhost:8000/browsers/work-profile"
```

## Using Default Browser

For simple use cases, you don't need to manage browsers explicitly:

```bash
# Start session in default browser
curl -X POST http://localhost:8000/start_session

# Get page content
curl -X POST http://localhost:8000/content \
  -H "X-Session-Id: abc-123" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'

# End session
curl -X DELETE http://localhost:8000/end_session \
  -H "X-Session-Id: abc-123"
```

## Parallel Scraping Example

```python
import asyncio
import aiohttp

API_URL = "http://localhost:8000"

async def scrape_url(session, url):
    """
    Creates a new browser tab (session), scrapes content, and closes the tab.
    Uses the default browser profile automatically.
    """
    try:
        # 1. Start a new session (tab)
        async with session.post(f"{API_URL}/start_session") as resp:
            data = await resp.json()
            session_id = data["session_id"]

        # 2. Navigate, scroll, and get content
        headers = {"X-Session-Id": session_id}
        payload = {
            "url": url,
            "output_action": "text",
            "wait_until": "domcontentloaded",
            "idle": 1,
            "steps": 4,
        }
        
        async with session.post(f"{API_URL}/content", json=payload, headers=headers) as resp:
            content = await resp.text()
            print(f"[{url}] Content length: {len(content)}")
            
        # 3. Cleanup: close the tab
        async with session.delete(f"{API_URL}/end_session", headers=headers) as resp:
            await resp.json()
            
        return {"url": url, "content_length": len(content)}

    except Exception as e:
        print(f"Error scraping {url}: {e}")
        return {"url": url, "error": str(e)}

async def main():
    urls = [
        "https://example.com",
        "https://python.org",
        "https://fastapi.tiangolo.com",
        "https://github.com",
        "https://news.ycombinator.com",
    ]

    async with aiohttp.ClientSession() as session:
        tasks = [scrape_url(session, url) for url in urls]
        results = await asyncio.gather(*tasks)
        
        print("\n--- Results ---")
        for res in results:
            print(res)

if __name__ == "__main__":
    # uv add aiohttp
    asyncio.run(main())
```

### Key Points

1. **Isolation** — Each `scrape_url` call creates its own session (tab). Concurrent requests don't interfere.
2. **Concurrency** — `asyncio.gather` runs all requests simultaneously. The browser handles multiple tabs efficiently.
3. **Cleanup** — Always call `end_session` to close the tab and free memory.
4. **Default Browser** — By not specifying `X-Browser-Id`, all sessions use the default persistent profile.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VNC_PW` | `headless` | VNC password |
| `VNC_RESOLUTION` | `1920x1080` | Screen resolution |
| `DISPLAY` | `:1` | X display number |
| `BROWSER_CDP_MODE` | _(off)_ | `1`/`true`/`yes` enables CDP mode (see below). |
| `CHROME_CDP_TRANSPORT` | `tcp` | DevTools transport: `tcp` (recommended) or `pipe`. |
| `CHROME_HEADLESS` | `0` | `0` keeps Chrome headful in the VNC desktop (recommended for CDP mode). |
| `CHROME_BIN` | _(auto)_ | Explicit path to the Chrome binary. |
| `CHROME_CDP_PORT_BASE` | `9333` | Base port for per-browser DevTools endpoints. |
| `CHROME_NO_SANDBOX` | _(off)_ | `1` disables the Chrome sandbox (needed when the container lacks kernel namespace support). |
| `CHROME_CDP_EXTRA_ARGS` | _(none)_ | Comma-separated extra Chrome flags (e.g. `--no-first-run,--start-maximized`). |
| `CHROME_LANG` / `CHROME_LOCALE` | _(container locale)_ | Browser `navigator.language` / locale; keep consistent with your egress IP geo to avoid WAF flags. |

### CDP Mode

Some WAFs (e.g. av.ru) flag browsers started with the full Playwright/Patchright argument set. CDP mode inverts the launch: Chrome is started **as a regular X11 application** with minimal flags, and Patchright then attaches to it over its local DevTools endpoint (`connect_over_cdp`). The controlled browser keeps a genuine fingerprint (real UA/platform from the actual Chrome build, no spoofing) and stays visible in the container's VNC desktop.

Enable with `BROWSER_CDP_MODE=1` (see `compose.yml` for a full working setup). Notes:

- CDP mode requires a `profile_uid` (persistent profile); proxy is **not supported** in this mode and is ignored with a warning.
- `CHROME_CDP_TRANSPORT=tcp` launches headful Chrome-for-Testing as a normal X11 app; `pipe` uses the stdio transport instead.
- Works alongside the standard Patchright launch path — the flag simply selects which `_create_browser_*` implementation runs.

### Kubernetes / Helm

A Helm chart is available in the `livellm-browser-chart` directory.

```bash
helm install livellm-browser ./livellm-browser-chart
```

## Project Structure

```
livellm-browser/
├── main.py               # FastAPI app entry point & lifespan
├── core/
│   ├── browser.py         # BrowserManager, BrowserInfo, profile management, CDP launch path
│   ├── network_trace.py   # NetworkTraceRecorder (bounded request/response capture)
│   └── dependencies.py    # FastAPI dependency injection (PageDep, etc.)
├── routes/
│   ├── health.py          # GET /ping
│   ├── browsers.py        # Browser & session CRUD
│   ├── search.py          # POST /search (Google)
│   ├── content.py         # POST /content (scroll + extract, optional network trace)
│   ├── interact.py        # POST /interact (actions + selectors + output)
│   ├── attribute.py       # POST /attribute (BS4/lxml data extraction)
│   ├── lenta.py           # /lenta/* storefront endpoints
│   └── utkonos.py         # /utkonos/* storefront endpoints
├── helpers/
│   ├── playwright.py      # Locator builders, scroll helpers
│   └── bs.py              # BeautifulSoup / lxml extraction helpers
├── models/
│   ├── requests.py        # Pydantic request models & OutputAction enum
│   └── responses.py       # Pydantic response models (incl. network trace envelope)
├── tests/
│   ├── conftest.py        # Pytest fixtures with mocked browser
│   ├── test_smoke.py      # Smoke tests for all endpoints
│   ├── test_network_trace.py
│   ├── test_lenta.py
│   └── test_utkonos.py
├── parse.py               # Example crawler script (uses the API)
├── Dockerfile
├── compose.yml
└── pyproject.toml
```

## Tech Stack

- **FastAPI** — REST API framework
- **Patchright** — Undetectable Playwright fork
- **BeautifulSoup4** + **lxml** — Fast HTML parsing for `/attribute` endpoint
- **Docker** — Containerization
- **XFCE** — Desktop environment
- **TigerVNC** — VNC server
- **noVNC** — Web-based VNC client
- **UV** — Python package manager

## License

MIT
