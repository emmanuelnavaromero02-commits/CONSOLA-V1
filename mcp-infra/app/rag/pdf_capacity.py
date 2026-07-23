from __future__ import annotations

import os
import threading
from collections.abc import Mapping


PDF_MAX_WORKERS_ENV = "MCP_INFRA_PDF_MAX_WORKERS"
DEFAULT_PDF_MAX_WORKERS = 2
MIN_PDF_MAX_WORKERS = 1
MAX_PDF_MAX_WORKERS = 4


class PdfCapacityExhausted(RuntimeError):
    pass


class PdfWorkerLease:
    def __init__(self, semaphore: threading.BoundedSemaphore) -> None:
        self._semaphore = semaphore
        self._lock = threading.Lock()
        self._started = False
        self._released = False

    def start(self) -> bool:
        with self._lock:
            if self._started or self._released:
                return False
            self._started = True
            return True

    def release_if_not_started(self) -> None:
        with self._lock:
            if self._started or self._released:
                return
            self._released = True
        self._semaphore.release()

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self._semaphore.release()


def configured_max_workers(
    environ: Mapping[str, str] | None = None,
) -> int:
    source = os.environ if environ is None else environ
    raw_value = source.get(PDF_MAX_WORKERS_ENV, str(DEFAULT_PDF_MAX_WORKERS))
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"{PDF_MAX_WORKERS_ENV} must be an integer between "
            f"{MIN_PDF_MAX_WORKERS} and {MAX_PDF_MAX_WORKERS}"
        ) from exc
    if not MIN_PDF_MAX_WORKERS <= value <= MAX_PDF_MAX_WORKERS:
        raise RuntimeError(
            f"{PDF_MAX_WORKERS_ENV} must be between "
            f"{MIN_PDF_MAX_WORKERS} and {MAX_PDF_MAX_WORKERS}"
        )
    return value


class PdfWorkerCapacity:
    def __init__(self, max_workers: int) -> None:
        if not MIN_PDF_MAX_WORKERS <= max_workers <= MAX_PDF_MAX_WORKERS:
            raise ValueError("PDF worker capacity must be between 1 and 4")
        self.max_workers = max_workers
        self._semaphore = threading.BoundedSemaphore(max_workers)

    def acquire(self) -> PdfWorkerLease:
        if not self._semaphore.acquire(blocking=False):
            raise PdfCapacityExhausted
        return PdfWorkerLease(self._semaphore)


PDF_WORKER_CAPACITY = PdfWorkerCapacity(configured_max_workers())


def acquire_pdf_worker_lease() -> PdfWorkerLease:
    return PDF_WORKER_CAPACITY.acquire()
