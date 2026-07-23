from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock


class InMemoryRunLease:
    """保证同一个 Run 同一时刻只有一个执行者。"""

    def __init__(self) -> None:
        self._active: set[str] = set()
        self._lock = RLock()

    @contextmanager
    def acquire(self, run_id: str) -> Iterator[None]:
        with self._lock:
            if run_id in self._active:
                raise RuntimeError(f"RUN_LEASE_CONFLICT: {run_id}")
            self._active.add(run_id)
        try:
            yield
        finally:
            with self._lock:
                self._active.discard(run_id)
