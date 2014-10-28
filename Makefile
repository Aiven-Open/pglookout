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
