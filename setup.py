from setuptools import setup, find_packages
from pglookout import __version__
import os

setup(
    name = "pglookout",
    version = os.getenv("VERSION") or __version__,
    zip_safe = False,
    packages = find_packages(exclude=["test"]),
    install_requires = ['psycopg2 >= 2.0.0', 'requests >= 1.2.0'],
    extras_require = {},
    dependency_links = [],
    package_data = {},
    data_files = [],
    entry_points = {
        'console_scripts': ["pglookout = pglookout.pglookout:main",
                            "pglookout_current_master = pglookout.current_master:main"],
    }
)
