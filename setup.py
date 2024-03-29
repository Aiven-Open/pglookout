from setuptools import setup, find_packages
import os
import sys
import version


readme_path = os.path.join(os.path.dirname(__file__), "README.rst")
with open(readme_path, "r") as fp:
    readme_text = fp.read()


version_for_setup_py = version.get_project_version("pglookout/version.py")
version_for_setup_py = ".dev".join(version_for_setup_py.split("-", 2)[:2])


requires = [
    "psycopg2 >= 2.0.0",
    "requests >= 1.2.0",
]

if sys.version_info[0] == 2:
    requires.append("futures")


setup(
    name="pglookout",
    version=version_for_setup_py,
    zip_safe=False,
    packages=find_packages(exclude=["test"]),
    install_requires=requires,
    extras_require={},
    dependency_links=[],
    package_data={},
    data_files=[],
    entry_points={
        "console_scripts": [
            "pglookout = pglookout.pglookout:main",
            "pglookout_current_master = pglookout.current_master:main",
        ],
    },
    author="Hannu Valtonen",
    author_email="hannu.valtonen@aiven.io",
    license="Apache 2.0",
    platforms=["POSIX", "MacOS"],
    description="PostgreSQL replication monitoring and failover daemon",
    long_description=readme_text,
    url="https://github.com/aiven/pglookout/",
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "Intended Audience :: Information Technology",
        "Intended Audience :: System Administrators",
        "License :: OSI Approved :: Apache Software License",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Database :: Database Engines/Servers",
        "Topic :: Software Development :: Libraries",
    ],
)
