"""A 240p CRT hides the top and bottom of the picture unless told otherwise.

The header and footer were unreadable on a YPbPr set at the old default of 0.
Blank in the config means "never calibrated" and gets the CRT safe-area
default on a 240p display; an explicit value, including 0, is kept.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync import config as gsconfig  # noqa: E402


def test_uncalibrated_240p_gets_the_crt_default():
    config = gsconfig.Config()
    assert not config.overscan_is_set
    assert config.overscan_for(lowres=True) == gsconfig.CRT_DEFAULT_OVERSCAN
    assert config.overscan_for(lowres=False) == (0.0, 0.0)


def test_explicit_zero_is_a_choice_not_a_blank():
    config = gsconfig.Config(overscan_x="0", overscan_y="0")
    assert config.overscan_is_set
    assert config.overscan_for(lowres=True) == (0.0, 0.0)


def test_blank_round_trips_through_the_config_text():
    config = gsconfig.Config(server_url="http://x", api_key="k")
    values = gsconfig._parse(config.to_text())
    assert values["OVERSCAN_X"] == "" and values["OVERSCAN_Y"] == ""
    again = gsconfig.Config(overscan_x=values["OVERSCAN_X"],
                            overscan_y=values["OVERSCAN_Y"])
    assert not again.overscan_is_set

    config.overscan_x, config.overscan_y = 3.5, 6.0
    values = gsconfig._parse(config.to_text())
    assert (values["OVERSCAN_X"], values["OVERSCAN_Y"]) == ("3.5", "6")


def test_legacy_zero_in_an_old_config_is_kept():
    """Configs written before this change say OVERSCAN_X=0 explicitly; that
    was a saved calibration on the cabinet and must not become 5%/8%."""
    config = gsconfig.Config(overscan_x="0", overscan_y="0")
    assert config.overscan_for(lowres=True) == (0.0, 0.0)
