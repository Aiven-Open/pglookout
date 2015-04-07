short_ver = 1.1.0
long_ver = $(shell git describe --long 2>/dev/null || echo $(short_ver)-0-unknown-g`git describe --always`)

all: py-egg

PYTHON ?= python
PYLINT_DIRS = pglookout/ test/

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
		python-mock python3-mock python-psycopg2 python3-psycopg2 \
		python-requests python3-requests systemd-python systemd-python3

build-dep-deb:
	sudo apt-get install \
		build-essential devscripts dh-systemd \
		python-all python-setuptools python-psycopg2 python-requests
