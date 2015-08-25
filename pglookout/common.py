"""
pglookout - common utility functions

Copyright (c) 2015 Ohmu Ltd
See LICENSE for details
"""

import datetime
import logging
import re
try:
    from urllib.parse import urlparse, parse_qs  # pylint: disable=no-name-in-module, import-error
except ImportError:
    from urlparse import urlparse, parse_qs  # pylint: disable=no-name-in-module, import-error


LOG_FORMAT = "%(asctime)s\t%(name)s\t%(levelname)s\t%(message)s"
LOG_FORMAT_SYSLOG = '%(name)s %(levelname)s: %(message)s'


def create_connection_string(connection_info):
    return " ".join("{0}='{1}'".format(k, str(v).replace("'", "\\'"))
                    for k, v in sorted(connection_info.items()))


def get_connection_info_from_config_line(line):
    _, value = line.split("=", 1)
    value = value.strip()[1:-1].replace("''", "'")
    return get_connection_info(value)


def get_connection_info(info):
    """turn a connection info object into a dict or return it if it was a
    dict already.  supports both the traditional libpq format and the new
    url format"""
    if isinstance(info, dict):
        return info.copy()
    elif info.startswith("postgres://") or info.startswith("postgresql://"):
        return parse_connection_string_url(info)
    else:
        return parse_connection_string_libpq(info)


def parse_connection_string_url(url):
    # drop scheme from the url as some versions of urlparse don't handle
    # query and path properly for urls with a non-http scheme
    schemeless_url = url.split(":", 1)[1]
    p = urlparse(schemeless_url)
    fields = {}
    if p.hostname:
        fields["host"] = p.hostname
    if p.port:
        fields["port"] = str(p.port)
    if p.username:
        fields["user"] = p.username
    if p.password is not None:
        fields["password"] = p.password
    if p.path and p.path != "/":
        fields["dbname"] = p.path[1:]
    for k, v in parse_qs(p.query).items():
        fields[k] = v[-1]
    return fields


def parse_connection_string_libpq(connection_string):
    """parse a postgresql connection string as defined in
    http://www.postgresql.org/docs/current/static/libpq-connect.html#LIBPQ-CONNSTRING"""
    fields = {}
    while True:
        connection_string = connection_string.strip()
        if not connection_string:
            break
        if "=" not in connection_string:
            raise ValueError("expecting key=value format in connection_string fragment {!r}".format(connection_string))
        key, rem = connection_string.split("=", 1)
        if rem.startswith("'"):
            asis, value = False, ""
            for i in range(1, len(rem)):
                if asis:
                    value += rem[i]
                    asis = False
                elif rem[i] == "'":
                    break  # end of entry
                elif rem[i] == "\\":
                    asis = True
                else:
                    value += rem[i]
            else:
                raise ValueError("invalid connection_string fragment {!r}".format(rem))
            connection_string = rem[i + 1:]  # pylint: disable=undefined-loop-variable
        else:
            res = rem.split(None, 1)
            if len(res) > 1:
                value, connection_string = res
            else:
                value, connection_string = rem, ""
        fields[key] = value
    return fields


def convert_xlog_location_to_offset(xlog_location):
    log_id, offset = xlog_location.split("/")
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


def set_syslog_handler(syslog_address, syslog_facility, logger):
    syslog_handler = logging.handlers.SysLogHandler(address=syslog_address, facility=syslog_facility)
    logger.addHandler(syslog_handler)
    formatter = logging.Formatter(LOG_FORMAT_SYSLOG)
    syslog_handler.setFormatter(formatter)
    return syslog_handler
