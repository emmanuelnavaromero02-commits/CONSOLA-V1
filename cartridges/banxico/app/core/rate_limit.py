from __future__ import annotations

import threading
import time
from collections import deque


class WindowRateLimiter:
    def __init__(self, *, max_calls: int, window_seconds: float) -> None:
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def wait(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= self.window_seconds:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                sleep_for = self.window_seconds - (now - self._calls[0])
            time.sleep(max(sleep_for, 0.01))
