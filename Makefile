short_ver = 1.2.0
long_ver = $(shell git describe --long 2>/dev/null || echo $(short_ver)-0-unknown-g`git describe --always`)
generated = pglookout/version.py

PYTHON ?= python
PYLINT_DIRS = pglookout/ test/

all: $(generated)
	: 'try "make rpm" or "make deb" or "make test"'

pglookout/version.py: version.py
	$(PYTHON) $^ $@

test: pylint unittest

unittest: $(generated)
	$(PYTHON) -m pytest -vv test/

pylint: $(generated)
	$(PYTHON) -m pylint.lint --rcfile .pylintrc $(PYLINT_DIRS)

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
		--define '_sourcedir $(shell pwd)' \
		--define 'major_version $(short_ver)' \
		--define 'minor_version $(subst -,.,$(subst $(short_ver)-,,$(long_ver)))'
	$(RM) pglookout-rpm-src.tar

build-dep-fed:
	sudo yum -y install \
		python-devel python3-devel pytest python3-pytest pylint python3-pylint \
		python-mock python3-mock python-psycopg2 python3-psycopg2 python-pytest-cov \
		python-requests python3-requests rpm-build systemd-python systemd-python3 \
		python-futures

build-dep-deb:
	sudo apt-get install \
		build-essential devscripts dh-systemd \
		python-all python-setuptools python-psycopg2 python-requests

pep8:
	$(PYTHON) -m pep8 --ignore=E501,E123 $(PYLINT_DIRS)

