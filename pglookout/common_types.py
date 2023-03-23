# Copyright (c) 2023 Aiven, Helsinki, Finland. https://aiven.io/
from __future__ import annotations

from datetime import datetime
from typing import TypedDict, Union


class ReplicationSlotAsDict(TypedDict, total=True):
    slot_name: str
    plugin: str
    slot_type: str
    database: str
    catalog_xmin: str
    restart_lsn: str
    confirmed_flush_lsn: str
    state_data: str


class MemberState(TypedDict, total=False):
    """Represents the state of a member of the cluster.

    Note:
        This is a very loose type as no key is mandatory. This is because
        it is too dangerous to impose a stricter type until we have a
        better test coverage, as it would change some behaviour in the
        code (some unconventional behaviour was detected, and it may be a
        bug or a feature).
    """

    # Connection Status
    connection: bool
    fetch_time: str
    # Queried Status
    db_time: str | datetime
    pg_is_in_recovery: bool
    pg_last_xact_replay_timestamp: datetime | str | None
    pg_last_xlog_receive_location: str | None
    pg_last_xlog_replay_location: str | None
    # Replication info
    replication_slots: list[ReplicationSlotAsDict]
    replication_time_lag: float | None
    min_replication_time_lag: float
    replication_start_time: float | None


# Note for future improvements:
# If we want ObservedState to accept arbitrary keys, we have three choices:
# - Use a different type (pydantic, dataclasses, etc.)
# - Use a TypedDict for static keys (connection, fetch_time) and a sub-dict for
#   dynamic keys (received from state.json).
# - Wait for something like `allow_extra` to be implemented into TypedDict (unlikely)
#   https://github.com/python/mypy/issues/4617
class ObservedState(dict[str, Union[MemberState, bool, str]]):
    """Represents an observed state, from the perspective of an observer.

    Note:
        The content of this type is dynamic, as it depends on the number of
        members in the cluster. There are two static keys, connection and
        fetch_time, and N dynamic keys, one for each member of the cluster.
        Like so::

            connection: bool
            fetch_time: str
            name_or_ip_1: MemberState
            name_or_ip_2: MemberState
            ...
            name_or_ip_N: MemberState
    """
