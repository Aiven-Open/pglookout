Name:           pglookout
Version:        %{major_version}
Release:        %{minor_version}%{?dist}
Url:            https://github.com/aiven/pglookout
Summary:        PostgreSQL replication monitoring and failover daemon
License:        ASL 2.0
Source0:        pglookout-rpm-src.tar
Obsoletes:      python3-pglookout
Requires:       python3-psycopg2, python3-requests, python3-setuptools, systemd-python3, systemd
BuildRequires:  python3-psycopg2, python3-requests, python3-setuptools, systemd-python3, systemd
BuildRequires:  python3-pytest, python3-pylint
BuildArch:      noarch

%description
pglookout is a PostgreSQL replication monitoring and failover daemon.
pglookout monitors PG database nodes and their replication status and acts
according to that status, for example calling a predefined failover command
to promote a new master in case the previous one goes missing.


%prep
%setup -q -n pglookout


%install
python3 setup.py install --prefix=%{_prefix} --root=%{buildroot}
sed -e "s@#!/bin/python@#!%{_bindir}/python@" -i %{buildroot}%{_bindir}/*
%{__install} -Dm0644 pglookout.unit %{buildroot}%{_unitdir}/pglookout.service


%check
make test PYTHON=python3


%files
%defattr(-,root,root,-)
%doc LICENSE README.rst pglookout.json
%{_bindir}/pglookout*
%{_unitdir}/pglookout.service
%{python3_sitelib}/*


%changelog
* Wed Mar 25 2015 Oskari Saarenmaa <os@ohmu.fi> - 1.1.0-9
* Build just a single package using Python 3 if possible, Python 2 otherwise

* Fri Feb 27 2015 Oskari Saarenmaa <os@ohmu.fi> - 1.1.0
- Refactored
- Python 3 support

* Tue Dec 16 2014 Hannu Valtonen <hannu.valtonen@ohmu.fi> - 1.0.0
- Initial RPM package spec
