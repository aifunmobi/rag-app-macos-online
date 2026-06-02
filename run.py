"""Cross-platform launcher for the RAG app.

Usage:
    python run.py [--auto] [--no-setup] [--no-browser]

Flags:
    --auto        Fully unattended setup: install Ollama silently and auto-pick
                  the default chat model, with no prompts.
    --no-setup    Skip the first-run doctor check entirely.
    --no-browser  Do not open a browser tab automatically.
"""
from __future__ import annotations

import socket
import sys
import threading
import webbrowser

# ---------------------------------------------------------------------------
# Simple flag parsing — no argparse dependency required
# ---------------------------------------------------------------------------
_args = sys.argv[1:]
AUTO = "--auto" in _args
NO_SETUP = "--no-setup" in _args
NO_BROWSER = "--no-browser" in _args


def _find_free_port(host: str, preferred: int, tries: int = 20) -> int:
    """Return ``preferred`` if it is bindable, else the next free port.

    Lets the app "just work" even when the default port is already taken by
    another service on the user's machine.
    """
    for port in range(preferred, preferred + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    return preferred  # give up — let uvicorn surface the bind error


def _banner() -> None:
    print()
    print("=" * 54)
    print("  RAG App  —  chat with your own documents")
    print("=" * 54)
    print()


def main() -> None:
    _banner()

    # ------------------------------------------------------------------
    # Step 1 — first-run setup / health check
    # ------------------------------------------------------------------
    if not NO_SETUP:
        from rag_app import doctor

        print("Running first-run setup (pass --no-setup to skip)…")
        result = doctor.run_first_run_setup(interactive=not AUTO, auto=AUTO)
        if not result.get("ok", False):
            reason = result.get("reason") or result.get("error") or "unknown error"
            print(f"\nSetup failed: {reason}")
            print("Fix the issue above and try again.")
            sys.exit(1)
        print()

    # ------------------------------------------------------------------
    # Step 2 — resolve URL, schedule browser open
    # ------------------------------------------------------------------
    from rag_app import config

    port = _find_free_port(config.SERVER_HOST, config.SERVER_PORT)
    if port != config.SERVER_PORT:
        print(f"Port {config.SERVER_PORT} is busy — using {port} instead.")
    url = f"http://{config.SERVER_HOST}:{port}"

    if not NO_BROWSER:
        t = threading.Timer(1.5, lambda: webbrowser.open(url))
        t.daemon = True
        t.start()

    print(f"Open {url}")
    print("Press Ctrl+C to stop the server.\n")

    # ------------------------------------------------------------------
    # Step 3 — run the server (blocks until Ctrl+C)
    # ------------------------------------------------------------------
    import uvicorn

    uvicorn.run(
        "rag_app.app:app",
        host=config.SERVER_HOST,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
