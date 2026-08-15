"""PDH-PKG 启动器：设置数据目录、启动后端、打开桌面窗口。"""
from __future__ import annotations

import os
import logging
import sys
import threading
import time
import traceback
from pathlib import Path

if getattr(sys, "frozen", False):
    APP_ROOT = Path(sys.executable).parent
else:
    APP_ROOT = Path(__file__).resolve().parent.parent

DATA_ROOT = Path(
    os.environ.get("PDH_PKG_DATA", Path(os.environ.get("LOCALAPPDATA", APP_ROOT)) / "PDH-PKG")
)
DATA_ROOT.mkdir(parents=True, exist_ok=True)
LOG_FILE = DATA_ROOT / "run.log"
_log_stream = open(LOG_FILE, "a", encoding="utf-8")
if sys.stdout is None:
    sys.stdout = _log_stream
if sys.stderr is None:
    sys.stderr = _log_stream
logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    encoding="utf-8",
)

os.environ["DATA_DIR"] = str(DATA_ROOT)
os.environ["KB_DATA_DIR"] = str(DATA_ROOT)
os.chdir(APP_ROOT)
if "--debug" in sys.argv:
    os.environ["PDH_PKG_DEBUG"] = "1"

import urllib.request  # noqa: E402
from uvicorn import Config, Server  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402


def _wait_health(timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen("http://127.0.0.1:8001/health", timeout=2).read()
            return True
        except Exception:
            time.sleep(0.5)
    return False


server = Server(
    Config(fastapi_app, host="127.0.0.1", port=8001, log_level="info")
)


def _run_server():
    server.run()


def main():
    try:
        threading.Thread(target=_run_server, daemon=True).start()
        _wait_health()

        if os.environ.get("PDH_PKG_NO_WINDOW") == "1":
            while True:
                time.sleep(3600)

        import webview
        webview.create_window(
            "PDH-PKG",
            "http://127.0.0.1:8001",
            width=1280,
            height=820,
            min_size=(960, 640),
        )
        webview.start()
        server.should_exit = True
        time.sleep(1)
    except Exception:
        logging.exception("PDH-PKG 启动失败")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            traceback.print_exc(file=f)
        raise


if __name__ == "__main__":
    main()
