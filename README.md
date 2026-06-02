# RAG Chat for macOS

Local document chat for macOS. The app installs its own Python runtime with
`uv`, downloads the standalone Ollama runtime, pulls the required local models,
indexes files from `input/`, and opens a browser-based chat UI.

`online` in this repo name means the installer downloads everything it needs
from the internet on first run. It does not mean your documents or chat run in
the cloud.

Everything runs locally after first setup. Your documents and chat index stay in
the app folder.

## Install

Open Terminal and run:

```bash
curl -fsSL https://raw.githubusercontent.com/aifunmobi/rag-app-macos-online/main/install.sh | bash
```

The installer creates a normal folder at `~/Applications/rag-app`, fixes local
macOS permissions, downloads the runtime dependencies, and starts the app. Do
not use GitHub's "Download ZIP" button for first install; the curl command is
the supported path.

To install somewhere else:

```bash
curl -fsSL https://raw.githubusercontent.com/aifunmobi/rag-app-macos-online/main/install.sh | RAG_APP_HOME="$HOME/Desktop/rag-app" bash
```

To install without immediately starting setup:

```bash
curl -fsSL https://raw.githubusercontent.com/aifunmobi/rag-app-macos-online/main/install.sh | RAG_APP_NO_RUN=1 bash
```

## Run After Install

Double-click:

```text
RAG Chat Setup.app
```

or run:

```bash
bash ~/Applications/rag-app/setup-mac.command
```

The first launch is large because it downloads Python, Ollama, Python packages,
and local LLM model files. Later launches reuse those downloads.

If your browser does not open automatically after setup finishes, open a browser
and enter:

```text
http://127.0.0.1:8000
```

If port `8000` is already in use, the setup window prints the alternate local
URL to open.

## What It Installs

The setup script:

1. Confirms it is running on macOS.
2. Downloads `uv` into `.tools/uv` if missing.
3. Uses `uv` to install Python 3.12 if missing.
4. Installs locked Python dependencies from `uv.lock`.
5. Downloads the official standalone Ollama macOS package into `vendor/ollama`.
6. Starts Ollama on `127.0.0.1:11435`.
7. Pulls `nomic-embed-text` and `gemma3:4b` into `vendor/models`.
8. Starts the RAG web app and opens the browser.

## Use

Drop documents into `input/`, then ask questions in the browser. Supported
documents include PDFs, Word documents, text, Markdown, HTML, CSS, JavaScript,
TypeScript, Python, and other source files.

The app indexes files automatically while it is running. Keep the Terminal
window open while using it. Press `Ctrl+C` in that window to stop the server.

If the browser window closes, reopen `http://127.0.0.1:8000` while the server is
still running.

## Gatekeeper Note

`RAG Chat Setup.app` is a native Swift/AppKit helper app and is ad-hoc signed,
but it is not Apple Developer ID notarized. The curl installer is the preferred
first-run path because it starts setup from Terminal instead of asking Finder to
open a downloaded unsigned app.

If macOS blocks a manually downloaded copy, run:

```bash
xattr -d -r com.apple.quarantine /path/to/rag-app
bash /path/to/rag-app/setup-mac.command
```

## Local Data

| Path | Purpose |
|---|---|
| `.tools/uv` | Downloaded uv executable |
| `.venv/` | Python dependency environment |
| `vendor/ollama` | Downloaded standalone Ollama |
| `vendor/models` | Downloaded Ollama model storage |
| `input/` | Documents to index |
| `data/rag.db` | Local SQLite index |
| `data/config.json` | Selected model and app settings |

## Retrieval Settings

| Setting | Value |
|---|---|
| Embedding model | `nomic-embed-text` |
| Ollama API | `/api/embed` |
| Embed options | `{"num_ctx": 2048}` |
| Document prefix | `search_document: ` |
| Query prefix | `search_query: ` |
| Chunk size | `1024` tokens |
| Chunk overlap | `160` tokens |
| Retrieved chunks | `8` |

## Uninstall

Stop the app, then remove the install folder:

```bash
rm -rf ~/Applications/rag-app
```

If you installed somewhere else, remove that folder instead.

## Development

Rebuild the native setup wrapper:

```bash
./macos-installer/build-setup-app.command
```

Run the Python app directly through the installer-managed environment:

```bash
./setup-mac.command
```
