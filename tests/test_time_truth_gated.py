from __future__ import annotations

from healthclaw.agent.response import _safe_time_context_dict
from tests.factories import make_time_context


def _high_conf_ctx():
    return make_time_context(
        timezone_confidence=1.0,
        human_phrasing={
            "now_user_local": "Sunday, 4 May 2026, 8:13 PM (Asia/Colombo, UTC+5:30)",
            "now_short": "Sun 8:13 PM",
            "weekday_user": "Sunday",
            "date_user": "4 May 2026",
            "time_user": "8:13 PM",
            "tz_name": "Asia/Colombo",
            "tz_offset": "UTC+05:30",
            "is_weekend": True,
        },
    )


def _low_conf_ctx():
    return make_time_context(timezone_confidence=0.0)


# ── TimeContext.time_truth_block() ───────────────────────────────────────────


def test_high_confidence_block_contains_now():
    block = _high_conf_ctx().time_truth_block()
    assert "NOW (user's local time):" in block
    assert "8:13 PM" in block


def test_high_confidence_block_has_no_unknown_clause():
    block = _high_conf_ctx().time_truth_block()
    assert "NOT yet confirmed" not in block
    assert "do NOT" not in block.lower().replace("do not re-state", "")


def test_low_confidence_block_has_no_now_value():
    block = _low_conf_ctx().time_truth_block()
    assert "NOW (user's local time):" not in block
    assert "NOW (server time" not in block


def test_low_confidence_block_declares_unknown():
    block = _low_conf_ctx().time_truth_block()
    assert "NOT yet confirmed" in block


def test_low_confidence_block_instructs_no_time_claim():
    block = _low_conf_ctx().time_truth_block()
    assert "Do NOT" in block


def test_boundary_at_exactly_0_6():
    ctx_below = make_time_context(timezone_confidence=0.59)
    ctx_at = make_time_context(
        timezone_confidence=0.60,
        human_phrasing={
            "now_user_local": "Monday, 5 May 2026, 9:00 AM (UTC, UTC+00:00)",
            "weekday_user": "Monday",
            "date_user": "5 May 2026",
            "time_user": "9:00 AM",
            "tz_name": "UTC",
            "tz_offset": "UTC+00:00",
            "is_weekend": False,
        },
    )
    assert "NOT yet confirmed" in ctx_below.time_truth_block()
    assert "NOW (user's local time):" in ctx_at.time_truth_block()


def test_empty_human_phrasing_returns_empty_string():
    ctx = make_time_context(timezone_confidence=1.0, human_phrasing={})
    assert ctx.time_truth_block() == ""


# ── _safe_time_context_dict ──────────────────────────────────────────────────


_TIME_BEARING_KEYS = {
    "local_datetime",
    "local_date",
    "weekday",
    "part_of_day",
    "human_phrasing",
    "circadian_phase",
    "day_arc_position",
}


def test_low_confidence_dict_strips_time_bearing_keys():
    result = _safe_time_context_dict(_low_conf_ctx())
    for key in _TIME_BEARING_KEYS:
        assert key not in result, f"key '{key}' should be absent when confidence is low"


def test_low_confidence_dict_keeps_safe_keys():
    result = _safe_time_context_dict(_low_conf_ctx())
    assert "timezone_confidence" in result
    assert "quiet_hours" in result
    assert "long_lapse" in result


def test_high_confidence_dict_keeps_all_keys():
    result = _safe_time_context_dict(_high_conf_ctx())
    for key in _TIME_BEARING_KEYS:
        assert key in result, f"key '{key}' should be present when confidence is high"
