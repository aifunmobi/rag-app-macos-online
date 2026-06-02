#!/usr/bin/env bash
set -Eeuo pipefail

DEFAULT_REPO_URL="https://github.com/aifunmobi/rag-app-macos-online.git"
REPO_URL="${RAG_APP_REPO:-$DEFAULT_REPO_URL}"
BRANCH="${RAG_APP_BRANCH:-main}"
INSTALL_DIR="${RAG_APP_HOME:-$HOME/Applications/rag-app}"
NO_RUN="${RAG_APP_NO_RUN:-0}"
APP_DIR=""

fail() {
  printf '\nERROR: %s\n' "$1" >&2
  exit 1
}

notice() {
  printf '\n==> %s\n' "$1"
}

github_archive_url() {
  local url="$1"
  local branch="$2"
  local slug

  slug="${url#https://github.com/}"
  slug="${slug#git@github.com:}"
  slug="${slug%.git}"

  if [[ "$slug" == "$url" || "$slug" != */* ]]; then
    return 1
  fi

  printf 'https://github.com/%s/archive/refs/heads/%s.tar.gz\n' "$slug" "$branch"
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  fail "This installer is for macOS."
fi

notice "Installing RAG Chat"
printf 'Repo:    %s\n' "$REPO_URL"
printf 'Branch:  %s\n' "$BRANCH"
printf 'Folder:  %s\n' "$INSTALL_DIR"

mkdir -p "$(dirname "$INSTALL_DIR")"

if command -v git >/dev/null 2>&1; then
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    notice "Updating existing checkout"
    git -C "$INSTALL_DIR" fetch --depth 1 origin "$BRANCH"
    git -C "$INSTALL_DIR" checkout "$BRANCH"
    git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH"
  else
    if [[ -e "$INSTALL_DIR" ]]; then
      fail "$INSTALL_DIR already exists and is not a git checkout. Move it or set RAG_APP_HOME to a different folder."
    fi
    notice "Cloning project"
    git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
  fi
else
  ARCHIVE_URL="${RAG_APP_ARCHIVE_URL:-$(github_archive_url "$REPO_URL" "$BRANCH" || true)}"
  [[ -n "$ARCHIVE_URL" ]] || fail "git is not installed and the repo URL is not a standard GitHub URL. Set RAG_APP_ARCHIVE_URL."
  [[ ! -e "$INSTALL_DIR" ]] || fail "$INSTALL_DIR already exists. Install git to update it, or move the folder first."

  notice "Downloading project archive"
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' EXIT
  curl --fail --location --show-error "$ARCHIVE_URL" --output "$tmpdir/rag-app.tgz"
  tar -xzf "$tmpdir/rag-app.tgz" -C "$tmpdir"
  srcdir="$(find "$tmpdir" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
  [[ -n "$srcdir" ]] || fail "Downloaded archive did not contain a project folder."
  mv "$srcdir" "$INSTALL_DIR"
fi

notice "Preparing local files"
xattr -d -r com.apple.quarantine "$INSTALL_DIR" >/dev/null 2>&1 || true

if [[ -f "$INSTALL_DIR/setup-mac.command" ]]; then
  APP_DIR="$INSTALL_DIR"
elif [[ -f "$INSTALL_DIR/rag-app/setup-mac.command" ]]; then
  APP_DIR="$INSTALL_DIR/rag-app"
else
  fail "Could not find setup-mac.command in $INSTALL_DIR or $INSTALL_DIR/rag-app."
fi

chmod +x \
  "$APP_DIR/setup-mac.command" \
  "$APP_DIR/Start RAG (online).command" \
  "$APP_DIR/run.sh" \
  "$APP_DIR/macos-installer/build-setup-app.command" \
  >/dev/null 2>&1 || true

if [[ -x "$APP_DIR/RAG Chat Setup.app/Contents/MacOS/RAG Chat Setup" ]]; then
  chmod +x "$APP_DIR/RAG Chat Setup.app/Contents/MacOS/RAG Chat Setup" >/dev/null 2>&1 || true
fi

notice "Installed"
printf 'RAG Chat is installed at:\n  %s\n' "$INSTALL_DIR"
if [[ "$APP_DIR" != "$INSTALL_DIR" ]]; then
  printf 'App folder:\n  %s\n' "$APP_DIR"
fi

if [[ "$NO_RUN" == "1" ]]; then
  printf '\nRun it later with:\n  bash "%s/setup-mac.command"\n' "$APP_DIR"
  exit 0
fi

notice "Starting first-run setup"
exec bash "$APP_DIR/setup-mac.command"
