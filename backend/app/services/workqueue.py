"""One background worker thread per queue.

Used for work that follows every backup (cloud uploads), so a scheduled run of
many devices does not start one thread and one database connection per backup.
"""
import logging
import queue
import threading

logger = logging.getLogger("ncm.workqueue")


class SerialWorker:
    """Runs submitted calls one at a time, in order, on a single daemon thread."""

    def __init__(self, name: str):
        self.name = name
        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def submit(self, fn, *args):
        self._q.put((fn, args))
        with self._lock:
            if not self._thread or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name=self.name, daemon=True)
                self._thread.start()

    def size(self) -> int:
        return self._q.qsize()

    def join(self):
        """Wait until everything submitted so far has run (used by tests)."""
        self._q.join()

    def _loop(self):
        while True:
            fn, args = self._q.get()
            try:
                fn(*args)
            except Exception:
                logger.exception("%s: background job failed", self.name)
            finally:
                self._q.task_done()
