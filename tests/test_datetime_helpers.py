from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import git_time_shift as gts


UTC = timezone.utc


def test_normalize_date_format_variants() -> None:
    default_spec = gts.normalize_date_format(None)
    assert default_spec.base == "rfc-3339"
    assert default_spec.precision == "seconds"

    blank_spec = gts.normalize_date_format("   ")
    assert blank_spec.base == "rfc-3339"
    assert blank_spec.precision == "seconds"

    short_selector = gts.normalize_date_format("iso-8601")
    assert short_selector.base == "iso-8601"
    assert short_selector.precision == "seconds"

    stripped_spec = gts.normalize_date_format("--iso8601=minutes")
    assert stripped_spec.base == "iso-8601"
    assert stripped_spec.precision == "minutes"

    custom_spec = gts.normalize_date_format("+%Y-%m-%d %H:%M:%S %:z")
    assert custom_spec.kind == "custom"
    assert custom_spec.custom_format == "%Y-%m-%d %H:%M:%S %:z"


def test_normalize_date_format_invalid() -> None:
    with pytest.raises(gts.ToolError, match="unsupported --format value"):
        gts.normalize_date_format("rfc-3339=weeks")

    with pytest.raises(gts.ToolError, match="unsupported --format value"):
        gts.normalize_date_format("not-a-selector")


def test_format_offset_and_naive_error() -> None:
    aware = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert gts.format_offset(aware) == "+05:30"
    assert gts.format_offset(aware, include_seconds=True) == "+05:30:00"

    with pytest.raises(gts.ToolError, match="timezone-aware"):
        gts.format_offset(datetime(2024, 1, 2, 3, 4, 5))


def test_standard_round_trips() -> None:
    base_dt = datetime(2024, 2, 3, 4, 5, 6, 123456, tzinfo=UTC)

    date_spec = gts.normalize_date_format("rfc-3339=date")
    assert gts.format_datetime(base_dt, date_spec) == "2024-02-03"
    assert gts.parse_datetime_value("2024-02-03", date_spec).tzinfo == gts.LOCAL_TZ

    hours_spec = gts.normalize_date_format("iso-8601=hours")
    assert gts.format_datetime(base_dt, hours_spec) == "2024-02-03T04+00:00"
    assert gts.parse_datetime_value("2024-02-03T04+00:00", hours_spec).isoformat() == "2024-02-03T04:00:00+00:00"

    minutes_spec = gts.normalize_date_format("iso-8601=minutes")
    assert gts.format_datetime(base_dt, minutes_spec) == "2024-02-03T04:05+00:00"
    assert gts.parse_datetime_value("2024-02-03T04:05+00:00", minutes_spec).isoformat() == "2024-02-03T04:05:00+00:00"

    seconds_spec = gts.normalize_date_format("rfc-3339=seconds")
    rendered = gts.format_datetime(base_dt, seconds_spec)
    assert rendered == "2024-02-03 04:05:06+00:00"
    assert gts.parse_datetime_value("2024-02-03T04:05:06Z", seconds_spec).isoformat() == "2024-02-03T04:05:06+00:00"

    ns_spec = gts.normalize_date_format("iso-8601=ns")
    ns_text = gts.format_datetime(base_dt, ns_spec)
    assert ns_text == "2024-02-03T04:05:06.123456000+00:00"
    assert gts.parse_datetime_value(ns_text, ns_spec).isoformat() == "2024-02-03T04:05:06.123456+00:00"


def test_standard_parse_errors() -> None:
    with pytest.raises(gts.ToolError, match="invalid nanosecond timestamp"):
        gts.parse_standard_datetime("not-a-date", gts.DateFormatSpec(raw="iso-8601=ns", kind="selector", base="iso-8601", precision="ns"))

    with pytest.raises(gts.ToolError, match="unsupported precision"):
        gts.format_standard_datetime(datetime(2024, 1, 1, tzinfo=UTC), gts.DateFormatSpec(raw="x", kind="selector", base="iso-8601", precision="weird"))

    with pytest.raises(gts.ToolError, match="unsupported precision"):
        gts.parse_standard_datetime("2024-01-01", gts.DateFormatSpec(raw="x", kind="selector", base="iso-8601", precision="weird"))


def test_custom_round_trips_and_normalization() -> None:
    dt = datetime(2024, 3, 4, 5, 6, 7, 123456, tzinfo=UTC)
    custom = gts.normalize_date_format("+%Y-%m-%d %H:%M:%S.%N %:z")
    rendered = gts.format_datetime(dt, custom)
    assert rendered == "2024-03-04 05:06:07.123456000 +00:00"
    parsed = gts.parse_datetime_value(rendered, custom)
    assert parsed.isoformat() == "2024-03-04T05:06:07.123456+00:00"

    custom_seconds = gts.normalize_date_format("+%Y-%m-%d %H:%M:%S %::z")
    rendered_seconds = gts.format_datetime(dt, custom_seconds)
    assert rendered_seconds == "2024-03-04 05:06:07 +00:00:00"
    parsed_seconds = gts.parse_datetime_value(rendered_seconds, custom_seconds)
    assert parsed_seconds.isoformat() == "2024-03-04T05:06:07+00:00"


def test_custom_parse_without_timezone_uses_local_tz() -> None:
    parsed = gts.parse_custom_datetime("2024-03-04 05:06:07", "%Y-%m-%d %H:%M:%S")
    assert parsed.tzinfo == gts.LOCAL_TZ


def test_custom_fraction_errors() -> None:
    with pytest.raises(gts.ToolError, match="no fractional seconds"):
        gts.normalize_fractional_seconds("2024-01-01T00:00:00+00:00")


def test_month_shift_and_offset_helpers() -> None:
    leap = datetime(2024, 1, 31, 12, 0, 0, tzinfo=UTC)
    assert gts.month_shift(leap, 1).isoformat() == "2024-02-29T12:00:00+00:00"
    assert gts.apply_offset_token(leap, 1, 1, "y").isoformat() == "2025-01-31T12:00:00+00:00"
    assert gts.apply_offset_token(leap, 1, 1, "w").isoformat() == "2024-02-07T12:00:00+00:00"
    assert gts.apply_offset_token(leap, -1, 2, "h").isoformat() == "2024-01-31T10:00:00+00:00"
    assert gts.apply_offset_token(leap, 1, 30, "s").isoformat() == "2024-01-31T12:00:30+00:00"

    tokens = gts.parse_offset_expression(["+1d", "-2h", "+3m", "+1mo"])
    shifted = gts.apply_offset(leap, tokens)
    assert shifted.isoformat() == "2024-03-01T10:03:00+00:00"


def test_offset_parse_errors_and_invalid_unit() -> None:
    assert gts.parse_offset_expression([]) == []
    assert gts.parse_offset_expression(["+1d", "   "]) == [(1, 1, "d")]
    assert gts.parse_offset_expression(["+1d   "]) == [(1, 1, "d")]

    with pytest.raises(gts.ToolError, match="invalid offset"):
        gts.parse_offset_expression(["tomorrow"])

    with pytest.raises(gts.ToolError, match="unsupported offset unit"):
        gts.apply_offset_token(datetime(2024, 1, 1, tzinfo=UTC), 1, 1, "q")
