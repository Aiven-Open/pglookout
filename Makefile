short_ver = 1.2.0
long_ver = $(shell git describe --long 2>/dev/null || echo $(short_ver)-0-unknown-g`git describe --always`)

PYTHON ?= python
PYLINT_DIRS = pglookout/ test/

all:
	: 'try "make rpm" or "make deb" or "make test"'

test: pylint unittest

unittest:
	$(PYTHON) -m pytest -vv test/

pylint:
	$(PYTHON) -m pylint.lint --rcfile .pylintrc $(PYLINT_DIRS)

coverage:
	$(PYTHON) -m pytest $(PYTEST_ARG) --cov-report term-missing --cov pglookout test/

clean:
	$(RM) -r *.egg-info/ build/ dist/
	$(RM) ../pglookout_* test-*.xml

deb:
	cp debian/changelog.in debian/changelog
	dch -v $(long_ver) "Automatically built package"
	dpkg-buildpackage -A -uc -us

rpm:
	git archive --output=pglookout-rpm-src.tar.gz --prefix=pglookout/ HEAD
	rpmbuild -bb pglookout.spec \
		--define '_sourcedir $(shell pwd)' \
		--define 'major_version $(short_ver)' \
		--define 'minor_version $(subst -,.,$(subst $(short_ver)-,,$(long_ver)))'
	$(RM) pglookout-rpm-src.tar.gz

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

