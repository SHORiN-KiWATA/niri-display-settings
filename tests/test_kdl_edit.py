from niri_display_settings import kdl_edit as k

# real-world sample: hand-written, tabs+spaces, Chinese comments, commented-out off
REAL_OUTPUT_KDL = '''output "eDP-1"{
//\toff
    mode "2560x1440@165"
\tscale 1.3
\tposition x=0 y=0

}
output "DP-2"{
\t//主显示器DP-2
\tmode "2560x1440@180"
\tscale 1
\tposition x=1970 y=0
    focus-at-startup

}
'''


def blocks(text):
    return {b.identifier: b for b in k.find_output_blocks(text)}


def test_find_blocks_real_config():
    bs = blocks(REAL_OUTPUT_KDL)
    assert set(bs) == {"eDP-1", "DP-2"}


def test_commented_off_is_ignored():
    b = blocks(REAL_OUTPUT_KDL)["eDP-1"]
    s = k.parse_block_settings(REAL_OUTPUT_KDL, b)
    assert s.enabled is True
    assert s.mode == "2560x1440@165"
    assert s.scale == 1.3
    assert s.position == (0, 0)
    assert s.focus_at_startup is False


def test_parse_dp2():
    b = blocks(REAL_OUTPUT_KDL)["DP-2"]
    s = k.parse_block_settings(REAL_OUTPUT_KDL, b)
    assert s.mode == "2560x1440@180"
    assert s.position == (1970, 0)
    assert s.focus_at_startup is True


def test_slashdash_block_skipped():
    text = '/-output "X-1" { mode "1x1@1" }\noutput "Y-1" {\n}\n'
    found = k.find_output_blocks(text)
    assert [(b.identifier, b.slashdash) for b in found] == [("X-1", True), ("Y-1", False)]


def test_output_in_comment_or_string_skipped():
    text = '// output "fake" { }\n/* output "fake2" { } */\nbinds { spawn "output" }\n'
    assert k.find_output_blocks(text) == []


def edit(text, ident, **kw):
    b = blocks(text)[ident]
    s = k.OutputSettings(**kw)
    new_body = k.edit_block_body(b.body(text), k.settings_to_changes(s))
    return text[: b.open_brace + 1] + new_body + text[b.close_brace :]


def test_surgical_edit_preserves_comments_and_layout():
    s = k.OutputSettings(enabled=True, mode="2560x1440@60.000", scale=1.25,
                         position=(0, 0), vrr="on")
    b = blocks(REAL_OUTPUT_KDL)["eDP-1"]
    body = k.edit_block_body(b.body(REAL_OUTPUT_KDL), k.settings_to_changes(s))
    new = REAL_OUTPUT_KDL[: b.open_brace + 1] + body + REAL_OUTPUT_KDL[b.close_brace :]
    assert '//\toff' in new                      # commented line untouched
    assert 'mode "2560x1440@60.000"' in new
    assert '\tscale 1.25' in new                 # tab indent preserved
    assert 'variable-refresh-rate' in new
    # DP-2 block completely untouched
    assert '//主显示器DP-2' in new
    assert 'mode "2560x1440@180"' in new


def test_trailing_comment_preserved():
    text = 'output "DP-1" {\n    scale 1.0 // my scale\n}\n'
    new = edit(text, "DP-1", scale=2.0)
    assert "scale 2 // my scale" in new


def test_disable_and_reenable():
    disabled = edit(REAL_OUTPUT_KDL, "eDP-1", enabled=False)
    b = blocks(disabled)["eDP-1"]
    assert k.parse_block_settings(disabled, b).enabled is False
    reenabled = edit(disabled, "eDP-1", enabled=True)
    b = blocks(reenabled)["eDP-1"]
    assert k.parse_block_settings(reenabled, b).enabled is True
    assert '//\toff' in reenabled  # the commented one still there


def test_remove_focus_and_vrr():
    new = edit(REAL_OUTPUT_KDL, "DP-2", focus_at_startup=False, vrr="off")
    b = blocks(new)["DP-2"]
    s = k.parse_block_settings(new, b)
    assert s.focus_at_startup is False and s.vrr == "off"


def test_vrr_on_demand():
    new = edit(REAL_OUTPUT_KDL, "DP-2", vrr="on-demand")
    b = blocks(new)["DP-2"]
    assert k.parse_block_settings(new, b).vrr == "on-demand"


def test_transform_add_and_remove():
    new = edit(REAL_OUTPUT_KDL, "DP-2", transform="90")
    b = blocks(new)["DP-2"]
    assert k.parse_block_settings(new, b).transform == "90"
    back = edit(new, "DP-2", transform="normal")
    b = blocks(back)["DP-2"]
    assert k.parse_block_settings(back, b).transform is None


def test_apply_settings_multi_and_new_block(tmp_path):
    cfg = tmp_path / "config.kdl"
    out = tmp_path / "output.kdl"
    cfg.write_text('include optional=true "output.kdl"\n// include "not-this.kdl"\n')
    out.write_text(REAL_OUTPUT_KDL)
    info = k.load_config(cfg)
    assert [p.name for p in info.files] == ["config.kdl", "output.kdl"]
    assert set(info.blocks) == {"eDP-1", "DP-2"}
    assert info.target_file().name == "output.kdl"

    desired = {
        "eDP-1": k.OutputSettings(mode="2560x1440@165.000", scale=1.3, position=(0, 0)),
        "DP-2": k.OutputSettings(mode="2560x1440@180.000", scale=1.0, position=(1969, 0),
                                 focus_at_startup=True),
        "HDMI-A-1": k.OutputSettings(mode="1920x1080@60.000", position=(4529, 0)),
    }
    aliases = {n: [n] for n in desired}
    changed = k.apply_settings(info, desired, aliases)
    assert list(changed) == [out.resolve()]
    text = changed[out.resolve()]
    assert 'output "HDMI-A-1" {' in text
    assert "x=1969" in text
    assert '//主显示器DP-2' in text


def test_alias_matching_make_model_serial(tmp_path):
    cfg = tmp_path / "config.kdl"
    cfg.write_text('output "some make some model 12345" {\n    scale 1\n}\n')
    info = k.load_config(cfg)
    desired = {"DP-3": k.OutputSettings(scale=2.0)}
    aliases = {"DP-3": ["DP-3", "Some Make Some Model 12345"]}
    changed = k.apply_settings(info, desired, aliases)
    assert "scale 2" in changed[cfg.resolve()]
    assert 'output "some make some model 12345"' in changed[cfg.resolve()]


def test_is_included_and_add_include(tmp_path):
    cfg = tmp_path / "config.kdl"
    other = tmp_path / "monitors.kdl"
    cfg.write_text('include "binds.kdl"\nscreenshot-path "x"\n')
    other.write_text("")
    (tmp_path / "binds.kdl").write_text("")
    info = k.load_config(cfg)
    assert not k.is_included(info, other)
    new = k.add_include_line(cfg.read_text(), "monitors.kdl")
    assert new.split("\n")[1] == 'include optional=true "monitors.kdl"'
    cfg.write_text(new)
    info = k.load_config(cfg)
    assert k.is_included(info, other)


def test_include_cycle_no_infinite_loop(tmp_path):
    a = tmp_path / "a.kdl"
    b = tmp_path / "b.kdl"
    a.write_text('include "b.kdl"\n')
    b.write_text('include "a.kdl"\n')
    info = k.load_config(a)
    assert len(info.files) == 2
