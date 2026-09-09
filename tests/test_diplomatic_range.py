"""Regression test for the diplomatic_range formula (calculations/compute_functions.py):
2 x administration + 2 x trade_speed + any otm modifier (e.g. a race trait's
nation_diplomatic_range bonus)."""
from calculations.compute_functions import CUSTOM_COMPUTE_FUNCTIONS


def _compute(administration, trade_speed, otm=None):
    fn = CUSTOM_COMPUTE_FUNCTIONS["diplomatic_range"]
    target = {"administration": administration, "trade_speed": trade_speed}
    return fn("diplomatic_range", target, 0, {}, otm or {})


class TestDiplomaticRangeFormula:
    def test_two_times_admin_plus_two_times_trade_speed(self):
        assert _compute(administration=3, trade_speed=5) == 16  # 2*3 + 2*5

    def test_zero_admin_and_trade_speed_gives_zero(self):
        assert _compute(administration=0, trade_speed=0) == 0

    def test_modifier_is_added_on_top(self):
        assert _compute(administration=3, trade_speed=5, otm={"diplomatic_range": 4}) == 20
