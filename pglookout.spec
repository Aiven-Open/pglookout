Name:           pglookout
Version:        %{major_version}
Release:        %{minor_version}%{?dist}
Url:            http://github.com/ohmu/pglookout
Summary:        PostgreSQL replication monitoring and failover daemon (Python 2)
License:        ASL 2.0
Source0:        pglookout-rpm-src.tar.gz
Requires:       python-psycopg2, python-requests, python-setuptools, systemd-python, systemd
Requires(pre):  shadow-utils
BuildRequires:  pytest, pylint, %{requires}
BuildArch:      noarch

%description
pglookout is a PostgreSQL replication monitoring and failover daemon.

This is the Python 2 package of pglookout.

%if %{?python3_sitelib:1}0
%package -n python3-pglookout
Summary:        PostgreSQL replication monitoring and failover daemon (Python 3)
Requires:       python3-psycopg2, python3-requests, python3-setuptools, systemd-python3, systemd
Requires(pre):  shadow-utils
BuildRequires:  python3-pytest, python3-pylint, %{requires}
BuildArch:      noarch

%description -n python3-pglookout
pglookout is a PostgreSQL replication monitoring and failover daemon.

This is the Python 3 package of pglookout.
%endif

%prep
%setup -q -n pglookout

%install
%{__mkdir_p} %{buildroot}%{_localstatedir}/lib/pglookout %{buildroot}%{_datadir}/pglookout

python2 setup.py install --prefix=%{_prefix} --root=%{buildroot}
mv %{buildroot}%{_bindir}/pglookout_current_master %{buildroot}%{_bindir}/pglookout_current_master-py2
mv %{buildroot}%{_bindir}/pglookout %{buildroot}%{_bindir}/pglookout-py2
sed -e "s!/usr/bin/pglookout /var/!%{_bindir}/pglookout-py2 %{_localstatedir}/!g" pglookout.unit \
    > %{buildroot}%{_datadir}/pglookout/pglookout-py2.service

%if %{?python3_sitelib:1}0
python3 setup.py install --prefix=%{_prefix} --root=%{buildroot}
mv %{buildroot}%{_bindir}/pglookout_current_master %{buildroot}%{_bindir}/pglookout_current_master-py3
mv %{buildroot}%{_bindir}/pglookout %{buildroot}%{_bindir}/pglookout-py3
sed -e "s!/usr/bin/pglookout /var/!%{_bindir}/pglookout-py3 %{_localstatedir}/!g" pglookout.unit \
    > %{buildroot}%{_datadir}/pglookout/pglookout-py3.service
%endif

sed -e "s@#!/bin/python@#!%{_bindir}/python@" -i %{buildroot}%{_bindir}/*

%check
make test PYTHON=python2
%if %{?python3_sitelib:1}0
make test PYTHON=python3
%endif

%pre
getent group pglookout >/dev/null || groupadd -r pglookout
getent passwd pglookout >/dev/null || \
    useradd -r -g pglookout -d %{_localstatedir}/lib/pglookout -s /usr/bin/sh \
	    -c "pglookout account" pglookout

%post
[ -L %{_bindir}/pglookout ] || ln -sf pglookout-py2 %{_bindir}/pglookout
[ -L %{_bindir}/pglookout_current_master ] || ln -sf pglookout_current_master-py2 %{_bindir}/pglookout_current_master
[ -L %{_unitdir}/pglookout.service ] || ln -sf %{_datadir}/pglookout/pglookout-py2.service %{_unitdir}/pglookout.service

%if %{?python3_sitelib:1}0
%pre -n python3-pglookout
getent group pglookout >/dev/null || groupadd -r pglookout
getent passwd pglookout >/dev/null || \
    useradd -r -g pglookout -d %{_localstatedir}/lib/pglookout -s /usr/bin/sh \
	    -c "pglookout account" pglookout

%post -n python3-pglookout
[ -L %{_bindir}/pglookout ] || ln -sf pglookout-py3 %{_bindir}/pglookout
[ -L %{_bindir}/pglookout_current_master ] || ln -sf pglookout_current_master-py3 %{_bindir}/pglookout_current_master
[ -L %{_unitdir}/pglookout.service ] || ln -sf %{_datadir}/pglookout/pglookout-py3.service %{_unitdir}/pglookout.service
%endif

%files
%defattr(-,root,root,-)
%doc LICENSE README.rst pglookout.json
%{_bindir}/pglookout-py2
%{_bindir}/pglookout_current_master-py2
%{python_sitelib}/*
%{_datadir}/pglookout/pglookout-py2.service
%dir %{_datadir}/pglookout
%ghost %{_unitdir}/pglookout.service
%ghost %{_bindir}/pglookout
%ghost %{_bindir}/pglookout_current_master
%attr(0755, pglookout, pglookout) %{_localstatedir}/lib/pglookout

%if %{?python3_sitelib:1}0
%files -n python3-pglookout
%defattr(-,root,root,-)
%doc LICENSE README.rst pglookout.json
%{_bindir}/pglookout-py3
%{_bindir}/pglookout_current_master-py3
%{python3_sitelib}/*
%{_datadir}/pglookout/pglookout-py3.service
%dir %{_datadir}/pglookout
%attr(0755, pglookout, pglookout) %{_localstatedir}/lib/pglookout
%ghost %{_unitdir}/pglookout.service
%ghost %{_bindir}/pglookout
%ghost %{_bindir}/pglookout_current_master
%endif

%changelog
* Fri Feb 27 2015 Oskari Saarenmaa <os@ohmu.fi> - 1.1.0
- Refactored
- Python 3 support

* Tue Dec 16 2014 Hannu Valtonen <hannu.valtonen@ohmu.fi> - 1.0.0
- Initial RPM package spec
