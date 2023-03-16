# Copyright (c) 2023 Aiven, Helsinki, Finland. https://aiven.io/

from __future__ import annotations

from typing import AnyStr, IO

def notify(
    status: str,
    unset_environment: bool = False,
    pid: int = 0,
    fds: IO[AnyStr] | None = None,
) -> bool:
    """
    notify(status, unset_environment=False, pid=0, fds=None) -> bool

    Send a message to the init system about a status change.
    Wraps sd_notify(3).
    """
    ...
