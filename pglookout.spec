%if %{?python3_sitelib:1}0
%global use_python3 1
%else
%global use_python3 0
%endif

Name:           pglookout
Version:        %{major_version}
Release:        %{minor_version}%{?dist}
Url:            https://github.com/ohmu/pglookout
Summary:        PostgreSQL replication monitoring and failover daemon
License:        ASL 2.0
Source0:        pglookout-rpm-src.tar
Requires(pre):  shadow-utils
Requires:       postgresql-server, systemd
%if %{use_python3}
Obsoletes:      python3-pglookout
Requires:       python3-psycopg2, python3-requests, python3-setuptools, systemd-python3, systemd
BuildRequires:  python3-pytest, python3-pylint
%else
Requires:       python-psycopg2, python-requests, python-futures, python-setuptools, systemd-python, systemd
BuildRequires:  pytest, pylint
%endif
BuildRequires:  %{requires}
BuildArch:      noarch

%description
pglookout is a PostgreSQL replication monitoring and failover daemon.
pglookout monitors PG database nodes and their replication status and acts
according to that status, for example calling a predefined failover command
to promote a new master in case the previous one goes missing.


%prep
%setup -q -n pglookout


%install
%if %{use_python3}
python3 setup.py install --prefix=%{_prefix} --root=%{buildroot}
%else
python2 setup.py install --prefix=%{_prefix} --root=%{buildroot}
%endif
sed -e "s@#!/bin/python@#!%{_bindir}/python@" -i %{buildroot}%{_bindir}/*
%{__install} -Dm0644 pglookout.unit %{buildroot}%{_unitdir}/pglookout.service
%{__mkdir_p} %{buildroot}%{_localstatedir}/lib/pglookout


%check
%if %{use_python3}
make test PYTHON=python3
%else
make test PYTHON=python2
%endif


%files
%defattr(-,root,root,-)
%doc LICENSE README.rst pglookout.json
%{_bindir}/pglookout*
%{_unitdir}/pglookout.service
%if %{use_python3}
%{python3_sitelib}/*
%else
%{python_sitelib}/*
%endif
%attr(0755, postgres, postgres) %{_localstatedir}/lib/pglookout


%changelog
* Wed Mar 25 2015 Oskari Saarenmaa <os@ohmu.fi> - 1.1.0-9
* Build just a single package using Python 3 if possible, Python 2 otherwise

* Fri Feb 27 2015 Oskari Saarenmaa <os@ohmu.fi> - 1.1.0
- Refactored
- Python 3 support

* Tue Dec 16 2014 Hannu Valtonen <hannu.valtonen@ohmu.fi> - 1.0.0
- Initial RPM package spec
