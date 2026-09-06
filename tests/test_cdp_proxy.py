import pytest

from cdp_proxy import target_ws_url


def test_target_ws_url_reads_chrome_devtools_endpoint():
    payload = {"webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/browser/abc"}

    assert target_ws_url(payload) == "ws://127.0.0.1:9333/devtools/browser/abc"


def test_target_ws_url_rejects_missing_endpoint():
    with pytest.raises(RuntimeError, match="webSocketDebuggerUrl"):
        target_ws_url({})
