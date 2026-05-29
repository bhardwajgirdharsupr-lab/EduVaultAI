import os
import sys
from pathlib import Path
from wsgiref.simple_server import make_server

from app import app


BASE_DIR = Path(__file__).resolve().parent
log_file = (BASE_DIR / "app-running.log").open("a", encoding="utf-8", buffering=1)
sys.stdout = log_file
sys.stderr = log_file


if __name__ == "__main__":
    host = "0.0.0.0"
    port = int(os.environ.get("PORT", "5055"))
    print(f"Serving EduVault on http://127.0.0.1:{port}", flush=True)
    with make_server(host, port, app) as server:
        server.serve_forever()
