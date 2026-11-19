import subprocess
import sys
import time
import urllib.request
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
FRONTEND_DIR = ROOT_DIR / "frontend"


def wait_for_url(url: str, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                if resp.status < 500:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def main() -> int:
    env_backend = os.environ.copy()
    env_backend["OIDC_ENABLED"] = "false"
    env_backend["ENVIRONMENT"] = "test"

    env_frontend = os.environ.copy()
    env_frontend["NEXT_PUBLIC_BACKEND_ORIGIN"] = "http://127.0.0.1:8000"

    print(f"[E2E Runner] Starting FastAPI Backend in {BACKEND_DIR} on http://127.0.0.1:8000 ...")
    backend_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=str(BACKEND_DIR),
        env=env_backend,
    )

    print(f"[E2E Runner] Starting Next.js Frontend in {FRONTEND_DIR} on http://127.0.0.1:3000 ...")
    npx_cmd = "npx.cmd" if os.name == "nt" else "npx"
    frontend_proc = subprocess.Popen(
        [npx_cmd, "next", "start", "-H", "127.0.0.1", "-p", "3000"],
        cwd=str(FRONTEND_DIR),
        env=env_frontend,
    )

    try:
        if not wait_for_url("http://127.0.0.1:8000/api/v1/openapi.json", timeout=60.0):
            print("[E2E Runner] ERROR: Backend did not become ready within 60s.")
            return 1
        print("[E2E Runner] Backend ready!")

        if not wait_for_url("http://127.0.0.1:3000", timeout=60.0):
            print("[E2E Runner] ERROR: Frontend did not become ready within 60s.")
            return 1
        print("[E2E Runner] Frontend ready! Running Playwright E2E test...")

        res = subprocess.run(
            [npx_cmd, "playwright", "test"],
            cwd=str(FRONTEND_DIR),
            env=env_frontend,
        )
        return res.returncode
    finally:
        print("[E2E Runner] Stopping servers...")
        backend_proc.terminate()
        frontend_proc.terminate()
        try:
            backend_proc.wait(timeout=5)
        except Exception:
            backend_proc.kill()
        try:
            frontend_proc.wait(timeout=5)
        except Exception:
            frontend_proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
