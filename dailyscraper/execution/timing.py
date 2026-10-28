from __future__ import annotations

import random
import time
from collections.abc import Callable

SLEEP_SLICE_SECONDS = 0.5


class Pacer:
    def __init__(
        self,
        check_interrupted: Callable[[], None] = lambda: None,
        *,
        rng: random.Random | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.check_interrupted = check_interrupted
        self._rng = rng or random.Random()
        self._sleep = sleep

    def delay(self, bounds: tuple[float, float]) -> None:
        self.pause(self._rng.uniform(*bounds))

    def pause(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        self.check_interrupted()
        while (remaining := deadline - time.monotonic()) > 0:
            self._sleep(min(remaining, SLEEP_SLICE_SECONDS))
            self.check_interrupted()
