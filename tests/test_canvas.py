"""Canvas drag regression tests (skipped when GTK is unavailable)."""

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk
    _GTK = Gtk.init_check()
except Exception:
    _GTK = False

if _GTK:
    from niri_display_settings.canvas import CanvasMonitor, MonitorCanvas

    def _drag(target_dx, target_dy, steps=30):
        c = MonitorCanvas()
        c.set_monitors([
            CanvasMonitor("eDP-1", 0, 0, 1970, 1108),
            CanvasMonitor("DP-2", 1970, 0, 2560, 1440),
        ])
        moved = []
        c.on_move = lambda n, x, y: moved.append((n, x, y))
        s, ox, oy = c._fit()
        c._drag_begin(None, 1970 * s + ox + 150, 0 * s + oy + 80)
        for i in range(1, steps + 1):
            c._drag_update(None, target_dx * i / steps, target_dy * i / steps)
        c._drag_end(None, target_dx, target_dy)
        return moved

    def test_drag_below_lands_exactly_below():
        # regression: the live fit used to feed back into the drag mapping,
        # accelerating the monitor thousands of px past the pointer
        assert _drag(-236, 133) == [("DP-2", 0, 1108)]

    def test_drag_result_independent_of_event_count():
        assert _drag(-236, 133, steps=5) == _drag(-236, 133, steps=60)

    def test_small_vertical_offset_not_flung():
        [(_, x, y)] = _drag(0, 40)
        assert x == 1970 and 250 < y < 450
