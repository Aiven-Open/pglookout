# Development

## Prerequisites

- Python 3.9+
- PostgreSQL 10+ (for integration tests)
- Git

## Getting the Source

```bash
git clone https://github.com/aiven/pglookout.git
cd pglookout
```

## Installing Dev Dependencies

```bash
pip install -r requirements.dev.txt
pip install -e .
```

## Project Structure

```
pglookout/
├── pglookout/                 # Main package
│   ├── __main__.py            # Entry point
│   ├── pglookout.py           # Main daemon (PgLookout class)
│   ├── cluster_monitor.py     # Replication monitoring thread
│   ├── webserver.py           # HTTP state server
│   ├── current_master.py      # CLI helper to query current primary
│   ├── pgutil.py              # Connection string parsing
│   ├── common.py              # Shared utilities
│   ├── statsd.py              # StatsD metrics client
│   └── logutil.py             # Logging and syslog setup
├── test/                      # Test suite
│   ├── conftest.py            # Fixtures (PgLookout instance, TestPG helper)
│   ├── test_lookout.py        # Main daemon tests
│   ├── test_cluster_monitor.py
│   ├── test_common.py
│   ├── test_pgutil.py
│   ├── test_webserver.py
│   └── utils.py               # Test utilities
├── examples/                  # Example scripts
│   ├── failover.sh            # IP-aliasing failover example
│   ├── init_replica.sh        # pg_basebackup replica init
│   └── pglookout_supervisord.conf
├── pglookout.json             # Example configuration
├── pglookout.unit             # systemd unit file
├── Makefile                   # Build and test targets
├── setup.py                   # Package setup
└── pyproject.toml             # Tool configuration
```

## Running Tests

The full test suite runs mypy, flake8, pylint, and pytest:

```bash
make test
```

To run only the pytest unit tests:

```bash
make unittest
```

To generate a coverage report:

```bash
make coverage
```

Some tests require a running PostgreSQL instance. The `db` fixture in `test/conftest.py` automatically initializes a temporary PostgreSQL cluster using `initdb` if PostgreSQL binaries are found on the system.

## Code Quality

| Tool | Command | Purpose |
|------|---------|---------|
| mypy | `make mypy` | Static type checking |
| flake8 | `make flake8` | Style and error linting |
| pylint | `make pylint` | Code analysis |
| black | `make fmt` | Code formatting (with isort) |
| black --check | `make fmt-check` | Verify formatting |

## Building Packages

See [Getting Started](getting-started.md) for building deb, rpm, and egg packages.

Build dependencies can be installed with:

#### Debian

```bash
make build-dep-deb
```

#### Fedora

```bash
make build-dep-fed
```
