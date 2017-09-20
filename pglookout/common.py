"""
pglookout - common utility functions

Copyright (c) 2015 Ohmu Ltd
See LICENSE for details
"""
import datetime
import re


def convert_xlog_location_to_offset(wal_location):
    log_id, offset = wal_location.split("/")
    return int(log_id, 16) << 32 | int(offset, 16)


ISO_EXT_RE = re.compile(r'(?P<year>\d{4})-(?P<month>\d\d)-(?P<day>\d\d)(T(?P<hour>\d\d):(?P<minute>\d\d)'
                        r'(:(?P<second>\d\d)(.(?P<microsecond>\d{6}))?)?Z)?$')
ISO_BASIC_RE = re.compile(r'(?P<year>\d{4})(?P<month>\d\d)(?P<day>\d\d)(T(?P<hour>\d\d)(?P<minute>\d\d)'
                          r'((?P<second>\d\d)((?P<microsecond>\d{6}))?)?Z)?$')


def parse_iso_datetime(value):
    match = ISO_EXT_RE.match(value)
    if not match:
        match = ISO_BASIC_RE.match(value)
    if not match:
        raise ValueError("Invalid ISO timestamp {0!r}".format(value))
    parts = dict((key, int(match.group(key) or '0'))
                 for key in ('year', 'month', 'day', 'hour', 'minute', 'second', 'microsecond'))
    return datetime.datetime(tzinfo=None, **parts)


def get_iso_timestamp(fetch_time=None):
    if not fetch_time:
        fetch_time = datetime.datetime.utcnow()
    elif fetch_time.tzinfo:
        fetch_time = fetch_time.replace(tzinfo=None) - datetime.timedelta(seconds=fetch_time.utcoffset().seconds)
    return fetch_time.isoformat() + "Z"
