"""Thin wrapper around the niri CLI: read output state, apply temporary
changes (previews) and validate config files.

Temporary changes via ``niri msg output`` are never written to the config
file and are forgotten as soon as the config file reloads — which makes them
a safe live-preview mechanism.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class NiriError(RuntimeError):
    pass


def _run(args: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise NiriError(f"command not found: {args[0]}") from e
    except subprocess.TimeoutExpired as e:
        raise NiriError(f"timed out: {' '.join(args)}") from e


@dataclass
class Mode:
    width: int
    height: int
    refresh_mhz: int
    is_preferred: bool = False

    @property
    def refresh_hz(self) -> float:
        return self.refresh_mhz / 1000.0

    def mode_string(self) -> str:
        """Format for config files / niri msg, e.g. 2560x1440@165.000"""
        return f"{self.width}x{self.height}@{self.refresh_hz:.3f}"

    def label(self) -> str:
        return f"{self.refresh_hz:.2f} Hz".replace(".00 ", " ")


@dataclass
class Logical:
    x: int
    y: int
    width: int
    height: int
    scale: float
    transform: str  # config-style: normal, 90, 180, 270, flipped, flipped-90, ...


@dataclass
class Output:
    name: str
    make: str
    model: str
    serial: str | None
    modes: list[Mode] = field(default_factory=list)
    current_mode: int | None = None
    vrr_supported: bool = False
    vrr_enabled: bool = False
    logical: Logical | None = None

    @property
    def enabled(self) -> bool:
        return self.logical is not None

    @property
    def mms_identifier(self) -> str:
        """The 'Make Model Serial' identifier niri also accepts in configs."""
        return f"{self.make} {self.model} {self.serial or 'Unknown'}"

    def aliases(self) -> list[str]:
        return [self.name, self.mms_identifier]

    def mode(self) -> Mode | None:
        if self.current_mode is None or not self.modes:
            return None
        return self.modes[self.current_mode]


# IPC JSON transform -> config-file / niri msg spelling
_TRANSFORMS = {
    "Normal": "normal", "_90": "90", "_180": "180", "_270": "270",
    "90": "90", "180": "180", "270": "270",
    "Flipped": "flipped", "Flipped90": "flipped-90",
    "Flipped180": "flipped-180", "Flipped270": "flipped-270",
}


def _parse_transform(t: str) -> str:
    return _TRANSFORMS.get(t, str(t).lower())


def get_outputs() -> dict[str, Output]:
    p = _run(["niri", "msg", "--json", "outputs"])
    if p.returncode != 0:
        raise NiriError(p.stderr.strip() or "niri msg outputs failed")
    data = json.loads(p.stdout)
    outputs: dict[str, Output] = {}
    for name, o in data.items():
        modes = [Mode(m["width"], m["height"], m["refresh_rate"], m.get("is_preferred", False))
                 for m in o.get("modes", [])]
        logical = None
        if o.get("logical"):
            lg = o["logical"]
            logical = Logical(lg["x"], lg["y"], lg["width"], lg["height"],
                              lg["scale"], _parse_transform(lg["transform"]))
        outputs[name] = Output(
            name=name,
            make=o.get("make", "Unknown"),
            model=o.get("model", "Unknown"),
            serial=o.get("serial"),
            modes=modes,
            current_mode=o.get("current_mode"),
            vrr_supported=o.get("vrr_supported", False),
            vrr_enabled=o.get("vrr_enabled", False),
            logical=logical,
        )
    return outputs


# --- temporary (preview) changes ---------------------------------------------

def _msg_output(name: str, *action: str) -> None:
    p = _run(["niri", "msg", "output", name, *action])
    if p.returncode != 0:
        raise NiriError(p.stderr.strip() or f"niri msg output {name} {' '.join(action)} failed")


def set_enabled(name: str, enabled: bool) -> None:
    _msg_output(name, "on" if enabled else "off")


def set_mode(name: str, mode: str) -> None:
    _msg_output(name, "mode", mode)


def set_scale(name: str, scale: float) -> None:
    _msg_output(name, "scale", f"{scale:g}")


def set_position(name: str, x: int, y: int) -> None:
    _msg_output(name, "position", "set", str(x), str(y))


def set_transform(name: str, transform: str) -> None:
    _msg_output(name, "transform", transform)


def set_vrr(name: str, vrr: str) -> None:
    """vrr: off | on | on-demand"""
    if vrr == "on-demand":
        _msg_output(name, "vrr", "--on-demand", "on")
    else:
        _msg_output(name, "vrr", vrr)


# --- validation ---------------------------------------------------------------

def validate(config_path: Path) -> tuple[bool, str]:
    p = _run(["niri", "validate", "-c", str(config_path)], timeout=15)
    return p.returncode == 0, (p.stderr or p.stdout).strip()
