short_ver = 2.1.0
long_ver = $(shell git describe --long 2>/dev/null || echo $(short_ver)-0-unknown-g`git describe --always`)
generated = pglookout/version.py

VENV = .venv-test
VENV_PYTHON = $(VENV)/bin/python
PYTHON ?= python3
PYTHON_SOURCE_DIRS = pglookout/ test/ stubs/ version.py setup.py

all: $(generated)
	: 'try "make rpm" or "make deb" or "make test"'

pglookout/version.py: version.py
	$(PYTHON) $^ $@

# This venv is only used for tests and development. It has access to system site packages, because pglookout
# would need them to run. Development deps are kept in this venv, so that they don't interfere with the
# system python.
$(VENV):
	$(PYTHON) -m venv --system-site-packages $(VENV)
	$(VENV)/bin/pip install -r requirements.dev.txt

local-install: $(VENV)
	$(VENV_PYTHON) -m pip install -e .

test: mypy flake8 pylint unittest fmt-check

unittest: $(generated) $(VENV)
	$(VENV_PYTHON) -m pytest

mypy: $(generated) $(VENV)
	MYPYPATH=stubs $(VENV_PYTHON) -m mypy

flake8: $(generated) $(VENV)
	$(VENV_PYTHON) -m flake8 $(PYTHON_SOURCE_DIRS)

pylint: $(generated) $(VENV)
	$(VENV_PYTHON) -m pylint $(PYTHON_SOURCE_DIRS)

fmt: $(generated) $(VENV)
	$(VENV_PYTHON) -m isort $(PYTHON_SOURCE_DIRS)
	$(VENV_PYTHON) -m black $(PYTHON_SOURCE_DIRS)

fmt-check: $(generated) $(VENV)
	$(VENV_PYTHON) -m isort --check $(PYTHON_SOURCE_DIRS)
	$(VENV_PYTHON) -m black --check $(PYTHON_SOURCE_DIRS)

coverage: $(VENV)
	$(VENV_PYTHON) -m pytest $(PYTEST_ARG) --cov-report term-missing --cov-branch \
		--cov-report xml:coverage.xml --cov pglookout test/

clean:
	$(RM) -r *.egg-info/ build/ dist/ $(VENV) .hypothesis
	$(RM) ../pglookout_* test-*.xml $(generated) .coverage coverage.xml

deb: $(generated)
	cp debian/changelog.in debian/changelog
	dch -v $(long_ver) "Automatically built package"
	dpkg-buildpackage -A -uc -us

rpm: $(generated)
	git archive --output=pglookout-rpm-src.tar --prefix=pglookout/ HEAD
	# add generated files to the tar, they're not in git repository
	tar -r -f pglookout-rpm-src.tar --transform=s,pglookout/,pglookout/pglookout/, $(generated)
	rpmbuild -bb pglookout.spec \
		--define '_topdir $(PWD)/rpm' \
		--define '_sourcedir $(shell pwd)' \
		--define 'major_version $(short_ver)' \
		--define 'minor_version $(subst -,.,$(subst $(short_ver)-,,$(long_ver)))'
	$(RM) pglookout-rpm-src.tar

build-dep-fed:
	sudo dnf -y install --best --allowerasing \
		python3-devel python3-psycopg2 python3-requests \
		rpm-build systemd-python3 python3-packaging

build-dep-deb:
	sudo apt-get install \
		build-essential devscripts dh-systemd \
		python3-all python3-setuptools python3-psycopg2 python3-requests \
		python3-packaging

.PHONY: rpm local-install
