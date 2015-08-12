"""
pglookout

Copyright (c) 2015 Ohmu Ltd
See LICENSE for details
"""

from pglookout.common import get_connection_info


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
