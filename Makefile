short_ver = 2.1.0
long_ver = $(shell git describe --long 2>/dev/null || echo $(short_ver)-0-unknown-g`git describe --always`)
generated = pglookout/version.py

PYTHON ?= python3
PYTHON_SOURCE_DIRS = pglookout/ test/ stubs/ version.py setup.py

all: $(generated)
	: 'try "make rpm" or "make deb" or "make test"'

pglookout/version.py: version.py
	$(PYTHON) $^ $@

test: mypy flake8 pylint unittest

test-dep:
	$(PYTHON) -m pip install -r requirements.dev.txt
	touch $@

unittest: $(generated) test-dep
	$(PYTHON) -m pytest

mypy: $(generated) test-dep
	MYPYPATH=stubs $(PYTHON) -m mypy

flake8: $(generated) test-dep
	$(PYTHON) -m flake8 $(PYTHON_SOURCE_DIRS)

pylint: $(generated) test-dep
	$(PYTHON) -m pylint $(PYTHON_SOURCE_DIRS)

fmt: $(generated) test-dep
	isort $(PYTHON_SOURCE_DIRS)
	black $(PYTHON_SOURCE_DIRS)

fmt-check: $(generated) test-dep
	isort --check $(PYTHON_SOURCE_DIRS)
	black --check $(PYTHON_SOURCE_DIRS)

coverage: test-dep
	$(PYTHON) -m pytest $(PYTEST_ARG) --cov-report term-missing --cov-branch \
		--cov-report xml:coverage.xml --cov pglookout test/

clean:
	$(RM) -r *.egg-info/ build/ dist/
	$(RM) ../pglookout_* test-*.xml $(generated) test-dep

deb: $(generated) test
	cp debian/changelog.in debian/changelog
	dch -v $(long_ver) "Automatically built package"
	dpkg-buildpackage -A -uc -us

rpm: $(generated) test
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
		python3-devel python3-pytest python3-pylint \
		python3-mock python3-psycopg2 \
		python3-requests rpm-build systemd-python3 \
		python3-flake8 python3-pytest-cov python3-packaging python-mypy

build-dep-deb:
	sudo apt-get install \
		build-essential devscripts dh-systemd \
		python3-all python3-setuptools python3-psycopg2 python3-requests \
		python3-packaging

.PHONY: rpm
