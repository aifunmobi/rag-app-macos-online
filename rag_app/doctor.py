"""First-run / self-heal setup for the RAG app.

Orchestrates Ollama installation, server startup, embedder pull, and
chat-model selection. All calls into sibling modules are module-qualified
so tests can monkeypatch them cleanly.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

import rag_app.config as config
import rag_app.ollama_client as ollama_client

# Windows subprocess flags
_DETACHED_PROCESS = 0x00000008
_CREATE_NO_WINDOW = 0x08000000


def _ollama_exe() -> str | None:
    """Locate the ollama executable, even if PATH hasn't refreshed post-install.

    After a fresh install, the new binary might not be on the *current*
    process's PATH, so ``shutil.which`` returns None. Fall back to bundled and
    well-known install locations.
    """
    explicit = os.environ.get("RAG_OLLAMA_EXE")
    if explicit:
        explicit_path = Path(explicit)
        try:
            if explicit_path.exists():
                return str(explicit_path)
        except OSError:
            pass

    found = shutil.which("ollama")
    if found:
        return found

    system = platform.system()
    candidates: list[Path] = []
    if system == "Windows":
        candidates.append(config.PROJECT_ROOT / "vendor" / "ollama" / "ollama.exe")
        local = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        if local:
            candidates.append(Path(local) / "Programs" / "Ollama" / "ollama.exe")
        candidates.append(Path(program_files) / "Ollama" / "ollama.exe")
    elif system == "Darwin":
        candidates += [
            config.PROJECT_ROOT / "vendor" / "ollama" / "ollama",
            Path("/opt/homebrew/bin/ollama"),
            Path("/usr/local/bin/ollama"),
            Path("/Applications/Ollama.app/Contents/Resources/ollama"),
        ]
    else:
        candidates += [Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")]

    for c in candidates:
        try:
            if c.exists():
                return str(c)
        except OSError:
            continue
    return None

# -------------------------------------------------------------------------
# status
# -------------------------------------------------------------------------

def status() -> dict:
    """Return a snapshot of the current setup state.

    Keys:
        ollama_installed  – bool, ``shutil.which("ollama") is not None``
        ollama_running    – bool, Ollama HTTP health check
        embedder_ready    – bool, EMBED_MODEL present in running instance
        chat_models       – list[str], non-embedder model names (empty when not running)
        chat_model        – str, currently configured chat model
    """
    installed = _ollama_exe() is not None
    running = ollama_client.is_running()

    names: list[str] = []
    if running:
        try:
            names = ollama_client.list_model_names()
        except ollama_client.OllamaError:
            names = []

    embed_prefix = config.EMBED_MODEL
    embedder_ready = any(n.startswith(embed_prefix) for n in names)
    chat_models = [n for n in names if not n.startswith(embed_prefix)]

    return {
        "ollama_installed": installed,
        "ollama_running": running,
        "embedder_ready": embedder_ready,
        "chat_models": chat_models,
        "chat_model": config.get_chat_model(),
    }


# -------------------------------------------------------------------------
# ensure_ollama_installed
# -------------------------------------------------------------------------

def _confirm(prompt: str) -> bool:
    """Y/n prompt; blank or yes -> True."""
    return input(f"{prompt}  [Y/n] ").strip().lower() in ("", "y", "yes")


def _install_ollama_windows() -> bool:
    """Download and extract the official standalone Windows Ollama zip."""
    import tempfile
    import urllib.request
    import zipfile

    url = "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip"
    dest = Path(tempfile.gettempdir()) / "ollama-windows-amd64.zip"
    install_dir = config.PROJECT_ROOT / "vendor" / "ollama"

    print("Downloading the official standalone Ollama zip...")
    try:
        urllib.request.urlretrieve(url, dest)
    except Exception as exc:  # noqa: BLE001
        print(f"  could not download Ollama: {exc}")
        return False

    print("Extracting Ollama...")
    try:
        install_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest) as zf:
            zf.extractall(install_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"  could not extract Ollama: {exc}")
        return False

    return _ollama_exe() is not None


def _install_ollama_macos() -> bool:
    """Download and extract the official standalone macOS Ollama tarball."""
    import tempfile
    import urllib.request

    url = "https://github.com/ollama/ollama/releases/latest/download/ollama-darwin.tgz"
    dest = Path(tempfile.gettempdir()) / "ollama-darwin.tgz"
    install_dir = config.PROJECT_ROOT / "vendor" / "ollama"

    print("Downloading the official standalone Ollama macOS package...")
    try:
        urllib.request.urlretrieve(url, dest)
    except Exception as exc:  # noqa: BLE001
        print(f"  could not download Ollama: {exc}")
        return False

    print("Extracting Ollama...")
    try:
        install_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["tar", "-xzf", str(dest), "-C", str(install_dir)], check=True)
        exe = install_dir / "ollama"
        if exe.exists():
            exe.chmod(0o755)
    except Exception as exc:  # noqa: BLE001
        print(f"  could not extract Ollama: {exc}")
        return False

    return _ollama_exe() is not None


def ensure_ollama_installed(interactive: bool = True, auto: bool = False) -> bool:
    """Ensure Ollama is installed, installing it when permitted.

    *auto*  – fully unattended: install without prompting (used by the Windows
              one-click setup and any ``--auto`` launch).
    *interactive* (and not *auto*) – prompt before installing.

    Returns True when Ollama is available (or its server is reachable).
    """
    if _ollama_exe() is not None:
        return True

    system = platform.system()

    if system == "Windows":
        # Factory-Windows friendly: download + unzip standalone Ollama.
        if not auto and not (interactive and _confirm(
            "Install Ollama now? (downloads the official standalone zip)"
        )):
            print("Ollama is required. Get it from https://ollama.com/download")
            return False
        return _install_ollama_windows()

    if system == "Darwin":
        if not auto and not (interactive and _confirm(
            "Install Ollama now? (downloads the official standalone package)"
        )):
            print("Skipping Ollama installation.")
            return False
        return _install_ollama_macos()

    # Linux / other
    print("Ollama is not installed. Install it from https://ollama.com/download")
    return False


# -------------------------------------------------------------------------
# ensure_ollama_running
# -------------------------------------------------------------------------

def ensure_ollama_running() -> bool:
    """Start ``ollama serve`` in the background if it is not already running.

    Polls for up to ~20 seconds; returns the final running state.
    """
    if ollama_client.is_running():
        return True

    print("Starting Ollama server (ollama serve)…")

    kwargs: dict = {}
    if platform.system() == "Windows":
        kwargs["creationflags"] = _DETACHED_PROCESS | _CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True

    exe = _ollama_exe() or "ollama"
    try:
        subprocess.Popen([exe, "serve"], **kwargs)
    except FileNotFoundError:
        print("ollama binary not found – cannot start server.")
        return False

    deadline = time.time() + 20.0
    while time.time() < deadline:
        time.sleep(1.0)
        if ollama_client.is_running():
            print("Ollama server is up.")
            return True

    print("Timed out waiting for Ollama to start.")
    return ollama_client.is_running()


# -------------------------------------------------------------------------
# ensure_embedder
# -------------------------------------------------------------------------

def ensure_embedder(progress=None) -> None:
    """Pull the embedding model if it is not already present."""
    try:
        names = ollama_client.list_model_names()
    except ollama_client.OllamaError:
        names = []

    if any(n.startswith(config.EMBED_MODEL) for n in names):
        return

    print(f"Pulling embedding model {config.EMBED_MODEL!r} ...")
    ollama_client.pull_model(config.EMBED_MODEL, progress=progress or _pull_progress)
    if progress is None:
        print()
    print(f"Embedder {config.EMBED_MODEL!r} ready.")


# -------------------------------------------------------------------------
# ensure_chat_model
# -------------------------------------------------------------------------

def ensure_chat_model(interactive: bool = True) -> str:
    """Select and persist a chat model, pulling one if necessary.

    Returns the chosen model tag.
    """
    try:
        names = ollama_client.list_model_names()
    except ollama_client.OllamaError:
        names = []

    chat_models = [n for n in names if not n.startswith(config.EMBED_MODEL)]

    if chat_models:
        # Models already present – pick one.
        current = config.get_chat_model()
        default = current if current in chat_models else chat_models[0]

        if not interactive:
            config.set_chat_model(default)
            return default

        print("\nAvailable chat models:")
        for i, m in enumerate(chat_models, 1):
            marker = " (default)" if m == default else ""
            print(f"  {i}. {m}{marker}")

        raw = input(f"Choose a model [default: {default}]: ").strip()
        if raw == "":
            choice = default
        else:
            try:
                idx = int(raw) - 1
                choice = chat_models[idx]
            except (ValueError, IndexError):
                print(f"Invalid selection, using default: {default}")
                choice = default

        config.set_chat_model(choice)
        return choice

    # No chat models installed – use the NEWBIE_LADDER.
    ladder = config.NEWBIE_LADDER  # [(tier, tag, approx), …]
    # Default to the recommended model (gemma3:4b — best small dense answer quality).
    default_tag = config.DEFAULT_CHAT_MODEL

    if not interactive:
        print(f"No chat models found. Pulling default model {default_tag!r} ...")
        ollama_client.pull_model(default_tag, progress=_pull_progress)
        print()
        config.set_chat_model(default_tag)
        return default_tag

    print("\nNo chat models installed. Choose one to download:")
    for i, (tier, tag, approx) in enumerate(ladder, 1):
        marker = " (default)" if tag == default_tag else ""
        print(f"  {i}. [{tier}] {tag}  {approx}{marker}")

    raw = input(f"Choose a model [default: {default_tag}]: ").strip()
    if raw == "":
        choice_tag = default_tag
    else:
        try:
            idx = int(raw) - 1
            choice_tag = ladder[idx][1]
        except (ValueError, IndexError):
            print(f"Invalid selection, using default: {default_tag}")
            choice_tag = default_tag

    def _progress(status: str, completed: int, total: int) -> None:
        if total:
            pct = int(completed / total * 100)
            print(f"\r  {status} {pct}%", end="", flush=True)
        else:
            print(f"\r  {status}", end="", flush=True)

    print(f"Pulling {choice_tag!r} …")
    ollama_client.pull_model(choice_tag, progress=_progress)
    print()
    config.set_chat_model(choice_tag)
    return choice_tag


def _pull_progress(status: str, completed: int, total: int) -> None:
    """Single-line Ollama pull progress for unattended setup."""
    if total:
        pct = int(completed / total * 100)
        print(f"\r  {status} {pct}%", end="", flush=True)
    elif status:
        print(f"\r  {status}", end="", flush=True)


# -------------------------------------------------------------------------
# run_first_run_setup
# -------------------------------------------------------------------------

def run_first_run_setup(interactive: bool = True, auto: bool = False) -> dict:
    """Orchestrate full first-run setup.

    *auto* runs the whole flow unattended (install Ollama silently, auto-pick the
    default chat model) — used by the one-click / ``--auto`` launch.

    Returns:
        {"ok": True,  "chat_model": str, "status": status()}  on success
        {"ok": False, "reason": str}                           on failure
    """
    print("=== RAG first-run setup ===")

    # 1. Install Ollama
    if not ensure_ollama_installed(interactive=interactive, auto=auto):
        return {"ok": False, "reason": "Ollama is not installed and could not be installed automatically."}

    # 2. Start Ollama
    if not ensure_ollama_running():
        return {"ok": False, "reason": "Ollama is installed but the server could not be started."}

    try:
        # 3. Pull embedder
        ensure_embedder()

        # 4. Select / pull chat model (auto -> non-interactive default pick)
        chat_model = ensure_chat_model(interactive=interactive and not auto)
    except ollama_client.OllamaError as exc:
        return {"ok": False, "reason": f"Model setup failed: {exc}"}

    print("\n=== Setup complete ===")
    return {"ok": True, "chat_model": chat_model, "status": status()}
