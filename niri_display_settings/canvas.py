"""Drag-to-arrange monitor layout canvas (Gtk.DrawingArea + cairo).

Monitors are laid out in niri's logical coordinate space and scaled to fit
the widget.  Dragging snaps edges to neighbouring monitors; overlapping
placements are shown in red and rejected on drop.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Adw, Gtk, Pango, PangoCairo  # noqa: E402

PADDING = 28
SNAP_CANVAS_PX = 22          # snap threshold, in on-screen pixels
CORNER = 8


@dataclass
class CanvasMonitor:
    name: str
    x: int                   # logical position
    y: int
    width: int               # logical size (already scale/transform adjusted)
    height: int
    enabled: bool = True
    primary: bool = False    # focus-at-startup
    sublabel: str = ""       # e.g. current mode string


class MonitorCanvas(Gtk.DrawingArea):
    def __init__(self) -> None:
        super().__init__()
        self.monitors: list[CanvasMonitor] = []
        self.selected: str | None = None
        self.on_select: Callable[[str], None] | None = None
        self.on_move: Callable[[str, int, int], None] | None = None

        self._drag_name: str | None = None
        self._drag_origin = (0, 0)        # logical pos at drag start
        self._drag_pos = (0.0, 0.0)       # current raw logical pos while dragging
        self._drag_valid = True
        self._last_valid = (0, 0)
        self._drag_scale = 1.0            # canvas scale captured at drag start

        self.set_content_height(280)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self.add_controller(drag)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)

    # --- geometry -------------------------------------------------------------

    def set_monitors(self, monitors: list[CanvasMonitor], selected: str | None = None) -> None:
        self.monitors = monitors
        if selected is not None:
            self.selected = selected
        elif self.selected not in [m.name for m in monitors]:
            self.selected = monitors[0].name if monitors else None
        self.queue_draw()

    def _fit(self) -> tuple[float, float, float]:
        """Return (scale, offset_x, offset_y) mapping logical -> canvas coords."""
        if not self.monitors:
            return 0.1, PADDING, PADDING
        xs = [m.x for m in self.monitors]
        ys = [m.y for m in self.monitors]
        xe = [m.x + m.width for m in self.monitors]
        ye = [m.y + m.height for m in self.monitors]
        if self._drag_name:
            for m in self.monitors:
                if m.name == self._drag_name:
                    dx, dy = self._drag_pos
                    xs.append(int(dx)); ys.append(int(dy))
                    xe.append(int(dx) + m.width); ye.append(int(dy) + m.height)
        bw = max(xe) - min(xs) or 1
        bh = max(ye) - min(ys) or 1
        w = self.get_width() or 600
        h = self.get_height() or 280
        scale = min((w - 2 * PADDING) / bw, (h - 2 * PADDING) / bh, 0.25)
        ox = (w - bw * scale) / 2 - min(xs) * scale
        oy = (h - bh * scale) / 2 - min(ys) * scale
        return scale, ox, oy

    def _rect(self, m: CanvasMonitor) -> tuple[float, float, float, float]:
        s, ox, oy = self._fit()
        x, y = (self._drag_pos if m.name == self._drag_name else (m.x, m.y))
        return x * s + ox, y * s + oy, m.width * s, m.height * s

    def _hit(self, cx: float, cy: float) -> CanvasMonitor | None:
        for m in reversed(self.monitors):
            x, y, w, h = self._rect(m)
            if x <= cx <= x + w and y <= cy <= y + h:
                return m
        return None

    # --- snapping -------------------------------------------------------------

    def _snap(self, m: CanvasMonitor, rx: float, ry: float,
              s: float | None = None) -> tuple[int, int]:
        if s is None:
            s = self._fit()[0]
        thr = SNAP_CANVAS_PX / max(s, 1e-6)
        others = [o for o in self.monitors if o.name != m.name and o.enabled]

        def best(value: float, candidates: list[float]) -> float:
            good = [c for c in candidates if abs(c - value) <= thr]
            return min(good, key=lambda c: abs(c - value)) if good else value

        cx: list[float] = []
        cy: list[float] = []
        for o in others:
            cx += [o.x + o.width, o.x - m.width, o.x, o.x + o.width - m.width]
            cy += [o.y + o.height, o.y - m.height, o.y, o.y + o.height - m.height]
        sx, sy = best(rx, cx), best(ry, cy)

        def overlaps(x: float, y: float) -> bool:
            for o in others:
                if x < o.x + o.width and x + m.width > o.x and \
                   y < o.y + o.height and y + m.height > o.y:
                    return True
            return False

        for x, y in ((sx, sy), (sx, ry), (rx, sy), (rx, ry)):
            if not overlaps(x, y):
                return round(x), round(y)
        return round(rx), round(ry)  # overlapping; caller marks invalid

    def _overlapping(self, m: CanvasMonitor, x: float, y: float) -> bool:
        for o in self.monitors:
            if o.name == m.name or not o.enabled:
                continue
            if x < o.x + o.width and x + m.width > o.x and \
               y < o.y + o.height and y + m.height > o.y:
                return True
        return False

    # --- gestures -------------------------------------------------------------

    def _drag_begin(self, gesture: Gtk.GestureDrag, cx: float, cy: float) -> None:
        m = self._hit(cx, cy)
        if m is None:
            return
        if self.selected != m.name:
            self.selected = m.name
            if self.on_select:
                self.on_select(m.name)
        if not m.enabled:
            self.queue_draw()
            return
        self._drag_scale = self._fit()[0]
        self._drag_name = m.name
        self._drag_origin = (m.x, m.y)
        self._drag_pos = (float(m.x), float(m.y))
        self._last_valid = (m.x, m.y)
        self._drag_valid = True
        self.queue_draw()

    def _drag_update(self, gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        if not self._drag_name:
            return
        m = next(mm for mm in self.monitors if mm.name == self._drag_name)
        # map pointer movement with the scale captured at drag start: the live
        # fit rescales as the dragged monitor expands the bounds, and feeding
        # that back into the mapping makes the monitor accelerate away from
        # the pointer (drag 133px down could end up thousands of px below)
        s = self._drag_scale
        rx = self._drag_origin[0] + dx / s
        ry = self._drag_origin[1] + dy / s
        sx, sy = self._snap(m, rx, ry, s)
        self._drag_valid = not self._overlapping(m, sx, sy)
        if self._drag_valid:
            self._last_valid = (sx, sy)
        self._drag_pos = (sx, sy)
        self.queue_draw()

    def _drag_end(self, gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        if not self._drag_name:
            return
        name = self._drag_name
        m = next(mm for mm in self.monitors if mm.name == name)
        x, y = self._last_valid
        self._drag_name = None
        m.x, m.y = int(x), int(y)
        self.queue_draw()
        if (x, y) != self._drag_origin and self.on_move:
            self.on_move(name, int(x), int(y))

    def _on_motion(self, ctrl, cx: float, cy: float) -> None:
        m = self._hit(cx, cy)
        cursor = "grab" if m and m.enabled and not self._drag_name else "default"
        if self._drag_name:
            cursor = "grabbing"
        self.set_cursor_from_name(cursor)

    # --- drawing --------------------------------------------------------------

    def _colors(self):
        dark = Adw.StyleManager.get_default().get_dark()
        fg = self.get_color()
        # theme accent: honors user CSS overrides such as matugen's
        # ~/.config/gtk-4.0/colors.css (@define-color accent_bg_color ...)
        ac = None
        found, rgba = self.get_style_context().lookup_color("accent_bg_color")
        if found:
            ac = (rgba.red, rgba.green, rgba.blue)
        if ac is None:
            try:
                accent = Adw.StyleManager.get_default().get_accent_color_rgba()
                ac = (accent.red, accent.green, accent.blue)
            except AttributeError:
                ac = (0.208, 0.518, 0.894)
        return dark, (fg.red, fg.green, fg.blue), ac

    @staticmethod
    def _rounded(cr, x, y, w, h, r) -> None:
        r = min(r, w / 2, h / 2)
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def _draw(self, area, cr, width, height) -> None:
        dark, fg, ac = self._colors()

        # subtle dot grid backdrop
        cr.set_source_rgba(*fg, 0.10)
        step = 24
        for gx in range(step, width, step):
            for gy in range(step, height, step):
                cr.arc(gx, gy, 1.0, 0, 2 * math.pi)
                cr.fill()

        order = [m for m in self.monitors if m.name != self.selected] + \
                [m for m in self.monitors if m.name == self.selected]
        for m in order:
            x, y, w, h = self._rect(m)
            selected = m.name == self.selected
            dragging = m.name == self._drag_name
            invalid = dragging and not self._drag_valid

            alpha = 1.0 if m.enabled else 0.35
            if invalid:
                fill = (0.90, 0.25, 0.25, 0.30)
                border = (0.90, 0.25, 0.25, 0.9)
            elif selected:
                fill = (*ac, 0.28 if dark else 0.18)
                border = (*ac, 1.0)
            else:
                fill = (*fg, 0.10)
                border = (*fg, 0.35)

            if dragging:
                self._rounded(cr, x + 2, y + 4, w, h, CORNER)
                cr.set_source_rgba(0, 0, 0, 0.25)
                cr.fill()

            self._rounded(cr, x, y, w, h, CORNER)
            cr.set_source_rgba(fill[0], fill[1], fill[2], fill[3] * alpha)
            cr.fill_preserve()
            cr.set_source_rgba(border[0], border[1], border[2], border[3] * alpha)
            cr.set_line_width(2.0 if selected or invalid else 1.0)
            if not m.enabled:
                cr.set_dash([5, 4])
            cr.stroke()
            cr.set_dash([])

            # screen "stand" notch at bottom for a monitor-ish look
            if m.enabled and h > 40:
                cr.set_source_rgba(border[0], border[1], border[2], 0.5 * alpha)
                cr.rectangle(x + w / 2 - min(14.0, w * 0.15), y + h - 3, min(28.0, w * 0.3), 3)
                cr.fill()

            # labels
            layout = PangoCairo.create_layout(cr)
            desc = Pango.FontDescription("Sans Bold 11")
            layout.set_font_description(desc)
            title = m.name + ("  ★" if m.primary else "")
            layout.set_text(title)
            tw, th = layout.get_pixel_size()
            sub = None
            if m.sublabel and h > 58 and w > 90:
                sub = PangoCairo.create_layout(cr)
                sub.set_font_description(Pango.FontDescription("Sans 8.5"))
                sub.set_text(m.sublabel)
            total_h = th + (sub.get_pixel_size()[1] + 2 if sub else 0)
            ty = y + (h - total_h) / 2
            cr.set_source_rgba(*fg, 0.95 * alpha)
            cr.move_to(x + (w - tw) / 2, ty)
            PangoCairo.show_layout(cr, layout)
            if sub:
                sw, _ = sub.get_pixel_size()
                cr.set_source_rgba(*fg, 0.6 * alpha)
                cr.move_to(x + (w - sw) / 2, ty + th + 2)
                PangoCairo.show_layout(cr, sub)
