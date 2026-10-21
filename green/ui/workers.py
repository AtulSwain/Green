"""Running blocking work off the GUI thread.

Results are delivered back to the GUI thread through queued Qt signals connected
to a QObject that lives on the GUI thread, so callbacks may safely update widgets.
"""

from __future__ import annotations

import itertools
import logging
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal

log = logging.getLogger(__name__)


class _Signals(QObject):
    finished = Signal(int, object)
    failed = Signal(int, object)
    progress = Signal(int, object)


class _Task(QRunnable):
    def __init__(self, task_id: int, fn: Callable[..., Any], signals: _Signals, pass_progress: bool):
        super().__init__()
        self.setAutoDelete(True)
        self.task_id = task_id
        self.fn = fn
        self.signals = signals
        self.pass_progress = pass_progress

    def run(self) -> None:
        try:
            if self.pass_progress:
                result = self.fn(lambda payload: self.signals.progress.emit(self.task_id, payload))
            else:
                result = self.fn()
        except BaseException as exc:  # report everything; never kill the pool thread silently
            if not isinstance(exc, Exception):
                log.error("Background task aborted: %s", exc.__class__.__name__)
            self.signals.failed.emit(self.task_id, exc)
        else:
            self.signals.finished.emit(self.task_id, result)


class TaskRunner(QObject):
    """Submit callables to a thread pool and receive results on the GUI thread."""

    def __init__(self, parent: QObject | None = None, max_threads: int = 4):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max_threads)
        self._ids = itertools.count(1)
        self._callbacks: dict[int, tuple[Callable | None, Callable | None, Callable | None]] = {}
        self._lock = threading.Lock()
        self._closing = False
        self._signals = _Signals()
        self._signals.finished.connect(self._on_finished, Qt.ConnectionType.QueuedConnection)
        self._signals.failed.connect(self._on_failed, Qt.ConnectionType.QueuedConnection)
        self._signals.progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)

    def submit(
        self,
        fn: Callable[..., Any],
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        on_progress: Callable[[Any], None] | None = None,
    ) -> int:
        """Run ``fn`` in the pool. If ``on_progress`` is given, ``fn`` receives a ``progress`` callable."""
        if self._closing:
            raise RuntimeError("TaskRunner is shutting down")
        task_id = next(self._ids)
        with self._lock:
            self._callbacks[task_id] = (on_done, on_error, on_progress)
        self.pool.start(_Task(task_id, fn, self._signals, on_progress is not None))
        return task_id

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._callbacks)

    def _pop(self, task_id: int):
        with self._lock:
            return self._callbacks.pop(task_id, (None, None, None))

    def _on_finished(self, task_id: int, result: Any) -> None:
        on_done, _, _ = self._pop(task_id)
        if on_done is not None and not self._closing:
            on_done(result)

    def _on_failed(self, task_id: int, error: Any) -> None:
        _, on_error, _ = self._pop(task_id)
        if self._closing:
            return
        if on_error is not None:
            on_error(error)
        else:
            log.error("Background task failed: %s", error.__class__.__name__)

    def _on_progress(self, task_id: int, payload: Any) -> None:
        with self._lock:
            callbacks = self._callbacks.get(task_id)
        if callbacks and callbacks[2] is not None and not self._closing:
            callbacks[2](payload)

    def shutdown(self, timeout_ms: int = 5000) -> bool:
        self._closing = True
        return self.pool.waitForDone(timeout_ms)
