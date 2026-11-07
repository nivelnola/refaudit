"""A plain progress bar for the terminal. Logs and pipes get simple lines instead."""
from __future__ import annotations

import shutil
import sys

BAR_WIDTH = 24


def render_bar(done: int, total: int) -> str:
    filled = BAR_WIDTH if total <= 0 else min(BAR_WIDTH, BAR_WIDTH * done // total)
    return "#" * filled + "-" * (BAR_WIDTH - filled)


class Progress:
    def __init__(self, label: str, total: int, unit: str = "references", stream=None):
        self.label, self.total, self.unit = label, total, unit
        self.stream = stream or sys.stdout
        self.live = self.stream.isatty()
        self.done = 0
        if self.live:
            self.update(0)
        else:
            self._print(f"{label}: {total} {unit}...")

    def _print(self, line: str) -> None:
        self.stream.write(line + "\n")
        self.stream.flush()

    def update(self, done: int, detail: str = "") -> None:
        self.done = done
        if not self.live:
            return
        width = max(20, shutil.get_terminal_size().columns - 1)
        line = f"{self.label} [{render_bar(done, self.total)}] {done}/{self.total} {detail}"
        self.stream.write("\r" + line[:width].ljust(width))
        self.stream.flush()

    def note(self, message: str) -> None:
        """Something worth saying mid-step, such as a rate-limit wait."""
        if self.live:
            self.update(self.done, message)
        else:
            self._print(f"{self.label}: {message}")

    def finish(self, message: str = "done") -> None:
        if self.live:
            width = max(20, shutil.get_terminal_size().columns - 1)
            self.stream.write("\r" + " " * width + "\r")
        self._print(f"{self.label}: {message}")
