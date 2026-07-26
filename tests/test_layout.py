from niri_display_settings.layout import Pending, normalize_positions, reflow_after_resize


def dual() -> dict[str, Pending]:
    # the real-world case: eDP-1 at scale 1.3 (logical 1969x1107), DP-2 right of it
    return {
        "eDP-1": Pending(width=2560, height=1440, scale=1.3, x=0, y=0),
        "DP-2": Pending(width=2560, height=1440, scale=1.0, x=1970, y=0),
    }


def test_scale_down_pushes_right_neighbour():
    p = dual()
    old = p["eDP-1"].logical_size()
    assert old == (1969, 1107)
    p["eDP-1"].scale = 1.0                      # logical grows to 2560
    reflow_after_resize(p, "eDP-1", *old)
    assert p["DP-2"].x == 1970 + (2560 - 1969)  # gap preserved, no overlap


def test_scale_up_pulls_right_neighbour_back():
    p = dual()
    old = p["eDP-1"].logical_size()
    p["eDP-1"].scale = 2.0                      # logical shrinks to 1280
    reflow_after_resize(p, "eDP-1", *old)
    assert p["DP-2"].x == 1970 - (1969 - 1280)


def test_transform_swaps_and_reflows():
    p = dual()
    old = p["eDP-1"].logical_size()
    p["eDP-1"].transform = "90"                 # logical becomes 1107x1969
    reflow_after_resize(p, "eDP-1", *old)
    assert p["DP-2"].x == 1970 - (1969 - 1107)


def test_left_neighbour_unaffected():
    p = dual()
    old = p["DP-2"].logical_size()
    p["DP-2"].scale = 2.0                       # right monitor shrinks
    reflow_after_resize(p, "DP-2", *old)
    assert p["eDP-1"].x == 0                    # left monitor stays put


def test_vertical_reflow():
    p = {
        "A": Pending(width=1920, height=1080, x=0, y=0),
        "B": Pending(width=1920, height=1080, x=0, y=1080),
    }
    old = p["A"].logical_size()
    p["A"].scale = 2.0                          # logical 960x540
    reflow_after_resize(p, "A", *old)
    assert p["B"].y == 540


def test_normalize_shifts_origin():
    p = dual()
    p["eDP-1"].x = -2
    normalize_positions(p)
    assert p["eDP-1"].x == 0 and p["DP-2"].x == 1972


def test_normalize_ignores_disabled():
    p = dual()
    p["eDP-1"].enabled = False
    p["eDP-1"].x = -500
    normalize_positions(p)
    # origin defined by enabled DP-2 only; both shifted together
    assert p["DP-2"].x == 0 and p["eDP-1"].x == -2470
