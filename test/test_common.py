"""
pglookout

Copyright (c) 2015 Ohmu Ltd
See LICENSE for details
"""
from datetime import datetime
from pglookout.common import convert_xlog_location_to_offset, get_iso_timestamp, ISO_EXT_RE, parse_iso_datetime

import pytest


def test_convert_xlog_location_to_offset() -> None:
    assert convert_xlog_location_to_offset("1/00000000") == 1 << 32
    assert convert_xlog_location_to_offset("F/AAAAAAAA") == (0xF << 32) | 0xAAAAAAAA
    with pytest.raises(ValueError):
        convert_xlog_location_to_offset("x")
    with pytest.raises(ValueError):
        convert_xlog_location_to_offset("x/y")


def test_parse_iso_datetime() -> None:
    date = datetime.utcnow()
    date.replace(microsecond=0)
    assert date == parse_iso_datetime(date.isoformat() + "Z")
    with pytest.raises(ValueError):
        parse_iso_datetime("foobar")


def test_get_iso_timestamp() -> None:
    v = get_iso_timestamp()
    assert ISO_EXT_RE.match(v)
    ts = datetime.now()
    v = get_iso_timestamp(ts)
    assert parse_iso_datetime(v) == ts


@pytest.mark.parametrize(
    "timestamp",
    [
        datetime(2021, 1, 1, 23, 42, 11, 123456),
        datetime(2021, 1, 1, 23, 42, 11),
        datetime(2021, 1, 1, 23, 42),
        datetime(2021, 1, 1, 23),
        datetime(2021, 1, 1),
    ],
)
def test_roundtrip(timestamp: datetime) -> None:
    ts2 = parse_iso_datetime(get_iso_timestamp(timestamp))

    assert ts2 == timestamp


@pytest.mark.parametrize(
    ("value", "normalized_value"),
    # fmt: off
    [
        # Extended format
        ("2021-01-01T00:00:00.000000Z",     "2021-01-01T00:00:00Z"),         # noqa: E241
        ("2021-01-01T23:42:11.123456Z",     "2021-01-01T23:42:11.123456Z"),  # noqa: E241
        ("2021-01-01T00:00:00Z",            "2021-01-01T00:00:00Z"),         # noqa: E241
        ("2021-01-01T23:42:11Z",            "2021-01-01T23:42:11Z"),         # noqa: E241
        ("2021-01-01T00:00Z",               "2021-01-01T00:00:00Z"),         # noqa: E241
        ("2021-01-01T23:42Z",               "2021-01-01T23:42:00Z"),         # noqa: E241
        ("2021-01-01",                      "2021-01-01T00:00:00Z"),         # noqa: E241
        # Basic format
        ("20210101T000000Z",                "2021-01-01T00:00:00Z"),         # noqa: E241
        ("20210101T234211123456Z",          "2021-01-01T23:42:11.123456Z"),  # noqa: E241
        ("20210101T000000Z",                "2021-01-01T00:00:00Z"),         # noqa: E241
        ("20210101T234211Z",                "2021-01-01T23:42:11Z"),         # noqa: E241
        ("20210101T0000Z",                  "2021-01-01T00:00:00Z"),         # noqa: E241
        ("20210101T2342Z",                  "2021-01-01T23:42:00Z"),         # noqa: E241
        ("20210101",                        "2021-01-01T00:00:00Z"),         # noqa: E241
    ],
    # fmt: on
)
def test_reverse_roundtrip(value: str, normalized_value: str) -> None:
    v2 = get_iso_timestamp(parse_iso_datetime(value))

    assert v2 == normalized_value
