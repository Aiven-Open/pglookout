"""
pglookout

Copyright (c) 2015 Ohmu Ltd
See LICENSE for details
"""

from pglookout.common import (
    convert_xlog_location_to_offset,
    parse_iso_datetime, get_iso_timestamp, ISO_EXT_RE,
)
from pytest import raises
import datetime


def test_convert_xlog_location_to_offset():
    assert convert_xlog_location_to_offset("1/00000000") == 1 << 32
    assert convert_xlog_location_to_offset("F/AAAAAAAA") == (0xF << 32) | 0xAAAAAAAA
    with raises(ValueError):
        convert_xlog_location_to_offset("x")
    with raises(ValueError):
        convert_xlog_location_to_offset("x/y")


def test_parse_iso_datetime():
    date = datetime.datetime.utcnow()
    date.replace(microsecond=0)
    assert date == parse_iso_datetime(date.isoformat() + "Z")
    with raises(ValueError):
        parse_iso_datetime("foobar")


def test_get_iso_timestamp():
    v = get_iso_timestamp()
    assert ISO_EXT_RE.match(v)
    ts = datetime.datetime.now()
    v = get_iso_timestamp(ts)
    assert parse_iso_datetime(v) == ts
