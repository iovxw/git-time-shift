from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import git_time_shift as gts


UTC = timezone.utc


def test_normalize_date_format_variants() -> None:
    default_spec = gts.normalize_date_format(None)
    assert default_spec.base == "rfc-3339"
    assert default_spec.raw == "rfc-3339"

    blank_spec = gts.normalize_date_format("   ")
    assert blank_spec.base == "rfc-3339"
    assert blank_spec.raw == "rfc-3339"

    short_selector = gts.normalize_date_format("iso-8601")
    assert short_selector.base == "iso-8601"
    assert short_selector.raw == "iso-8601"

    alias_spec = gts.normalize_date_format("rfc2822")
    assert alias_spec.base == "rfc-2822"

    unix_spec = gts.normalize_date_format("unix")
    assert unix_spec.base == "unix"


def test_normalize_date_format_invalid() -> None:
    with pytest.raises(gts.ToolError, match="unsupported --format value"):
        gts.normalize_date_format("rfc-3339=seconds")

    with pytest.raises(gts.ToolError, match="unsupported --format value"):
        gts.normalize_date_format("+%Y-%m-%d %H:%M:%S %:z")


def test_format_offset_and_naive_error() -> None:
    aware = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert gts.format_offset(aware) == "+05:30"
    assert gts.format_offset(aware, include_seconds=True) == "+05:30:00"

    with pytest.raises(gts.ToolError, match="timezone-aware"):
        gts.format_offset(datetime(2024, 1, 2, 3, 4, 5))


def test_standard_round_trips() -> None:
    base_dt = datetime(2024, 2, 3, 4, 5, 6, tzinfo=UTC)

    rfc3339_spec = gts.normalize_date_format("rfc-3339")
    assert gts.format_datetime(base_dt, rfc3339_spec) == "2024-02-03 04:05:06+00:00"
    assert gts.parse_datetime_value("2024-02-03T04:05:06Z", rfc3339_spec).isoformat() == "2024-02-03T04:05:06+00:00"

    iso_spec = gts.normalize_date_format("iso-8601")
    assert gts.format_datetime(base_dt, iso_spec) == "2024-02-03T04:05:06+00:00"
    assert gts.parse_datetime_value("2024-02-03T04:05:06+00:00", iso_spec).isoformat() == "2024-02-03T04:05:06+00:00"

    rfc2822_spec = gts.normalize_date_format("rfc-2822")
    assert gts.format_datetime(base_dt, rfc2822_spec) == "Sat, 03 Feb 2024 04:05:06 +0000"
    assert gts.parse_datetime_value("Sat, 03 Feb 2024 04:05:06 +0000", rfc2822_spec).isoformat() == "2024-02-03T04:05:06+00:00"

    unix_spec = gts.normalize_date_format("unix")
    assert gts.format_datetime(base_dt, unix_spec) == "1706933106"
    assert gts.parse_datetime_value("1706933106", unix_spec).isoformat() == "2024-02-03T04:05:06+00:00"


def test_standard_parse_errors() -> None:
    with pytest.raises(gts.ToolError, match="invalid rfc-3339 timestamp"):
        gts.parse_standard_datetime("not-a-date", gts.DateFormatSpec(raw="rfc-3339", base="rfc-3339"))

    with pytest.raises(gts.ToolError, match="invalid rfc-2822 timestamp"):
        gts.parse_standard_datetime("not-a-date", gts.DateFormatSpec(raw="rfc-2822", base="rfc-2822"))

    with pytest.raises(gts.ToolError, match="invalid unix timestamp"):
        gts.parse_standard_datetime("not-a-date", gts.DateFormatSpec(raw="unix", base="unix"))

    with pytest.raises(gts.ToolError, match="unsupported format"):
        gts.format_standard_datetime(datetime(2024, 1, 1, tzinfo=UTC), gts.DateFormatSpec(raw="x", base="weird"))

    with pytest.raises(gts.ToolError, match="unsupported format"):
        gts.parse_standard_datetime("2024-01-01", gts.DateFormatSpec(raw="x", base="weird"))


def test_month_shift_and_offset_helpers() -> None:
    leap = datetime(2024, 1, 31, 12, 0, 0, tzinfo=UTC)
    assert gts.month_shift(leap, 1).isoformat() == "2024-02-29T12:00:00+00:00"
    assert gts.apply_offset_token(leap, 1, 1, "y").isoformat() == "2025-01-31T12:00:00+00:00"
    assert gts.apply_offset_token(leap, 1, 1, "w").isoformat() == "2024-02-07T12:00:00+00:00"
    assert gts.apply_offset_token(leap, -1, 2, "h").isoformat() == "2024-01-31T10:00:00+00:00"
    assert gts.apply_offset_token(leap, 1, 30, "s").isoformat() == "2024-01-31T12:00:30+00:00"

    tokens = gts.parse_offset_expression("1mo1d3m")
    shifted = gts.apply_offset(leap, tokens)
    assert shifted.isoformat() == "2024-03-01T12:03:00+00:00"

    negative_tokens = gts.parse_offset_expression("-1d2h")
    negative_shifted = gts.apply_offset(leap, negative_tokens)
    assert negative_shifted.isoformat() == "2024-01-30T10:00:00+00:00"


def test_offset_parse_errors_and_invalid_unit() -> None:
    assert gts.parse_offset_expression(None) == []
    assert gts.parse_offset_expression("   ") == []
    assert gts.parse_offset_expression("1d   ") == [(1, 1, "d")]

    with pytest.raises(gts.ToolError, match="invalid offset"):
        gts.parse_offset_expression("tomorrow")

    with pytest.raises(gts.ToolError, match="invalid offset"):
        gts.parse_offset_expression("+1d-2h")

    with pytest.raises(gts.ToolError, match="unsupported offset unit"):
        gts.apply_offset_token(datetime(2024, 1, 1, tzinfo=UTC), 1, 1, "q")
