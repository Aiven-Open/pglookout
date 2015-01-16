short_ver = 1.0.0
long_ver = $(shell git describe --long 2>/dev/null || echo $(short_ver)-0-unknown-g`git describe --always`)

all: py-egg

PYLINT_DIRS = pglookout/ test/

test: pylint unittest

unittest:
	PYTHONPATH=test/ nosetests --nologcapture --nocapture test/

unittest3:
	PYTHONPATH=test/ nosetests --nologcapture --nocapture test/

pylint:
	pylint --rcfile .pylintrc $(PYLINT_DIRS)

clean:
	$(RM) -r *.egg-info/ build/ dist/
	$(RM) ../pglookout_* test-*.xml

deb:
	cp debian/changelog.in debian/changelog
	dpkg-buildpackage -A -uc -us

rpm:
	git archive --output=pglookout-rpm-src.tar.gz --prefix=pglookout/ HEAD
	rpmbuild -bb pglookout.spec \
		--define '_sourcedir $(shell pwd)' \
		--define 'major_version $(short_ver)' \
		--define 'minor_version $(subst -,.,$(subst $(short_ver)-,,$(long_ver)))'
	$(RM) pglookout-rpm-src.tar.gz

build-dep-fed:
	sudo yum -y install python-devel python-nose python-psycopg2 python3-psycopg2
