# Copyright (c) 2023 Aiven, Helsinki, Finland. https://aiven.io/
from typing import Final

WARNING_REPLICATION_TIME_LAG: Final[float] = 30.0
MAX_FAILOVER_REPLICATION_TIME_LAG: Final[float] = 120.0
REPLICATION_CATCHUP_TIMEOUT: Final[float] = 300.0
MISSING_MASTER_FROM_CONFIG_TIMEOUT: Final[float] = 15.0
MAINTENANCE_MODE_FILE: Final[str] = "/tmp/pglookout_maintenance_mode_file"
