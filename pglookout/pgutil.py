# Copied from https://github.com/ohmu/ohmu_common_py ohmu_common_py/pgutil.py version 0.0.1-0-unknown-fa54b44
"""
pglookout - postgresql utility functions

Copyright (c) 2015 Ohmu Ltd
See LICENSE for details
"""
from __future__ import annotations

from typing import cast, Literal, TypedDict
from urllib.parse import parse_qs, urlparse  # pylint: disable=no-name-in-module, import-error

import psycopg2.extensions


class DsnDictBase(TypedDict, total=False):
    user: str
    password: str
    host: str
    port: str | int


class DsnDict(DsnDictBase, total=False):
    dbname: str


class DsnDictDeprecated(DsnDictBase, total=False):
    database: str


class ConnectionParameterKeywords(TypedDict, total=False):
    """Parameter Keywords for Connection.

    See:
        https://www.postgresql.org/docs/current/libpq-connect.html#LIBPQ-PARAMKEYWORDS
    """

    host: str
    hostaddr: str
    port: str
    dbname: str
    user: str
    password: str
    passfile: str
    channel_binding: Literal["require", "prefer", "disable"]
    connect_timeout: str
    client_encoding: str
    options: str
    application_name: str
    fallback_application_name: str
    keepalives: Literal["0", "1"]
    keepalives_idle: str
    keepalives_interval: str
    keepalives_count: str
    tcp_user_timeout: str
    replication: Literal["true", "on", "yes", "1", "database", "false", "off", "no", "0"]
    gssencmode: Literal["disable", "prefer", "require"]
    sslmode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]
    requiressl: Literal["0", "1"]
    sslcompression: Literal["0", "1"]
    sslcert: str
    sslkey: str
    sslpassword: str
    sslrootcert: str
    sslcrl: str
    sslcrldir: str
    sslsni: Literal["0", "1"]
    requirepeer: str
    ssl_min_protocol_version: Literal["TLSv1", "TLSv1.1", "TLSv1.2", "TLSv1.3"]
    ssl_max_protocol_version: Literal["TLSv1", "TLSv1.1", "TLSv1.2", "TLSv1.3"]
    krbsrvname: str
    gsslib: str
    service: str
    target_session_attrs: Literal["any", "read-write", "read-only", "primary", "standby", "prefer-standby"]


def create_connection_string(connection_info: DsnDict | DsnDictDeprecated | ConnectionParameterKeywords) -> str:
    return str(psycopg2.extensions.make_dsn(**connection_info))


def mask_connection_info(info: str) -> str:
    masked_info = get_connection_info(info)
    password = masked_info.pop("password", None)
    connection_string = create_connection_string(masked_info)
    message = "no password" if password is None else "hidden password"
    return f"{connection_string}; {message}"


def get_connection_info_from_config_line(line: str) -> ConnectionParameterKeywords:
    _, value = line.split("=", 1)
    value = value.strip()[1:-1].replace("''", "'")
    return get_connection_info(value)


def get_connection_info(
    info: str | DsnDict | DsnDictDeprecated | ConnectionParameterKeywords,
) -> ConnectionParameterKeywords:
    """Get a normalized connection info dict from a connection string or a dict.

    Supports both the traditional libpq format and the new url format.
    """
    if isinstance(info, dict):
        # Potentially, we might clean deprecated DSN dicts: `database` -> `dbname`.
        # Also, psycopg2 will validate the keys and values.
        return parse_connection_string_libpq(create_connection_string(info))
    if info.startswith("postgres://") or info.startswith("postgresql://"):
        return parse_connection_string_url(info)
    return parse_connection_string_libpq(info)


def parse_connection_string_url(url: str) -> ConnectionParameterKeywords:
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
    return cast(ConnectionParameterKeywords, fields)


def parse_connection_string_libpq(connection_string: str) -> ConnectionParameterKeywords:
    """Parse a postgresql connection string.

    See:
        http://www.postgresql.org/docs/current/static/libpq-connect.html#LIBPQ-CONNSTRING
    """
    fields = {}
    while True:
        connection_string = connection_string.strip()
        if not connection_string:
            break
        if "=" not in connection_string:
            raise ValueError(f"expecting key=value format in connection_string fragment {connection_string!r}")
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
                raise ValueError(f"invalid connection_string fragment {rem!r}")
            connection_string = rem[i + 1 :]  # pylint: disable=undefined-loop-variable
        else:
            res = rem.split(None, 1)
            if len(res) > 1:
                value, connection_string = res
            else:
                value, connection_string = rem, ""
        # This one is case-insensitive. To continue benefiting from mypy, we make it lowercase.
        if key == "replication":
            value = value.lower()
        fields[key] = value
    return cast(ConnectionParameterKeywords, fields)
