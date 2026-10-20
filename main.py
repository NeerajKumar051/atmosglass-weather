"""AtmosGlass Weather - desktop launcher."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import webview

from app.bridge import APP_NAME, Api

ROOT = Path(__file__).resolve().parent
FRONTEND_INDEX = ROOT / "frontend" / "dist" / "index.html"

MISSING_BUILD_HTML = """
<!doctype html>
<html lang="en">
  <body style="font-family: system-ui, sans-serif; padding: 2rem; background:#0f172a; color:#f1f5f9;">
    <h2>Frontend build not found</h2>
    <p>Build the frontend first, then start the app again:</p>
    <pre style="background:#1e293b; padding:1rem; border-radius:8px;">cd frontend
npm install
npm run build
cd ..
.\\.venv\\Scripts\\python.exe main.py</pre>
  </body>
</html>
"""


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    if not FRONTEND_INDEX.is_file():
        print(
            f"ERROR: {FRONTEND_INDEX} was not found.\n"
            "Build the frontend first:\n"
            "  cd frontend; npm install; npm run build",
            file=sys.stderr,
        )
        webview.create_window(
            f"{APP_NAME} - build required",
            html=MISSING_BUILD_HTML,
            width=640,
            height=420,
        )
        webview.start()
        return 1

    webview.create_window(
        APP_NAME,
        url=str(FRONTEND_INDEX),
        js_api=Api(),
        width=1100,
        height=760,
        min_size=(480, 600),
    )
    # http_server=True: pywebview serves the local files over http://127.0.0.1,
    # so the app does not run from file://.
    webview.start(http_server=True, debug=os.environ.get("ATMOSGLASS_DEBUG") == "1")
    return 0


if __name__ == "__main__":
    sys.exit(main())