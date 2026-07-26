"""Pure layout state and geometry helpers (no GTK dependencies)."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Pending:
    """Desired state for one output, edited by the UI."""
    enabled: bool = True
    width: int = 1920
    height: int = 1080
    refresh_mhz: int = 60000
    scale: float = 1.0
    transform: str = "normal"
    vrr: str = "off"
    focus: bool = False
    x: int = 0
    y: int = 0

    def mode_string(self) -> str:
        return f"{self.width}x{self.height}@{self.refresh_mhz / 1000:.3f}"

    def logical_size(self) -> tuple[int, int]:
        w, h = self.width, self.height
        if self.transform in ("90", "270", "flipped-90", "flipped-270"):
            w, h = h, w
        # niri places outputs in fractional logical space (1440/1.3 = 1107.69),
        # so adjacent positions must clear the ceiling: a monitor put at
        # y=1107 below that one is rejected as overlapping, y=1108 works.
        # (The IPC "logical" size reports the floor - do not copy that.)
        return max(1, math.ceil(w / self.scale)), max(1, math.ceil(h / self.scale))


def reflow_after_resize(pending: dict[str, Pending], name: str,
                        old_w: int, old_h: int) -> None:
    """Shift neighbours after ``name``'s logical size changed.

    Monitors at or beyond the old right edge move by the width delta (and
    likewise below the old bottom edge), so flush layouts stay flush and
    growing a monitor never overlaps its neighbours.
    """
    p = pending[name]
    new_w, new_h = p.logical_size()
    dw, dh = new_w - old_w, new_h - old_h
    if not dw and not dh:
        return
    for other, q in pending.items():
        if other == name:
            continue
        if dw and q.x >= p.x + old_w:
            q.x += dw
        if dh and q.y >= p.y + old_h:
            q.y += dh


def normalize_positions(pending: dict[str, Pending]) -> None:
    """Shift all monitors so the top-left of the enabled bounds is (0, 0)."""
    enabled = [p for p in pending.values() if p.enabled]
    if not enabled:
        return
    min_x = min(p.x for p in enabled)
    min_y = min(p.y for p in enabled)
    if (min_x, min_y) == (0, 0):
        return
    for p in pending.values():
        p.x -= min_x
        p.y -= min_y
