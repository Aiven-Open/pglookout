# Copied from https://github.com/ohmu/ohmu_common_py version.py version 0.0.1-0-unknown-fa54b44
"""
pglookout - version detection and version.py __version__ generation

Copyright (c) 2015 Ohmu Ltd
See LICENSE for details
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from textwrap import dedent
from typing import Final

import imp  # pylint: disable=deprecated-module
import subprocess

ROOT_DIR: Final[Path] = Path(__file__).parent


def save_version(new_ver: str, old_ver: str | None, version_file: Path) -> bool:
    """Write new version to the version file if it has changed."""
    if not new_ver:
        return False

    if not old_ver or new_ver != old_ver:
        version_file.write_text(
            dedent(
                f"""\
        # Copyright (c) {datetime.now():%Y} Aiven, Helsinki, Finland. https://aiven.io/
        __version__ = '{new_ver}'
        """
            )
        )

    return True


def get_existing_version(version_file: Path) -> str | None:
    """Read version from the version file"""
    try:
        module = imp.load_source("verfile", str(version_file))
        return str(module.__version__)
    except (IOError, AttributeError):
        return None


def get_makefile_version(makefile_path: Path) -> str | None:
    """Read version from ``Makefile``."""
    if makefile_path.is_file():
        lines = makefile_path.read_text().splitlines()
        try:
            it_short_ver = (line.split("=", 1)[1].strip() for line in lines if line.startswith("short_ver"))
            return next(it_short_ver)
        except StopIteration:
            pass
    return None


def get_git_version(repo_dir: Path) -> str | None:
    """Read version from git."""
    try:
        git_out = subprocess.check_output(
            ["git", "describe", "--always"], cwd=repo_dir, stderr=getattr(subprocess, "DEVNULL", None)
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    git_ver = git_out.splitlines()[0].strip().decode("utf-8")
    if "." not in git_ver:
        git_ver = f"0.0.1-0-unknown-{git_ver}"

    return git_ver


def get_project_version(version_file_name: str) -> str:
    """Read version from git or from the version file"""
    version_file = ROOT_DIR / version_file_name
    file_ver = get_existing_version(version_file)

    git_ver = get_git_version(ROOT_DIR)
    if git_ver and save_version(git_ver, file_ver, version_file):
        return git_ver

    makefile = ROOT_DIR / "Makefile"
    short_ver = get_makefile_version(makefile)
    if short_ver and save_version(short_ver, file_ver, version_file):
        return short_ver

    if not file_ver:
        raise Exception(f"version not available from git or from file {version_file!r}")

    return file_ver


if __name__ == "__main__":
    import sys

    get_project_version(sys.argv[1])
