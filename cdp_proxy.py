"""Stable CDP WebSocket endpoint expected by the LiveLLM operator."""
import asyncio
import json
import os
import urllib.request

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import websockets

CHROME_CDP_HOST = os.getenv("CHROME_CDP_HOST", "127.0.0.1")
CHROME_CDP_PORT = int(os.getenv("CHROME_CDP_PORT_BASE", "9333"))

app = FastAPI(title="LiveLLM CDP proxy")


def target_ws_url(payload: dict) -> str:
    url = payload.get("webSocketDebuggerUrl")
    if not isinstance(url, str) or not url.startswith("ws"):
        raise RuntimeError("Chrome did not return webSocketDebuggerUrl")
    return url


def _read_target() -> str:
    url = f"http://{CHROME_CDP_HOST}:{CHROME_CDP_PORT}/json/version"
    with urllib.request.urlopen(url, timeout=3) as response:
        return target_ws_url(json.load(response))


async def _client_to_chrome(client: WebSocket, chrome) -> None:
    try:
        while True:
            message = await client.receive()
            if message["type"] == "websocket.disconnect":
                return
            await chrome.send(message.get("text") if message.get("text") is not None else message.get("bytes"))
    except (WebSocketDisconnect, websockets.ConnectionClosed):
        pass


async def _chrome_to_client(chrome, client: WebSocket) -> None:
    try:
        async for message in chrome:
            if isinstance(message, str):
                await client.send_text(message)
            else:
                await client.send_bytes(message)
    except (WebSocketDisconnect, websockets.ConnectionClosed):
        pass


@app.websocket("/devtools/browser/default")
async def default_browser(websocket: WebSocket) -> None:
    try:
        target = await asyncio.to_thread(_read_target)
        async with websockets.connect(target, max_size=None) as chrome:
            await websocket.accept()
            client_to_chrome = asyncio.create_task(_client_to_chrome(websocket, chrome))
            chrome_to_client = asyncio.create_task(_chrome_to_client(chrome, websocket))
            done, pending = await asyncio.wait((client_to_chrome, chrome_to_client), return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
    except Exception:
        await websocket.close(code=1011)
