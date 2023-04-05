"""
pglookout - common utility functions

Copyright (c) 2015 Ohmu Ltd
See LICENSE for details
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

import re


def convert_xlog_location_to_offset(wal_location: str) -> int:
    log_id, offset = wal_location.split("/")
    return int(log_id, 16) << 32 | int(offset, 16)


ISO_EXT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<year>\d{4})-(?P<month>\d\d)-(?P<day>\d\d)(T(?P<hour>\d\d):(?P<minute>\d\d)"
    r"(:(?P<second>\d\d)(.(?P<microsecond>\d{6}))?)?Z)?$"
)
ISO_BASIC_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<year>\d{4})(?P<month>\d\d)(?P<day>\d\d)(T(?P<hour>\d\d)(?P<minute>\d\d)"
    r"((?P<second>\d\d)((?P<microsecond>\d{6}))?)?Z)?$"
)
ISO_GROUP_NAMES: Final[tuple[str, ...]] = (
    "year",
    "month",
    "day",
    "hour",
    "minute",
    "second",
    "microsecond",
)


def parse_iso_datetime(value: str) -> datetime:
    match = ISO_EXT_RE.match(value)
    if not match:
        match = ISO_BASIC_RE.match(value)
    if not match:
        raise ValueError(f"Invalid ISO timestamp {value!r}")
    parts = {key: int(match.group(key) or "0") for key in ISO_GROUP_NAMES}
    return datetime(tzinfo=None, **parts)


def get_iso_timestamp(fetch_time: datetime | None = None) -> str:
    if not fetch_time:
        fetch_time = datetime.utcnow()
    elif (offset := fetch_time.utcoffset()) is not None:
        fetch_time = fetch_time.replace(tzinfo=None) - timedelta(seconds=offset.seconds)
    return fetch_time.isoformat() + "Z"
