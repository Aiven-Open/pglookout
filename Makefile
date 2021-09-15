short_ver = 2.0.0
long_ver = $(shell git describe --long 2>/dev/null || echo $(short_ver)-0-unknown-g`git describe --always`)
generated = pglookout/version.py

PYTHON ?= python3
PYLINT_DIRS = pglookout/ test/

all: $(generated)
	: 'try "make rpm" or "make deb" or "make test"'

pglookout/version.py: version.py
	$(PYTHON) $^ $@

test: flake8 pylint unittest

unittest: $(generated)
	$(PYTHON) -m pytest -vv test/

flake8: $(generated)
	$(PYTHON) -m flake8 --ignore E722 --max-line-len=125 $(PYLINT_DIRS)

pylint: $(generated)
	$(PYTHON) -m pylint --rcfile .pylintrc $(PYLINT_DIRS)

coverage:
	$(PYTHON) -m pytest $(PYTEST_ARG) --cov-report term-missing --cov pglookout test/

clean:
	$(RM) -r *.egg-info/ build/ dist/
	$(RM) ../pglookout_* test-*.xml $(generated)

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
		python3-devel python3-pytest python3-pylint \
		python3-mock python3-psycopg2 \
		python3-requests rpm-build systemd-python3 \
		python3-flake8 python3-pytest-cov

build-dep-deb:
	sudo apt-get install \
		build-essential devscripts dh-systemd \
		python3-all python3-setuptools python3-psycopg2 python3-requests

.PHONY: rpm
