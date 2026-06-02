#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

STEP=1
TOOLS_DIR="$ROOT/.tools"
UV_DIR="$TOOLS_DIR/uv"
UV="$UV_DIR/uv"
VENDOR_DIR="$ROOT/vendor"
OLLAMA_DIR="$VENDOR_DIR/ollama"
OLLAMA="$OLLAMA_DIR/ollama"
MODELS_DIR="$VENDOR_DIR/models"

step() {
  printf '\n[%s] %s\n' "$STEP" "$1"
  STEP=$((STEP + 1))
}

ok() {
  printf '    OK  %s\n' "$1"
}

fail() {
  printf '\nERROR: %s\n' "$1" >&2
  printf '\nPress Return to close this window...' >&2
  read -r _ || true
  exit 1
}

download() {
  local url="$1"
  local dest="$2"
  curl --fail --location --show-error --progress-bar "$url" --output "$dest"
}

ensure_uv() {
  if [[ -x "$UV" ]]; then
    return
  fi

  printf '    Downloading uv into this folder...\n'
  mkdir -p "$UV_DIR"
  curl -LsSf https://astral.sh/uv/install.sh | UV_UNMANAGED_INSTALL="$UV_DIR" sh

  if [[ ! -x "$UV" ]]; then
    fail "uv was downloaded but was not found at $UV"
  fi
}

ensure_ollama() {
  if [[ -x "$OLLAMA" ]]; then
    return
  fi

  printf '    Downloading official Ollama for macOS...\n'
  mkdir -p "$OLLAMA_DIR"
  local archive
  archive="$(mktemp -t ollama-darwin.XXXXXX.tgz)"
  download "https://github.com/ollama/ollama/releases/latest/download/ollama-darwin.tgz" "$archive"
  tar -xzf "$archive" -C "$OLLAMA_DIR"
  rm -f "$archive"
  chmod +x "$OLLAMA"

  if [[ ! -x "$OLLAMA" ]]; then
    fail "Ollama was downloaded but was not found at $OLLAMA"
  fi
}

echo "==============================================="
echo "   RAG Chat - macOS one-click setup"
echo "==============================================="
echo

step "Checking macOS"
if [[ "$(uname -s)" != "Darwin" ]]; then
  fail "This installer is for macOS."
fi
ok "$(sw_vers -productName) $(sw_vers -productVersion)"

step "Installing uv and Python 3.12"
ensure_uv
"$UV" python install 3.12
ok "uv: $("$UV" --version)"

step "Installing Python packages"
"$UV" sync --frozen --python 3.12
ok "Python environment ready"

step "Installing Ollama"
ensure_ollama
ok "$("$OLLAMA" --version)"

step "Preparing isolated app environment"
mkdir -p "$MODELS_DIR" "$ROOT/data" "$ROOT/input"
xattr -d -r com.apple.quarantine "$ROOT" >/dev/null 2>&1 || true
chmod +x "$ROOT/setup-mac.command" "$ROOT/Start RAG (online).command" "$ROOT/run.sh" >/dev/null 2>&1 || true
export RAG_OLLAMA_EXE="$OLLAMA"
export OLLAMA_MODELS="$MODELS_DIR"
export OLLAMA_HOST="127.0.0.1:11435"
export RAG_OLLAMA_HOST="http://127.0.0.1:11435"
export NO_PROXY="127.0.0.1,localhost"
export PATH="$UV_DIR:$OLLAMA_DIR:$PATH"
ok "Ollama models: $MODELS_DIR"
ok "Ollama API: http://127.0.0.1:11435"

step "Launching RAG Chat"
echo "    First launch downloads nomic-embed-text and gemma3:4b."
echo "    The browser opens automatically when the server is ready."
echo "    Leave this window open. Press Ctrl+C to stop the app."
echo

exec "$UV" run --frozen --python 3.12 python run.py --auto "$@"
