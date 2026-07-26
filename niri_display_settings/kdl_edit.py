"""Surgical editor for niri KDL config files.

Edits ``output "..." { ... }`` blocks in place, preserving every comment,
blank line, unknown property and the surrounding formatting.  Never
parse-and-regenerate: user configs are hand-written and full of comments.

Also resolves the ``include`` closure of a niri config so the tool can find
which file actually holds the output blocks (config.kdl itself, or a split
file like output.kdl), and whether a manually chosen file is included.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# --- character-level scanning -------------------------------------------------

CODE, LINE_COMMENT, BLOCK_COMMENT, STRING = 0, 1, 2, 3


def code_mask(text: str) -> list[int]:
    """Classify every character as CODE / LINE_COMMENT / BLOCK_COMMENT / STRING.

    Understands ``//`` line comments, nested ``/* */`` block comments and
    ``"..."`` strings with backslash escapes (the constructs that appear in
    real niri configs).
    """
    mask = [CODE] * len(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = text.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                mask[k] = LINE_COMMENT
            i = j
        elif c == "/" and nxt == "*":
            depth, j = 1, i + 2
            while j < n and depth:
                if text[j] == "/" and j + 1 < n and text[j + 1] == "*":
                    depth += 1
                    j += 2
                elif text[j] == "*" and j + 1 < n and text[j + 1] == "/":
                    depth -= 1
                    j += 2
                else:
                    j += 1
            for k in range(i, j):
                mask[k] = BLOCK_COMMENT
            i = j
        elif c == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                elif text[j] == '"':
                    j += 1
                    break
                else:
                    j += 1
            for k in range(i, j):
                mask[k] = STRING
            i = j
        else:
            i += 1
    return mask


def _code_part_of_line(line: str) -> str:
    """Return the portion of a single line before any // comment (string-aware)."""
    mask = code_mask(line)
    for i, m in enumerate(mask):
        if m == LINE_COMMENT:
            return line[:i]
    return line


# --- output block model -------------------------------------------------------

@dataclass
class OutputBlock:
    file: Path
    identifier: str          # as written in the config, e.g. eDP-1 or "Make Model Serial"
    node_start: int          # offset of the 'output' keyword
    open_brace: int          # offset of '{'
    close_brace: int         # offset of '}'
    slashdash: bool = False  # preceded by /- (whole node commented out)

    def body(self, text: str) -> str:
        return text[self.open_brace + 1 : self.close_brace]


_OUTPUT_RE = re.compile(r'\boutput\b')


def find_output_blocks(text: str) -> list[OutputBlock]:
    """Locate top-level ``output "name" { ... }`` blocks (comment/string aware)."""
    mask = code_mask(text)
    blocks: list[OutputBlock] = []
    # brace depth over code chars only
    depth = 0
    depth_at = [0] * (len(text) + 1)
    for i, c in enumerate(text):
        depth_at[i] = depth
        if mask[i] == CODE:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
    depth_at[len(text)] = depth

    for m in _OUTPUT_RE.finditer(text):
        i = m.start()
        if mask[i] != CODE or depth_at[i] != 0:
            continue
        # must be at start of a node: preceded by newline/;/start (or /- slashdash)
        j = i - 1
        while j >= 0 and text[j] in " \t":
            j -= 1
        slashdash = j >= 1 and text[j - 1 : j + 1] == "/-"
        if slashdash:
            j -= 2
            while j >= 0 and text[j] in " \t":
                j -= 1
        if j >= 0 and text[j] not in "\n;":
            continue
        # parse identifier: quoted string or bare word
        k = m.end()
        while k < len(text) and text[k] in " \t":
            k += 1
        if k >= len(text):
            continue
        if text[k] == '"':
            e = k + 1
            while e < len(text):
                if text[e] == "\\":
                    e += 2
                elif text[e] == '"':
                    break
                else:
                    e += 1
            identifier = text[k + 1 : e].replace('\\"', '"')
            k = e + 1
        else:
            e = k
            while e < len(text) and not text[e].isspace() and text[e] != "{":
                e += 1
            identifier = text[k:e]
            k = e
        # find opening brace
        while k < len(text) and (mask[k] != CODE or text[k] != "{"):
            if text[k] == "\n":
                break
            k += 1
        if k >= len(text) or text[k] != "{":
            continue
        open_brace = k
        # matching close brace
        d = 0
        close = -1
        for p in range(open_brace, len(text)):
            if mask[p] != CODE:
                continue
            if text[p] == "{":
                d += 1
            elif text[p] == "}":
                d -= 1
                if d == 0:
                    close = p
                    break
        if close == -1:
            continue
        blocks.append(OutputBlock(Path(), identifier, m.start(), open_brace, close, slashdash))
    return blocks


# --- block body editing -------------------------------------------------------

# properties we manage; each matched at start of a body line's code part
_PROP_RES = {
    "mode": re.compile(r'^(\s*)mode\b[^\n]*'),
    "scale": re.compile(r'^(\s*)scale\b[^\n]*'),
    "position": re.compile(r'^(\s*)position\b[^\n]*'),
    "transform": re.compile(r'^(\s*)transform\b[^\n]*'),
    "variable-refresh-rate": re.compile(r'^(\s*)variable-refresh-rate\b[^\n]*'),
    "focus-at-startup": re.compile(r'^(\s*)focus-at-startup\b[^\n]*'),
    "off": re.compile(r'^(\s*)off\s*$'),
}


def _line_prop(line: str) -> str | None:
    code = _code_part_of_line(line)
    for prop, rx in _PROP_RES.items():
        if rx.match(code):
            return prop
    return None


def _guess_indent(lines: list[str]) -> str:
    for line in lines:
        if line.strip() and _line_prop(line):
            return line[: len(line) - len(line.lstrip())]
    for line in lines:
        if line.strip():
            return line[: len(line) - len(line.lstrip())]
    return "    "


def edit_block_body(body: str, changes: dict[str, str | None]) -> str:
    """Apply property changes to an output block body.

    ``changes`` maps property name -> full replacement line content (without
    indent), or ``None`` to remove the property.  Lines not addressed by
    ``changes`` (comments, unknown props, commented-out props) are untouched.
    Trailing ``//`` comments on replaced lines are preserved.
    """
    lines = body.split("\n")
    indent = _guess_indent(lines)
    remaining = dict(changes)
    out: list[str] = []
    for line in lines:
        prop = _line_prop(line)
        if prop is None or prop not in remaining:
            out.append(line)
            continue
        new_value = remaining.pop(prop)
        if new_value is None:
            continue  # drop the line
        line_indent = line[: len(line) - len(line.lstrip())] or indent
        code = _code_part_of_line(line)
        trailing = line[len(code):]  # the // comment, if any
        if trailing.strip():
            out.append(f"{line_indent}{new_value} {trailing.strip()}")
        else:
            out.append(f"{line_indent}{new_value}")
    # insert new properties that had no existing line
    additions = [f"{indent}{v}" for v in remaining.values() if v is not None]
    if additions:
        # insert after the last non-empty line, before the closing-brace line
        insert_at = len(out)
        while insert_at > 0 and out[insert_at - 1].strip() == "":
            insert_at -= 1
        out[insert_at:insert_at] = additions
    return "\n".join(out)


# --- include resolution -------------------------------------------------------

_INCLUDE_RE = re.compile(r'^\s*include\b(?P<args>[^\n]*)$')
_STRING_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


@dataclass
class ConfigInfo:
    main_config: Path
    files: list[Path]                         # include closure, main first
    blocks: dict[str, OutputBlock] = field(default_factory=dict)  # identifier -> block
    texts: dict[Path, str] = field(default_factory=dict)
    extra_file: Path | None = None            # manually chosen target file
    extra_included: bool = True               # is extra_file in the include closure?

    def target_file(self) -> Path:
        """File where new output blocks should go."""
        if self.extra_file is not None:
            return self.extra_file
        for b in self.blocks.values():
            return b.file
        for f in self.files[1:]:
            if f.name == "output.kdl":
                return f
        return self.main_config


def default_config_path() -> Path:
    env = os.environ.get("NIRI_CONFIG")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME", "~/.config")
    return Path(xdg).expanduser() / "niri" / "config.kdl"


def resolve_includes(config: Path, _seen: set[Path] | None = None) -> list[Path]:
    """Return the include closure of ``config`` (itself first), existing files only."""
    seen = _seen if _seen is not None else set()
    config = config.resolve()
    if config in seen or not config.is_file():
        return []
    seen.add(config)
    result = [config]
    try:
        text = config.read_text()
    except OSError:
        return result
    mask = code_mask(text)
    for lineno, line in enumerate(text.split("\n")):
        m = _INCLUDE_RE.match(line)
        if not m:
            continue
        # reject if the include keyword itself is inside a comment
        offset = sum(len(l) + 1 for l in text.split("\n")[:lineno])
        kw = offset + line.find("include")
        if kw < len(mask) and mask[kw] != CODE:
            continue
        sm = _STRING_RE.search(m.group("args"))
        if not sm:
            continue
        raw = sm.group(1)
        p = Path(os.path.expanduser(raw))
        if not p.is_absolute():
            p = config.parent / p
        result += resolve_includes(p, seen)
    return result


def load_config(config: Path | None = None, extra_file: Path | None = None) -> ConfigInfo:
    main = (config or default_config_path()).resolve()
    files = resolve_includes(main) or [main]
    info = ConfigInfo(main_config=main, files=files)
    for f in files:
        try:
            text = f.read_text()
        except OSError:
            continue
        info.texts[f] = text
        for b in find_output_blocks(text):
            if b.slashdash:
                continue
            b.file = f
            info.blocks.setdefault(b.identifier, b)
    if extra_file is not None:
        ef = extra_file.resolve()
        info.extra_file = ef
        info.extra_included = ef in files
        if ef not in info.texts:
            try:
                text = ef.read_text()
            except OSError:
                text = ""
            info.texts[ef] = text
            for b in find_output_blocks(text):
                if b.slashdash:
                    continue
                b.file = ef
                info.blocks[b.identifier] = b  # manual file takes precedence
    return info


def is_included(info: ConfigInfo, file: Path) -> bool:
    return file.resolve() in [f.resolve() for f in info.files]


def add_include_line(config_text: str, include_path: str) -> str:
    """Append an include line after the last existing include (or at top)."""
    line = f'include optional=true "{include_path}"'
    lines = config_text.split("\n")
    mask_text = config_text
    last_inc = -1
    for i, l in enumerate(lines):
        if _INCLUDE_RE.match(l):
            offset = sum(len(x) + 1 for x in lines[:i])
            kw = offset + l.find("include")
            if kw < len(mask_text) and code_mask(mask_text)[kw] == CODE:
                last_inc = i
    if last_inc >= 0:
        lines.insert(last_inc + 1, line)
    else:
        lines.insert(0, line)
    return "\n".join(lines)


# --- high-level: apply output settings to files -------------------------------

@dataclass
class OutputSettings:
    """Desired persistent state for one output."""
    enabled: bool = True
    mode: str | None = None          # "2560x1440@165.000"
    scale: float | None = None
    position: tuple[int, int] | None = None
    transform: str | None = None     # "normal" means remove the property
    vrr: str = "off"                 # off | on | on-demand
    focus_at_startup: bool = False


def _fmt_scale(scale: float) -> str:
    s = f"{scale:.6f}".rstrip("0").rstrip(".")
    return s


def settings_to_changes(s: OutputSettings) -> dict[str, str | None]:
    changes: dict[str, str | None] = {}
    changes["off"] = None if s.enabled else "off"
    if s.mode is not None:
        changes["mode"] = f'mode "{s.mode}"'
    if s.scale is not None:
        changes["scale"] = f"scale {_fmt_scale(s.scale)}"
    if s.position is not None:
        changes["position"] = f"position x={s.position[0]} y={s.position[1]}"
    if s.transform is not None:
        changes["transform"] = None if s.transform == "normal" else f'transform "{s.transform}"'
    if s.vrr == "off":
        changes["variable-refresh-rate"] = None
    elif s.vrr == "on":
        changes["variable-refresh-rate"] = "variable-refresh-rate"
    else:
        changes["variable-refresh-rate"] = "variable-refresh-rate on-demand=true"
    changes["focus-at-startup"] = "focus-at-startup" if s.focus_at_startup else None
    return changes


def new_block_text(identifier: str, s: OutputSettings) -> str:
    lines = [f'output "{identifier}" {{']
    for v in settings_to_changes(s).values():
        if v is not None:
            lines.append(f"    {v}")
    lines.append("}")
    return "\n".join(lines)


def apply_settings(info: ConfigInfo, desired: dict[str, OutputSettings],
                   aliases: dict[str, list[str]]) -> dict[Path, str]:
    """Compute new file contents applying ``desired`` settings.

    ``desired`` is keyed by connector name; ``aliases`` maps connector name to
    every identifier that may appear in the config ("DP-2", "Make Model Serial",
    case-insensitive match).  Returns {path: new_text} for changed files only.
    """
    texts = {p: t for p, t in info.texts.items()}

    # match config blocks to connectors
    def find_block(connector: str) -> OutputBlock | None:
        wanted = {a.lower() for a in aliases.get(connector, [connector])}
        for ident, b in info.blocks.items():
            if ident.lower() in wanted:
                return b
        return None

    # edit or create, processing blocks per file from last to first so offsets stay valid
    per_file: dict[Path, list[tuple[OutputBlock, OutputSettings]]] = {}
    missing: list[tuple[str, OutputSettings]] = []
    for connector, s in desired.items():
        b = find_block(connector)
        if b is None:
            missing.append((connector, s))
        else:
            per_file.setdefault(b.file, []).append((b, s))

    for path, pairs in per_file.items():
        text = texts[path]
        for b, s in sorted(pairs, key=lambda x: -x[0].open_brace):
            new_body = edit_block_body(b.body(text), settings_to_changes(s))
            text = text[: b.open_brace + 1] + new_body + text[b.close_brace :]
        texts[path] = text

    if missing:
        target = info.target_file()
        text = texts.get(target, "")
        for connector, s in missing:
            if text and not text.endswith("\n"):
                text += "\n"
            text += new_block_text(connector, s) + "\n"
        texts[target] = text

    return {p: t for p, t in texts.items() if info.texts.get(p) != t}


def parse_block_settings(text: str, block: OutputBlock) -> OutputSettings:
    """Read the settings currently written in a block (for merging with IPC state)."""
    s = OutputSettings()
    body = block.body(text)
    for line in body.split("\n"):
        code = _code_part_of_line(line).strip()
        prop = _line_prop(line)
        if prop == "off":
            s.enabled = False
        elif prop == "mode":
            m = _STRING_RE.search(code)
            if m:
                s.mode = m.group(1)
        elif prop == "scale":
            m = re.search(r"scale\s+([0-9.]+)", code)
            if m:
                s.scale = float(m.group(1))
        elif prop == "position":
            m = re.search(r"x=(-?\d+)\s+y=(-?\d+)", code)
            if m:
                s.position = (int(m.group(1)), int(m.group(2)))
        elif prop == "transform":
            m = _STRING_RE.search(code)
            if m:
                s.transform = m.group(1)
        elif prop == "variable-refresh-rate":
            s.vrr = "on-demand" if "on-demand=true" in code else "on"
        elif prop == "focus-at-startup":
            s.focus_at_startup = True
    return s
