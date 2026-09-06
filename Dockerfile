# contains with controller

# docker build -f Dockerfile -t qalby-tech/ubuntu-xfce-vnc-firefox .
# FROM kamasalyamov/ubuntu-xfce-vnc-firefox:latest 
# FROM accetto/ubuntu-vnc-xfce-firefox-g3:latest
FROM accetto/ubuntu-vnc-xfce-g3:24.04

# Switch to root to install dependencies
# USER root
USER 0

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Use Yandex Ubuntu mirror (archive.ubuntu.com is unreliable from RU)
# Handles both legacy /etc/apt/sources.list and noble's deb822 .sources files
RUN set -eux; \
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources /etc/apt/sources.list.d/*.list; do \
        [ -f "$f" ] || continue; \
        sed -i \
            -e 's|http[s]*://archive.ubuntu.com/ubuntu|http://mirror.yandex.ru/ubuntu|g' \
            -e 's|http[s]*://security.ubuntu.com/ubuntu|http://mirror.yandex.ru/ubuntu|g' \
            "$f"; \
    done

# Install system dependencies for screenshot functionality and display detection
# Also install CJK fonts so Chrome can render Chinese/Japanese/Korean characters
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gnome-screenshot \
    scrot \
    x11-utils \
    curl \
    libnspr4 \
    libnss3 \
    libasound2t64 \
    libatk-bridge2.0-0t64 \
    libatk1.0-0t64 \
    libatspi2.0-0t64 \
    libcups2t64 \
    libdbus-1-3 \
    libdrm2 \
    libgtk-3-0t64 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    locales \
    fonts-noto-cjk \
    fonts-noto-cjk-extra \
    fonts-wqy-zenhei \
    fonts-wqy-microhei \
    && sed -i 's/^# ru_RU.UTF-8 UTF-8/ru_RU.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen ru_RU.UTF-8 \
    && fc-cache -fv \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install Python 3.9 using uv
RUN uv python install 3.9

ENV PYTHONUNBUFFERED=1

WORKDIR "${HOME}"/Desktop/app
# Copy only dependency files first (for better caching)
COPY pyproject.toml uv.lock ./

# Install Python dependencies (cached layer)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev

# Now copy the rest of the application code
COPY . .

# # Create necessary directories for headless user and set permissions
# # profiles/default is the default browser profile directory
# RUN mkdir -p /home/headless/.cache /home/headless/.local/share/uv /controller/profiles/default && \
#     chown -R headless:headless /controller /workspace /home/headless/.cache /home/headless/.local

# Fix permissions for startup script modification
RUN chmod 666 /etc/passwd /etc/group

# Ensure the copied app and uv-managed Python are owned by the runtime user.
# Do not recursively chown the base image's whole cache: it creates a large,
# memory-heavy Docker layer. CfT is installed into that cache after USER below.
RUN chown -R "${HEADLESS_USER_ID}":"${HEADLESS_USER_GROUP_ID}" "${HOME}"/Desktop/app "${HOME}"/.local

# The base image owns $HOME/.cache as root. Create only the registry directory
# needed by the runtime user instead of recursively chowning the whole cache.
RUN mkdir -p "${HOME}/.cache/ms-playwright" && \
    chown "${HEADLESS_USER_ID}":"${HEADLESS_USER_GROUP_ID}" "${HOME}/.cache/ms-playwright"

# Create a custom startup script that waits for VNC, then runs main.py
RUN printf '%s\n' \
    '#!/bin/bash' \
    'set -e' \
    '' \
    '# Track child PIDs for graceful shutdown' \
    'STARTUP_PID=""' \
    'APP_PID=""' \
    '' \
    '# Graceful shutdown handler' \
    'shutdown() {' \
    '  echo "Received shutdown signal, stopping services..."' \
    '  if [ -n "$APP_PID" ] && kill -0 $APP_PID 2>/dev/null; then' \
    '    echo "Stopping main.py (PID $APP_PID)..."' \
    '    kill -TERM $APP_PID 2>/dev/null || true' \
    '    wait $APP_PID 2>/dev/null || true' \
    '  fi' \
    '  if [ -n "$STARTUP_PID" ] && kill -0 $STARTUP_PID 2>/dev/null; then' \
    '    echo "Stopping VNC services (PID $STARTUP_PID)..."' \
    '    kill -TERM $STARTUP_PID 2>/dev/null || true' \
    '    wait $STARTUP_PID 2>/dev/null || true' \
    '  fi' \
    '  echo "Shutdown complete"' \
    '  exit 0' \
    '}' \
    '' \
    '# Trap SIGTERM and SIGINT' \
    'trap shutdown SIGTERM SIGINT' \
    '' \
    '# Start VNC/desktop environment in background' \
    '/dockerstartup/startup.sh "$@" &' \
    'STARTUP_PID=$!' \
    '' \
    '# Wait for X11 display to be ready (up to 80 seconds)' \
    'echo "Waiting for display :1 to be ready..."' \
    'for i in $(seq 1 80); do' \
    '  if xdpyinfo -display :1 >/dev/null 2>&1; then' \
    '    echo "Display :1 is ready!"' \
    '    break' \
    '  fi' \
    '  sleep 1' \
    'done' \
    '' \
    '# Start main.py with logs to stdout (visible in docker logs)' \
    'echo "Starting main.py..."' \
    'cd /home/headless/Desktop/app && /bin/uv run main.py 2>&1 &' \
    'APP_PID=$!' \
    'echo "main.py started (PID $APP_PID)"' \
    '' \
    '# Wait for any child to exit (keeps container running)' \
    'wait -n 2>/dev/null || wait' \
    > /usr/local/bin/custom-startup.sh \
    && chmod +x /usr/local/bin/custom-startup.sh

# Switch back to headless user
USER "${HEADLESS_USER_ID}"

# Install Chrome for Testing for the runtime user (into the Playwright
# registry cache, so core/browser.py's pipe mode finds it automatically).
#
# Why: the retail /opt/google/chrome in the base image silently ignores
# --remote-debugging-port and --remote-debugging-pipe in this container —
# its DevTools remote-debugging server is gated behind a user-consent flow
# that cannot be completed unattended (no port/pipe is ever opened, no
# "DevTools listening" in logs, verified empirically). Chrome for Testing
# is built from the same source base as retail Chrome (identical UA/TLS/JS
# fingerprint) but has no such gate, which is why Playwright automation
# uses it. Pin the version for reproducible builds; bump with Chrome.
ARG CHROME_CFT_VERSION=152.0.7977.82
RUN set -eux; \
    ver="$CHROME_CFT_VERSION"; \
    cache="$HOME/.cache/ms-playwright"; \
    mkdir -p "$cache/chrome-$ver"; \
    curl -fsSL "https://storage.googleapis.com/chrome-for-testing-public/$ver/linux64/chrome-linux64.zip" -o /tmp/cft.zip; \
    python3 -c "import zipfile; zipfile.ZipFile('/tmp/cft.zip').extractall('$cache/chrome-$ver')"; \
    chmod +x "$cache/chrome-$ver/chrome-linux64/chrome" "$cache/chrome-$ver/chrome-linux64/chrome_crashpad_handler" 2>/dev/null || true; \
    rm -f /tmp/cft.zip; \
    "$cache/chrome-$ver/chrome-linux64/chrome" --version

# Use custom entrypoint that starts main.py then hands off to VNC startup
ENTRYPOINT ["/usr/local/bin/custom-startup.sh"]
CMD ["--wait"]
