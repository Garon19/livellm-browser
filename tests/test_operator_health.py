from core.browser import DEFAULT_BROWSER_ID, browser_manager


def test_operator_health_is_ready_when_default_browser_is_connected(client, monkeypatch):
    browser = type("Browser", (), {"is_connected": lambda self: True})()
    info = type("BrowserInfo", (), {"browser": browser})()
    monkeypatch.setattr(browser_manager, "browsers", {DEFAULT_BROWSER_ID: info})

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_operator_health_is_unavailable_without_default_browser(client, monkeypatch):
    monkeypatch.setattr(browser_manager, "browsers", {})

    response = client.get("/health")

    assert response.status_code == 503
