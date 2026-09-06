import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
import logging
from pathlib import Path
from typing import Optional

from patchright.async_api import Playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

# Absolute, CWD-independent: the CDP de-elevation re-launch of Chrome may run
# with a different working directory, so a relative profile path would resolve
# to the wrong place for the de-elevated child.
PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"
DEFAULT_BROWSER_ID = "default"


def cleanup_profile_locks(profile_path: Path):
    """Remove Chrome lock files from a profile directory to prevent startup errors."""
    if not profile_path.exists():
        return

    for lock_name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        lock_file = profile_path / lock_name
        if os.path.lexists(lock_file):
            try:
                if os.path.islink(lock_file):
                    os.unlink(lock_file)
                elif lock_file.is_dir():
                    shutil.rmtree(lock_file)
                else:
                    lock_file.unlink()
                logger.info(f"Removed lock file: {lock_file}")
            except Exception as e:
                logger.warning(f"Failed to remove lock file {lock_file}: {e}")


class BrowserInfo:
    """Container for browser, context, and its associated pages."""

    def __init__(self, browser: Browser, context: BrowserContext, profile_path: Optional[Path] = None):
        self.browser = browser
        self.context = context
        self.profile_path = profile_path
        self.pages: dict[str, Page] = {}


class BrowserManager:
    """Manages multiple browser instances with persistent and ephemeral profiles."""

    def __init__(self):
        self.playwright: Optional[Playwright] = None
        self.browsers: dict[str, BrowserInfo] = {}
        # CDP mode: Chrome processes we launched ourselves (browser_id -> Popen)
        self._chrome_procs: dict[str, subprocess.Popen] = {}
        self._cdp_port_counter = 0

    async def start(self, playwright: Playwright):
        """Initialize with a Playwright instance and create the default browser."""
        self.playwright = playwright
        await self.create_browser(profile_uid=DEFAULT_BROWSER_ID)
        logger.info("Browser manager started with default browser")

    async def create_browser(self, profile_uid: Optional[str] = None, proxy=None) -> tuple[str, BrowserInfo]:
        """
        Create a new browser instance.

        Args:
            profile_uid: If provided, creates persistent profile in profiles/{uid}.
                         If not provided, creates an ephemeral browser.
            proxy: Optional proxy settings (ProxySettings model).

        Returns:
            Tuple of (browser_id, BrowserInfo).
        """
        if not self.playwright:
            raise RuntimeError("Browser manager not started")

        # CDP mode: launch Chrome the NORMAL way (minimal flags) and attach
        # over the DevTools TCP port. Some anti-bot WAFs (e.g. the one in
        # front of av.ru) block browsers started with the full Playwright
        # argument set, but accept a normally-launched Chrome with a CDP
        # connection. Enable with BROWSER_CDP_MODE=1.
        if os.environ.get("BROWSER_CDP_MODE", "") in ("1", "true", "yes"):
            if proxy:
                logger.warning("Proxy is not supported in CDP mode; ignoring it")
            return await self._create_browser_cdp(profile_uid)

        if profile_uid:
            browser_id = profile_uid
            profile_path = PROFILES_DIR / profile_uid
            cleanup_profile_locks(profile_path)
            is_persistent = True
        else:
            browser_id = str(uuid.uuid4())
            profile_path = None
            is_persistent = False

        if browser_id in self.browsers:
            raise ValueError(f"Browser with id '{browser_id}' already exists")

        # Build proxy config
        proxy_config = None
        if proxy:
            proxy_config = {"server": proxy.server}
            if proxy.username:
                proxy_config["username"] = proxy.username
            if proxy.password:
                proxy_config["password"] = proxy.password
            if proxy.bypass:
                proxy_config["bypass"] = proxy.bypass
            logger.info(f"Browser '{browser_id}' configured with proxy: {proxy.server}")

        # Fingerprint config (env-driven): some WAFs flag a browser whose
        # language/UA disagree with the egress IP geolocation.
        chrome_lang = os.environ.get("CHROME_LANG", "")
        chrome_locale = os.environ.get("CHROME_LOCALE", "")
        chrome_ua = os.environ.get("CHROME_USER_AGENT", "")

        def _fingerprint_kwargs() -> dict:
            kwargs = {}
            if chrome_locale:
                kwargs["locale"] = chrome_locale
            if chrome_ua:
                kwargs["user_agent"] = chrome_ua
            return kwargs

        def _fingerprint_args() -> list:
            args = ["--start-maximized"]
            if chrome_lang:
                args.append(f"--lang={chrome_lang}")
            # Software WebGL renderer: a container without a GPU reports a
            # null WebGL context, which is a strong automation signal.
            # On a real GPU machine (e.g. native Windows) set
            # CHROME_SWIFTSHADER=0 to use the hardware renderer instead.
            if os.environ.get("CHROME_SWIFTSHADER", "1") != "0":
                args.append("--use-angle=swiftshader")
                args.append("--enable-unsafe-swiftshader")
            return args

        platform_override = os.environ.get("CHROME_PLATFORM_OVERRIDE", "")

        browser = None
        context = None

        if is_persistent:
            launch_kwargs = {
                "user_data_dir": str(profile_path),
                "headless": False,
                "channel": "chrome",
                "no_viewport": True,
                "args": _fingerprint_args(),
            }
            launch_kwargs.update(_fingerprint_kwargs())
            if proxy_config:
                launch_kwargs["proxy"] = proxy_config
            context = await self.playwright.chromium.launch_persistent_context(**launch_kwargs)
            browser = context.browser
        else:
            launch_kwargs = {
                "headless": False,
                "channel": "chrome",
                "args": _fingerprint_args(),
            }
            launch_kwargs.update(_fingerprint_kwargs())
            if proxy_config:
                launch_kwargs["proxy"] = proxy_config
            browser = await self.playwright.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                no_viewport=True,
                **({"user_agent": chrome_ua, "locale": chrome_locale} if (chrome_ua or chrome_locale) else {}),
            )

        if browser is None and context:
            browser = context.browser
        if browser is None:
            raise RuntimeError(f"Failed to get browser object for {browser_id}")

        # Align navigator.platform with the declared UA at the context level
        # so it applies to EVERY page, including sessions created via
        # /start_session (which bypass the per-page PageDep hook).
        if platform_override:
            try:
                await context.add_init_script(
                    "Object.defineProperty(navigator, 'platform', "
                    "{get: () => %s});" % json.dumps(platform_override)
                )
            except Exception as e:
                logger.warning(f"Could not add platform init script for '{browser_id}': {e}")

        browser_info = BrowserInfo(browser, context, profile_path)
        self.browsers[browser_id] = browser_info

        kind = "persistent" if is_persistent else "ephemeral"
        logger.info(f"Created {kind} browser '{browser_id}'" + (f" with profile at {profile_path}" if is_persistent else ""))
        return browser_id, browser_info

    # ---------------- CDP mode ----------------

    @staticmethod
    def _cdp_mode_enabled() -> bool:
        return os.environ.get("BROWSER_CDP_MODE", "") in ("1", "true", "yes")

    @staticmethod
    def _cdp_transport() -> str:
        """CDP transport selection.

        tcp  (default): Popen Chrome with --remote-debugging-port and attach
                        over the DevTools HTTP endpoint (works on Windows).
        pipe:           let the driver manage the DevTools pipe transport and
                        launch Chrome with ignore_default_args=True plus only
                        minimal honest flags (for containers, where the retail
                        Chrome build silently ignores the DevTools port/pipe
                        flags behind an unattended-unsatisfiable consent gate).
        """
        t = os.environ.get("CHROME_CDP_TRANSPORT", "tcp").strip().lower()
        return t if t in ("tcp", "pipe") else "tcp"

    @staticmethod
    def _headless_enabled() -> bool:
        """Return whether Chrome should run without the X11 window.

        Headful remains the default for native/VNC deployments. Linux Chrome
        152 currently starts its pipe transport only in headless mode in the
        Docker runtime, so compose enables this explicitly there.
        """
        return os.environ.get("CHROME_HEADLESS", "").strip().lower() in ("1", "true", "yes")

    def _chrome_exe(self) -> str:
        override = os.environ.get("CHROME_BIN", "")
        if override:
            return override
        if sys.platform.startswith("win"):
            candidates = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            ]
            for cand in candidates:
                if Path(cand).exists():
                    return cand
        for name in ("google-chrome", "google-chrome-stable", "chromium"):
            found = shutil.which(name)
            if found:
                return found
        raise RuntimeError("Chrome executable not found; set CHROME_BIN")

    def _pipe_chrome_exe(self) -> str:
        """Resolve the Chrome executable for pipe mode.

        Prefers the Chrome-for-Testing binary from the patchright/Playwright
        registry cache: in headless containers the retail Chrome build gates
        DevTools remote debugging behind a user-consent flow that cannot be
        completed unattended, so --remote-debugging-port / --remote-debugging-pipe
        are silently ignored there. CfT is fingerprint-equivalent to retail
        Chrome (same build base, same UA/TLS) and has no such gate.
        Falls back to the system Chrome (works on Windows and on hosts where
        the retail build serves DevTools normally).
        """
        override = os.environ.get("CHROME_BIN", "")
        if override:
            return override
        cache = Path.home() / ".cache" / "ms-playwright"
        if cache.is_dir():
            cands = [p for p in cache.iterdir() if p.is_dir() and p.name.startswith("chrome-")]

            def _ver(p: Path):
                return [int(x) for x in p.name.split("-", 1)[1].split(".") if x.isdigit()]

            for p in sorted(cands, key=_ver, reverse=True):
                for sub in ("chrome-linux64", "chrome-linux"):
                    exe = p / sub / "chrome"
                    if exe.exists() and os.access(exe, os.X_OK):
                        return str(exe)
        return self._chrome_exe()

    @staticmethod
    async def _cdp_ready(port: int, timeout: float = 30.0) -> bool:
        """Poll the DevTools HTTP endpoint until it answers (or timeout)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as resp:
                    resp.read()
                    return True
            except Exception:
                await asyncio.sleep(0.5)
        return False

    async def _create_browser_cdp(self, profile_uid: Optional[str]) -> tuple[str, BrowserInfo]:
        """Create a browser by launching Chrome normally and attaching via CDP."""
        if not profile_uid:
            raise ValueError("CDP mode requires a profile_uid")
        if profile_uid in self.browsers:
            raise ValueError(f"Browser with id '{profile_uid}' already exists")

        if self._cdp_transport() == "pipe":
            return await self._create_browser_pipe(profile_uid)

        profile_path = PROFILES_DIR / profile_uid
        profile_path.mkdir(parents=True, exist_ok=True)
        cleanup_profile_locks(profile_path)

        port = int(os.environ.get("CHROME_CDP_PORT_BASE", "9333")) + self._cdp_port_counter
        self._cdp_port_counter += 1

        lang = os.environ.get("CHROME_LANG", "") or "ru-RU"
        args = [
            self._chrome_exe(),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_path.as_posix()}",
            f"--lang={lang}",
        ]
        # Optional extra flags (e.g. --use-angle=swiftshader in a GPU-less container)
        extra = os.environ.get("CHROME_CDP_EXTRA_ARGS", "")
        if extra:
            args.extend(a.strip() for a in extra.split(",") if a.strip())

        if not await self._cdp_ready(port, timeout=2.0):
            logger.info(f"Launching Chrome (CDP mode) for '{profile_uid}' on port {port}: {' '.join(args[1:])}")
            proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._chrome_procs[profile_uid] = proc
            if not await self._cdp_ready(port, timeout=30.0):
                proc.terminate()
                self._chrome_procs.pop(profile_uid, None)
                self._cdp_port_counter -= 1
                hint = ""
                if proc.poll() is not None:
                    hint = (
                        " (the launcher process exited immediately: another Chrome instance "
                        f"is probably holding the profile '{profile_path}' - close it and retry)"
                    )
                raise RuntimeError(f"Chrome CDP endpoint did not come up on port {port}{hint}")
        else:
            logger.info(f"Attaching to already-running Chrome for '{profile_uid}' on port {port}")

        browser = await self.playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        if not browser.contexts:
            context = await browser.new_context()
        else:
            context = browser.contexts[0]

        # Honest UA/platform in CDP mode (no spoofing); keep the optional
        # platform init-script path for parity with the launch path.
        platform_override = os.environ.get("CHROME_PLATFORM_OVERRIDE", "")
        if platform_override:
            try:
                await context.add_init_script(
                    "Object.defineProperty(navigator, 'platform', "
                    "{get: () => %s});" % json.dumps(platform_override)
                )
            except Exception as e:
                logger.warning(f"Could not add platform init script for '{profile_uid}': {e}")

        browser_info = BrowserInfo(browser, context, profile_path)
        self.browsers[profile_uid] = browser_info
        logger.info(f"Created CDP browser '{profile_uid}' (port {port})")
        return profile_uid, browser_info

    async def _create_browser_pipe(self, profile_uid: str) -> tuple[str, BrowserInfo]:
        """Create a browser through the driver-managed DevTools pipe transport.

        Chrome is launched with ignore_default_args=True — none of the driver's
        default automation flag set — plus only a minimal honest argument list,
        the same command-line shape the Popen+CDP recipe uses (which is what
        anti-bot WAFs accept). Because ignore_default_args=True suppresses the
        driver's own defaults entirely, --remote-debugging-pipe, --user-data-dir
        and the startup about:blank page must be passed explicitly; the Node
        driver still owns the pipe fds (3/4) and speaks the DevTools protocol.
        """
        profile_path = PROFILES_DIR / profile_uid
        profile_path.mkdir(parents=True, exist_ok=True)
        cleanup_profile_locks(profile_path)

        lang = os.environ.get("CHROME_LANG", "") or "ru-RU"
        args = [
            "--remote-debugging-pipe",
            f"--user-data-dir={profile_path.as_posix()}",
            "about:blank",
            f"--lang={lang}",
        ]
        # ignore_default_args=True suppresses Patchright's headless CLI flag
        # along with every other default, so the API option alone is not
        # sufficient: Chrome must receive the flag explicitly as well.
        if self._headless_enabled():
            args.append("--headless=new")
        # Containers without real namespace support need the sandbox off.
        if os.environ.get("CHROME_NO_SANDBOX", "0") in ("1", "true", "yes"):
            args.append("--no-sandbox")
        # Fill the (VNC) desktop so window size matches screen size.
        if os.environ.get("CHROME_MAXIMIZED", "1") not in ("0", "false", "no"):
            args.append("--start-maximized")
        extra = os.environ.get("CHROME_CDP_EXTRA_ARGS", "")
        if extra:
            args.extend(a.strip() for a in extra.split(",") if a.strip())

        launch_kwargs: dict = {
            "user_data_dir": str(profile_path),
            "headless": self._headless_enabled(),
            "ignore_default_args": True,
            "args": args,
            # No viewport emulation: report the real window size.
            "no_viewport": True,
        }
        locale = os.environ.get("CHROME_LOCALE", "")
        if locale:
            launch_kwargs["locale"] = locale
        try:
            launch_kwargs["executable_path"] = self._pipe_chrome_exe()
            logger.info(f"Pipe mode: using executable {launch_kwargs['executable_path']}")
        except RuntimeError as e:
            logger.warning(f"Pipe mode: no Chrome executable found ({e}); using driver default")

        context = await self.playwright.chromium.launch_persistent_context(**launch_kwargs)
        browser = context.browser
        if browser is None:
            browser = context  # type: ignore[assignment]
        browser_info = BrowserInfo(browser, context, profile_path)
        self.browsers[profile_uid] = browser_info
        logger.info(f"Created pipe browser '{profile_uid}' (profile {profile_path})")
        return profile_uid, browser_info

    def _terminate_chrome(self, browser_id: str) -> bool:
        proc = self._chrome_procs.pop(browser_id, None)
        if proc is None:
            return False
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        logger.info(f"Terminated Chrome process for '{browser_id}'")
        return True

    # ---------------- Regular mode ----------------

    def get_browser(self, browser_id: str) -> BrowserInfo:
        """Get a browser by its ID. Raises KeyError if not found."""
        if browser_id not in self.browsers:
            raise KeyError(f"Browser with id '{browser_id}' not found")
        return self.browsers[browser_id]

    def get_default_browser(self) -> BrowserInfo:
        return self.browsers[DEFAULT_BROWSER_ID]

    def get_default_browser_id(self) -> str:
        return DEFAULT_BROWSER_ID

    async def close_browser(self, browser_id: str) -> bool:
        """Close and remove a browser instance. Cannot close the default browser."""
        if browser_id == DEFAULT_BROWSER_ID:
            raise ValueError("Cannot close the default browser")
        if browser_id not in self.browsers:
            return False

        # CDP mode: the Chrome process owns the context; stop it directly.
        if self._terminate_chrome(browser_id):
            del self.browsers[browser_id]
            logger.info(f"Closed CDP browser '{browser_id}'")
            return True

        browser_info = self.browsers[browser_id]
        for page in browser_info.pages.values():
            try:
                await page.close()
            except Exception as e:
                logger.warning(f"Error closing page: {e}")
        try:
            await browser_info.context.close()
        except Exception as e:
            logger.warning(f"Error closing context: {e}")
        try:
            await browser_info.browser.close()
        except Exception as e:
            logger.warning(f"Error closing browser: {e}")

        del self.browsers[browser_id]
        logger.info(f"Closed browser '{browser_id}'")
        return True

    async def shutdown(self, timeout: float = 25.0):
        """Close all browsers with timeout protection."""
        logger.info("Starting browser shutdown...")

        async def _shutdown_task():
            for browser_id in list(self.browsers.keys()):
                if self._terminate_chrome(browser_id):
                    self.browsers.pop(browser_id, None)
                    continue
                info = self.browsers[browser_id]
                for page in info.pages.values():
                    try:
                        await asyncio.wait_for(page.close(), timeout=2.0)
                    except (asyncio.TimeoutError, Exception) as e:
                        logger.warning(f"Error closing page: {e}")
                try:
                    await asyncio.wait_for(info.context.close(), timeout=5.0)
                except (asyncio.TimeoutError, Exception) as e:
                    logger.warning(f"Error closing context for browser {browser_id}: {e}")
                try:
                    await asyncio.wait_for(info.browser.close(), timeout=5.0)
                except (asyncio.TimeoutError, Exception) as e:
                    logger.warning(f"Error closing browser {browser_id}: {e}")
            self.browsers.clear()
            logger.info("All browsers closed")

        try:
            await asyncio.wait_for(_shutdown_task(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(f"Shutdown timed out after {timeout}s, forcing cleanup")
            self.browsers.clear()


# Global singleton
browser_manager = BrowserManager()
