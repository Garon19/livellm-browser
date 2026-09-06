import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import core.browser as browser_module
from core.browser import BrowserManager


def _compose_environment() -> dict[str, str]:
    """Read the controller's list-form Compose environment without PyYAML."""
    compose = Path(__file__).resolve().parents[1] / "compose.yml"
    environment: dict[str, str] = {}
    in_environment = False
    for raw_line in compose.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if stripped == "environment:":
            in_environment = True
            continue
        if in_environment and raw_line.startswith("    ") and not raw_line.startswith("      "):
            break
        if in_environment and stripped.startswith("- "):
            key, value = stripped[2:].split("=", 1)
            environment[key] = value
    return environment


def test_compose_runs_controller_chrome_headful_over_cft_tcp():
    environment = _compose_environment()

    assert environment["CHROME_CDP_TRANSPORT"] == "tcp"
    assert environment["CHROME_HEADLESS"] == "0"
    assert environment["CHROME_BIN"] == (
        "/home/headless/.local/bin/livellm-chrome"
    )
    extra_args = environment["CHROME_CDP_EXTRA_ARGS"].split(",")
    assert "--no-sandbox" in extra_args
    assert "--no-first-run" in extra_args
    assert "--start-maximized" in extra_args


@pytest.mark.parametrize("value", ["1", "true", "yes", "TRUE", " Yes "])
def test_headless_enabled_accepts_truthy_values(monkeypatch, value):
    monkeypatch.setenv("CHROME_HEADLESS", value)
    assert BrowserManager._headless_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "unexpected"])
def test_headless_enabled_defaults_to_false(monkeypatch, value):
    monkeypatch.setenv("CHROME_HEADLESS", value)
    assert BrowserManager._headless_enabled() is False


@pytest.mark.asyncio
async def test_pipe_headless_sets_api_option_and_explicit_chrome_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("CHROME_HEADLESS", "1")
    monkeypatch.setattr(browser_module, "PROFILES_DIR", tmp_path)

    context = SimpleNamespace(browser=object())
    launch = AsyncMock(return_value=context)
    manager = BrowserManager()
    manager.playwright = SimpleNamespace(
        chromium=SimpleNamespace(launch_persistent_context=launch)
    )
    monkeypatch.setattr(manager, "_pipe_chrome_exe", lambda: "/chrome")

    await manager._create_browser_pipe("headless-test")

    kwargs = launch.await_args.kwargs
    assert kwargs["headless"] is True
    assert "--headless=new" in kwargs["args"]
