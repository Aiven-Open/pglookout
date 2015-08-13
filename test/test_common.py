"""
pglookout

Copyright (c) 2015 Ohmu Ltd
See LICENSE for details
"""

from pglookout.common import (
    get_connection_info, convert_xlog_location_to_offset,
    parse_iso_datetime, get_iso_timestamp, ISO_EXT_RE)
from pytest import raises
import datetime


def test_connection_info():
    url = "postgres://hannu:secret@dbhost.local:5555/abc?replication=true&sslmode=foobar&sslmode=require"
    cs = "host=dbhost.local user='hannu'   dbname='abc'\n" \
         "replication=true   password=secret sslmode=require port=5555"
    ci = {
        "host": "dbhost.local",
        "port": "5555",
        "user": "hannu",
        "password": "secret",
        "dbname": "abc",
        "replication": "true",
        "sslmode": "require",
        }
    assert get_connection_info(ci) == get_connection_info(cs)
    assert get_connection_info(ci) == get_connection_info(url)


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
