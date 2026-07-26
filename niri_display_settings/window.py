"""Main window: layout canvas, per-monitor settings and the apply flow.

Apply flow: preview via ``niri msg output`` (temporary, file untouched) →
15 s countdown dialog → on confirm, back up + surgically edit the config
files, validating a staged copy with ``niri validate`` before touching the
real files.  On any failure the previous state is restored.
"""

from __future__ import annotations

import copy
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import kdl_edit, niri_ipc
from .canvas import CanvasMonitor, MonitorCanvas
from .i18n import _

TRANSFORMS = ["normal", "90", "180", "270",
              "flipped", "flipped-90", "flipped-180", "flipped-270"]
VRR_VALUES = ["off", "on", "on-demand"]
COUNTDOWN_S = 15


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
        return max(1, round(w / self.scale)), max(1, round(h / self.scale))


class DisplayWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title=_("Display Settings"))
        self.set_default_size(760, 820)

        self.outputs: dict[str, niri_ipc.Output] = {}
        self.info: kdl_edit.ConfigInfo | None = None
        self.manual_target: Path | None = None
        self.pending: dict[str, Pending] = {}
        self.original: dict[str, Pending] = {}
        self.selected: str | None = None
        self._updating = False

        self._build_ui()
        self.reload(select_first=True)

    # --- UI construction ------------------------------------------------------

    def _build_ui(self) -> None:
        self.toast_overlay = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()

        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic",
                                 tooltip_text=_("Refresh state"))
        refresh_btn.connect("clicked", lambda *_a: self.reload())
        header.pack_start(refresh_btn)

        self.apply_btn = Gtk.Button(label=_("Apply"))
        self.apply_btn.add_css_class("suggested-action")
        self.apply_btn.set_sensitive(False)
        self.apply_btn.connect("clicked", self._on_apply)
        header.pack_end(self.apply_btn)

        view.add_top_bar(header)

        self.banner = Adw.Banner(button_label=_("Fix"))
        self.banner.connect("button-clicked", self._on_fix_include)
        view.add_top_bar(self.banner)

        scroller = Gtk.ScrolledWindow(vexpand=True,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=720, tightening_threshold=560)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                      margin_top=18, margin_bottom=24,
                      margin_start=12, margin_end=12)

        # config file
        cfg_group = Adw.PreferencesGroup(title=_("Configuration File"))
        self.cfg_row = Adw.ActionRow(
            title=_("Output blocks are written to this file"),
            subtitle="—")
        self.cfg_row.add_css_class("property")
        choose = Gtk.Button(label=_("Choose…"), valign=Gtk.Align.CENTER)
        choose.connect("clicked", self._on_choose_file)
        self.cfg_row.add_suffix(choose)
        cfg_group.add(self.cfg_row)
        box.append(cfg_group)

        # canvas card
        canvas_group = Adw.PreferencesGroup(
            title=_("Arrangement"), description=_("Drag monitors to rearrange them"))
        self.canvas = MonitorCanvas()
        self.canvas.add_css_class("card")
        self.canvas.on_select = self._on_canvas_select
        self.canvas.on_move = self._on_canvas_move
        canvas_group.add(self.canvas)
        box.append(canvas_group)

        # per-monitor settings
        self.monitor_group = Adw.PreferencesGroup(title=_("Monitor"))

        self.enabled_row = Adw.SwitchRow(title=_("Enabled"))
        self.enabled_row.connect("notify::active", self._on_enabled)
        self.monitor_group.add(self.enabled_row)

        self.res_row = Adw.ComboRow(title=_("Resolution"))
        self.res_row.connect("notify::selected", self._on_resolution)
        self.monitor_group.add(self.res_row)

        self.rate_row = Adw.ComboRow(title=_("Refresh Rate"))
        self.rate_row.connect("notify::selected", self._on_rate)
        self.monitor_group.add(self.rate_row)

        self.scale_row = Adw.SpinRow.new_with_range(0.25, 4.0, 0.05)
        self.scale_row.set_title(_("Scale"))
        self.scale_row.set_digits(2)
        self.scale_row.connect("notify::value", self._on_scale)
        self.monitor_group.add(self.scale_row)

        self.transform_row = Adw.ComboRow(title=_("Transform"))
        labels = [_("Normal")] + [_(f"_transform_{t}") for t in TRANSFORMS[1:]]
        self.transform_row.set_model(Gtk.StringList.new(labels))
        self.transform_row.connect("notify::selected", self._on_transform)
        self.monitor_group.add(self.transform_row)

        self.vrr_row = Adw.SwitchRow(title=_("Variable Refresh Rate"))
        self.vrr_row.connect("notify::active", self._on_vrr)
        self.monitor_group.add(self.vrr_row)

        self.vrr_demand_row = Adw.SwitchRow(
            title=_("On demand"),
            subtitle=_("Enable VRR only when an application requests it"))
        self.vrr_demand_row.connect("notify::active", self._on_vrr)
        self.monitor_group.add(self.vrr_demand_row)

        self.focus_row = Adw.SwitchRow(
            title=_("Focus at startup"),
            subtitle=_("Only one monitor can be focused at startup"))
        self.focus_row.connect("notify::active", self._on_focus)
        self.monitor_group.add(self.focus_row)

        box.append(self.monitor_group)

        clamp.set_child(box)
        scroller.set_child(clamp)
        view.set_content(scroller)
        self.toast_overlay.set_child(view)
        self.set_content(self.toast_overlay)

    # --- state loading --------------------------------------------------------

    def reload(self, select_first: bool = False) -> None:
        try:
            self.outputs = niri_ipc.get_outputs()
        except niri_ipc.NiriError as e:
            self._error_dialog(_("niri is not running or not reachable"), str(e))
            return
        self.info = kdl_edit.load_config(extra_file=self.manual_target)

        self.pending = {}
        for name, o in self.outputs.items():
            p = Pending()
            block = self._find_block(name)
            cfg = (kdl_edit.parse_block_settings(self.info.texts[block.file], block)
                   if block else None)

            p.enabled = o.enabled
            mode = o.mode()
            if mode:
                p.width, p.height, p.refresh_mhz = mode.width, mode.height, mode.refresh_mhz
            elif cfg and cfg.mode:
                self._parse_mode_into(p, o, cfg.mode)
            elif o.modes:
                pref = next((m for m in o.modes if m.is_preferred), o.modes[0])
                p.width, p.height, p.refresh_mhz = pref.width, pref.height, pref.refresh_mhz
            if o.logical:
                p.scale = o.logical.scale
                p.transform = o.logical.transform
                p.x, p.y = o.logical.x, o.logical.y
            elif cfg:
                p.scale = cfg.scale or 1.0
                p.transform = cfg.transform or "normal"
                if cfg.position:
                    p.x, p.y = cfg.position
            if cfg:
                p.vrr = cfg.vrr
                p.focus = cfg.focus_at_startup
            elif o.vrr_enabled:
                p.vrr = "on"
            self.pending[name] = p

        # disabled outputs without a position: park right of everything
        right = max((p.x + p.logical_size()[0] for p in self.pending.values()
                     if p.enabled), default=0)
        for p in self.pending.values():
            if not p.enabled and (p.x, p.y) == (0, 0):
                p.x, right = right + 80, right + 80 + p.logical_size()[0]

        self.original = copy.deepcopy(self.pending)
        if select_first or self.selected not in self.pending:
            self.selected = next(iter(self.pending), None)
        self._refresh_cfg_row()
        self._refresh_canvas()
        self._populate_rows()
        self._refresh_apply()

    def _find_block(self, connector: str) -> kdl_edit.OutputBlock | None:
        o = self.outputs.get(connector)
        wanted = {a.lower() for a in (o.aliases() if o else [connector])}
        for ident, b in (self.info.blocks if self.info else {}).items():
            if ident.lower() in wanted:
                return b
        return None

    @staticmethod
    def _parse_mode_into(p: Pending, o: niri_ipc.Output, mode_str: str) -> None:
        try:
            res, _at, rate = mode_str.partition("@")
            w, h = (int(v) for v in res.split("x"))
            mhz = round(float(rate) * 1000) if rate else None
        except ValueError:
            return
        best = None
        for m in o.modes:
            if (m.width, m.height) != (w, h):
                continue
            if mhz is None or best is None or \
               abs(m.refresh_mhz - mhz) < abs(best.refresh_mhz - mhz):
                best = m
        if best:
            p.width, p.height, p.refresh_mhz = best.width, best.height, best.refresh_mhz

    # --- canvas ---------------------------------------------------------------

    def _refresh_canvas(self) -> None:
        mons = []
        for name, p in self.pending.items():
            w, h = p.logical_size()
            sub = f"{p.width}×{p.height} · {p.refresh_mhz / 1000:.0f} Hz"
            mons.append(CanvasMonitor(name=name, x=p.x, y=p.y, width=w, height=h,
                                      enabled=p.enabled, primary=p.focus, sublabel=sub))
        self.canvas.set_monitors(mons, self.selected)

    def _on_canvas_select(self, name: str) -> None:
        self.selected = name
        self._populate_rows()

    def _on_canvas_move(self, name: str, x: int, y: int) -> None:
        self.pending[name].x = x
        self.pending[name].y = y
        self._normalize_positions()
        self._refresh_canvas()
        self._refresh_apply()

    def _normalize_positions(self) -> None:
        """Shift all monitors so the top-left of the enabled bounds is (0, 0).

        Keeps the live preview coordinates identical to what gets written to
        the config file (which is normalized the same way).
        """
        enabled = [p for p in self.pending.values() if p.enabled]
        if not enabled:
            return
        min_x = min(p.x for p in enabled)
        min_y = min(p.y for p in enabled)
        if (min_x, min_y) == (0, 0):
            return
        for p in self.pending.values():
            p.x -= min_x
            p.y -= min_y

    # --- per-monitor rows -----------------------------------------------------

    def _resolutions(self, o: niri_ipc.Output) -> list[tuple[int, int]]:
        seen: list[tuple[int, int]] = []
        for m in o.modes:
            if (m.width, m.height) not in seen:
                seen.append((m.width, m.height))
        return sorted(seen, key=lambda r: (-r[0] * r[1], -r[0]))

    def _rates(self, o: niri_ipc.Output, w: int, h: int) -> list[niri_ipc.Mode]:
        rates = [m for m in o.modes if (m.width, m.height) == (w, h)]
        return sorted(rates, key=lambda m: -m.refresh_mhz)

    def _populate_rows(self) -> None:
        name = self.selected
        if not name or name not in self.pending:
            self.monitor_group.set_sensitive(False)
            return
        self.monitor_group.set_sensitive(True)
        o, p = self.outputs[name], self.pending[name]
        self._updating = True
        try:
            self.monitor_group.set_title(f"{name} — {o.make} {o.model}")

            self.enabled_row.set_active(p.enabled)

            res = self._resolutions(o)
            self.res_row.set_model(Gtk.StringList.new([f"{w}×{h}" for w, h in res]))
            if (p.width, p.height) in res:
                self.res_row.set_selected(res.index((p.width, p.height)))

            rates = self._rates(o, p.width, p.height)
            self.rate_row.set_model(Gtk.StringList.new(
                [f"{m.refresh_hz:.2f} Hz" for m in rates]))
            idx = next((i for i, m in enumerate(rates)
                        if m.refresh_mhz == p.refresh_mhz), 0)
            if rates:
                self.rate_row.set_selected(idx)

            self.scale_row.set_value(p.scale)
            self.transform_row.set_selected(TRANSFORMS.index(p.transform)
                                            if p.transform in TRANSFORMS else 0)
            self.vrr_row.set_visible(o.vrr_supported)
            self.vrr_row.set_active(p.vrr != "off")
            self.vrr_demand_row.set_visible(o.vrr_supported and p.vrr != "off")
            self.vrr_demand_row.set_active(p.vrr == "on-demand")
            self.focus_row.set_active(p.focus)
        finally:
            self._updating = False

    def _sel(self) -> Pending | None:
        if self._updating or not self.selected:
            return None
        return self.pending.get(self.selected)

    def _on_enabled(self, row, _pspec) -> None:
        p = self._sel()
        if p is None:
            return
        if not row.get_active() and sum(q.enabled for q in self.pending.values()) <= 1 \
                and p.enabled:
            self._toast(_("Cannot disable the last enabled monitor"))
            self._updating = True
            row.set_active(True)
            self._updating = False
            return
        p.enabled = row.get_active()
        self._refresh_canvas()
        self._refresh_apply()

    def _on_resolution(self, row, _pspec) -> None:
        p = self._sel()
        if p is None or row.get_selected() == Gtk.INVALID_LIST_POSITION:
            return
        o = self.outputs[self.selected]
        res = self._resolutions(o)
        if row.get_selected() >= len(res):
            return
        w, h = res[row.get_selected()]
        if (w, h) == (p.width, p.height):
            return
        p.width, p.height = w, h
        rates = self._rates(o, w, h)
        best = next((m for m in rates if m.is_preferred), rates[0] if rates else None)
        if best:
            p.refresh_mhz = best.refresh_mhz
        self._populate_rows()
        self._refresh_canvas()
        self._refresh_apply()

    def _on_rate(self, row, _pspec) -> None:
        p = self._sel()
        if p is None or row.get_selected() == Gtk.INVALID_LIST_POSITION:
            return
        rates = self._rates(self.outputs[self.selected], p.width, p.height)
        if row.get_selected() < len(rates):
            p.refresh_mhz = rates[row.get_selected()].refresh_mhz
            self._refresh_canvas()
            self._refresh_apply()

    def _on_scale(self, row, _pspec) -> None:
        p = self._sel()
        if p is None:
            return
        p.scale = round(row.get_value(), 2)
        self._refresh_canvas()
        self._refresh_apply()

    def _on_transform(self, row, _pspec) -> None:
        p = self._sel()
        if p is None:
            return
        p.transform = TRANSFORMS[row.get_selected()]
        self._refresh_canvas()
        self._refresh_apply()

    def _on_vrr(self, _row, _pspec) -> None:
        p = self._sel()
        if p is None:
            return
        on = self.vrr_row.get_active()
        if on:
            p.vrr = "on-demand" if self.vrr_demand_row.get_active() else "on"
        else:
            p.vrr = "off"
        self.vrr_demand_row.set_visible(
            on and self.outputs[self.selected].vrr_supported)
        self._refresh_apply()

    def _on_focus(self, row, _pspec) -> None:
        p = self._sel()
        if p is None:
            return
        p.focus = row.get_active()
        if p.focus:
            for name, q in self.pending.items():
                if name != self.selected:
                    q.focus = False
        self._refresh_canvas()
        self._refresh_apply()

    def _refresh_apply(self) -> None:
        self.apply_btn.set_sensitive(self.pending != self.original)

    # --- config file row ------------------------------------------------------

    def _refresh_cfg_row(self) -> None:
        target = self.info.target_file()
        home = str(Path.home())
        shown = str(target).replace(home, "~")
        self.cfg_row.set_subtitle(shown)
        included = kdl_edit.is_included(self.info, target)
        self.banner.set_title(_("This file is not included from config.kdl"))
        self.banner.set_revealed(not included)

    def _on_choose_file(self, _btn) -> None:
        dialog = Gtk.FileDialog(title=_("Choose config file"))
        f = Gtk.FileFilter()
        f.add_pattern("*.kdl")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dialog.set_filters(filters)
        dialog.set_initial_folder(
            Gio.File.new_for_path(str(self.info.main_config.parent)))

        def done(dlg, result):
            try:
                gfile = dlg.open_finish(result)
            except GLib.Error:
                return
            self.manual_target = Path(gfile.get_path())
            self.reload()

        dialog.open(self, None, done)

    def _on_fix_include(self, _banner) -> None:
        cfg = self.info.main_config
        target = self.info.target_file()
        try:
            rel = target.relative_to(cfg.parent)
            inc_path = str(rel)
        except ValueError:
            inc_path = str(target)
        backup = cfg.with_name(cfg.name + ".bak")
        shutil.copy2(cfg, backup)
        if not target.exists():
            target.touch()
        cfg.write_text(kdl_edit.add_include_line(cfg.read_text(), inc_path))
        ok, msg = niri_ipc.validate(cfg)
        if not ok:
            shutil.copy2(backup, cfg)
            self._error_dialog(_("Validation failed"),
                               _("Config restored from backup. niri reported:") + "\n\n" + msg)
            return
        self._toast(_("Include line added to config.kdl (backup: {path})")
                    .format(path=backup.name))
        self.reload()

    # --- apply flow -----------------------------------------------------------

    def _live_ops(self, state: dict[str, Pending],
                  base: dict[str, Pending]) -> list[tuple]:
        """niri msg operations to go from ``base`` to ``state``."""
        ops: list[tuple] = []
        for name, p in state.items():
            b = base.get(name, Pending())
            if p.enabled and not b.enabled:
                ops.append((niri_ipc.set_enabled, name, True))
        for name, p in state.items():
            if not p.enabled:
                continue
            b = base.get(name, Pending())
            if (p.width, p.height, p.refresh_mhz) != (b.width, b.height, b.refresh_mhz) \
                    or not b.enabled:
                ops.append((niri_ipc.set_mode, name, p.mode_string()))
            if p.scale != b.scale or not b.enabled:
                ops.append((niri_ipc.set_scale, name, p.scale))
            if p.transform != b.transform or not b.enabled:
                ops.append((niri_ipc.set_transform, name, p.transform))
            if p.vrr != b.vrr:
                ops.append((niri_ipc.set_vrr, name, p.vrr))
            if (p.x, p.y) != (b.x, b.y) or not b.enabled:
                ops.append((niri_ipc.set_position, name, p.x, p.y))
        for name, p in state.items():
            b = base.get(name, Pending())
            if not p.enabled and b.enabled:
                ops.append((niri_ipc.set_enabled, name, False))
        return ops

    def _run_ops(self, ops: list[tuple]) -> None:
        for fn, *args in ops:
            fn(*args)

    def _on_apply(self, _btn) -> None:
        if self.pending == self.original:
            self._toast(_("No changes to apply"))
            return
        ops = self._live_ops(self.pending, self.original)
        try:
            self._run_ops(ops)
        except niri_ipc.NiriError as e:
            try:
                self._run_ops(self._live_ops(self.original, self.pending))
            except niri_ipc.NiriError:
                pass
            self._error_dialog(_("Failed to apply preview"), str(e))
            return
        self._countdown_dialog()

    def _countdown_dialog(self) -> None:
        dialog = Adw.AlertDialog(heading=_("Keep these display settings?"))
        dialog.add_response("revert", _("Revert"))
        dialog.add_response("keep", _("Keep"))
        dialog.set_response_appearance("keep", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("keep")
        dialog.set_close_response("revert")

        remaining = {"n": COUNTDOWN_S}
        dialog.set_body(_("Settings will revert in {n} seconds.").format(n=remaining["n"]))

        def tick() -> bool:
            remaining["n"] -= 1
            if remaining["n"] <= 0:
                dialog.close()
                return False
            dialog.set_body(_("Settings will revert in {n} seconds.")
                            .format(n=remaining["n"]))
            return True

        source = GLib.timeout_add_seconds(1, tick)

        def on_response(dlg, response: str) -> None:
            GLib.source_remove(source) if remaining["n"] > 0 else None
            if response == "keep":
                self._persist()
            else:
                try:
                    self._run_ops(self._live_ops(self.original, self.pending))
                except niri_ipc.NiriError as e:
                    self._error_dialog(_("Failed to apply preview"), str(e))
                self.pending = copy.deepcopy(self.original)
                self._refresh_canvas()
                self._populate_rows()
                self._refresh_apply()
                self._toast(_("Settings reverted"))

        dialog.connect("response", on_response)
        dialog.present(self)

    def _desired_settings(self) -> dict[str, kdl_edit.OutputSettings]:
        enabled = [p for p in self.pending.values() if p.enabled]
        min_x = min((p.x for p in enabled), default=0)
        min_y = min((p.y for p in enabled), default=0)
        desired = {}
        for name, p in self.pending.items():
            # keep the mode string as written in the config when it already
            # denotes the same mode (avoids rewriting "@165" to "@165.000")
            mode_str = p.mode_string()
            block = self._find_block(name)
            if block is not None:
                cfg = kdl_edit.parse_block_settings(self.info.texts[block.file], block)
                if cfg.mode:
                    probe = Pending()
                    self._parse_mode_into(probe, self.outputs[name], cfg.mode)
                    if (probe.width, probe.height, probe.refresh_mhz) == \
                            (p.width, p.height, p.refresh_mhz):
                        mode_str = cfg.mode
            desired[name] = kdl_edit.OutputSettings(
                enabled=p.enabled,
                mode=mode_str,
                scale=p.scale,
                position=(p.x - min_x, p.y - min_y),
                transform=p.transform,
                vrr=p.vrr,
                focus_at_startup=p.focus,
            )
        return desired

    def _persist(self) -> None:
        info = self.info
        aliases = {name: o.aliases() for name, o in self.outputs.items()}
        changed = kdl_edit.apply_settings(info, self._desired_settings(), aliases)
        if not changed:
            self._toast(_("Settings applied and saved"))
            return

        # staged validation: copy the config dir, apply edits there, validate
        cfg_dir = info.main_config.parent
        with tempfile.TemporaryDirectory(prefix="niri-display-") as tmp:
            stage = Path(tmp) / "cfg"
            try:
                shutil.copytree(cfg_dir, stage, ignore_dangling_symlinks=True)
            except shutil.Error:
                pass  # partial copy is still useful for validation
            stageable = True
            for path, text in changed.items():
                try:
                    rel = path.relative_to(cfg_dir)
                except ValueError:
                    stageable = False
                    break
                staged = stage / rel
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_text(text)
            if stageable and (stage / info.main_config.name).exists():
                ok, msg = niri_ipc.validate(stage / info.main_config.name)
                if not ok:
                    self._error_dialog(
                        _("Validation failed"),
                        _("The config file was not modified. niri reported:")
                        + "\n\n" + msg)
                    return

        # back up and write
        backups: dict[Path, Path] = {}
        for path in changed:
            if path.exists():
                bak = path.with_name(path.name + ".bak")
                shutil.copy2(path, bak)
                backups[path] = bak
        for path, text in changed.items():
            path.write_text(text)

        ok, msg = niri_ipc.validate(info.main_config)
        if not ok:
            for path, bak in backups.items():
                shutil.copy2(bak, path)
            for path in changed:
                if path not in backups and path.exists():
                    path.unlink()
            self._error_dialog(_("Validation failed"),
                               _("Config restored from backup. niri reported:")
                               + "\n\n" + msg)
            return

        names = ", ".join(b.name for b in backups.values())
        toast_msg = _("Settings applied and saved")
        if names:
            toast_msg += " · " + _("Backup saved as {path}").format(path=names)
        self._toast(toast_msg)
        self.original = copy.deepcopy(self.pending)
        self.info = kdl_edit.load_config(extra_file=self.manual_target)
        self._refresh_cfg_row()
        self._refresh_apply()

    # --- helpers --------------------------------------------------------------

    def _toast(self, message: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=message, timeout=4))

    def _error_dialog(self, heading: str, body: str) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("ok", "OK")
        dialog.present(self)
