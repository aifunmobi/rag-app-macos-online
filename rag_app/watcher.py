"""Filesystem watcher that re-ingests input_dir on changes.

Uses watchdog to observe the input directory and debounces rapid filesystem
events into a single ingest_directory call.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

import rag_app.ingest as ingest
from rag_app.config import INPUT_DIR
from rag_app.loaders import is_supported
from rag_app.store import Store


class _ChangeHandler(FileSystemEventHandler):
    """Watchdog handler that notifies the InputWatcher on relevant events."""

    def __init__(self, watcher: InputWatcher) -> None:
        super().__init__()
        self._watcher = watcher

    def _relevant(self, event: FileSystemEvent) -> bool:
        """Return True for any delete event or supported-file event."""
        if event.is_directory:
            return False
        # Deletes are always relevant (file gone -> must remove from store).
        event_type: str = event.event_type  # 'created','modified','moved','deleted'
        if event_type == "deleted":
            return True
        # For create/modify/move, check the destination path.
        path_str: str = getattr(event, "dest_path", None) or event.src_path
        return is_supported(Path(path_str))

    def on_any_event(self, event: FileSystemEvent) -> None:  # type: ignore[override]
        if self._relevant(event):
            self._watcher.notify()


class InputWatcher:
    """Watch *input_dir* and debounce filesystem events into ingest calls.

    Parameters
    ----------
    store:
        The Store instance to pass to ingest_directory.
    input_dir:
        Directory to watch (recursive).
    debounce:
        Seconds to wait after the last event before calling _flush.
    """

    def __init__(
        self,
        store: Store,
        input_dir: Path = INPUT_DIR,
        debounce: float = 1.0,
        progress: Callable[[int, int, str], None] | None = None,
        on_start: Callable[[], None] | None = None,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        self.store = store
        self.input_dir = Path(input_dir)
        self.debounce = debounce
        self.progress = progress
        self.on_start = on_start
        self.on_done = on_done

        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._observer: Observer | None = None
        self._handler = _ChangeHandler(self)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the watchdog Observer watching *input_dir* recursively."""
        self.input_dir.mkdir(parents=True, exist_ok=True)
        observer = Observer()
        observer.schedule(self._handler, str(self.input_dir), recursive=True)
        observer.start()
        self._observer = observer

    def stop(self) -> None:
        """Stop the observer and cancel any pending debounce timer."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None

    def notify(self) -> None:
        """Schedule (or reschedule) a debounced flush.

        Calling this directly is safe from any thread and is the primary
        hook for unit tests that want to bypass real filesystem timing.
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce, self._flush)
            self._timer.daemon = True
            self._timer.start()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _flush(self) -> None:
        """Call ingest_directory and swallow any exception so the watcher survives."""
        with self._lock:
            self._timer = None
        if self.on_start is not None:
            try:
                self.on_start()
            except Exception:  # noqa: BLE001
                pass
        try:
            ingest.ingest_directory(self.store, self.input_dir, self.progress)
        except Exception as exc:  # noqa: BLE001
            print(f"[watcher] ingest error: {exc}")
        finally:
            if self.on_done is not None:
                try:
                    self.on_done()
                except Exception:  # noqa: BLE001
                    pass
