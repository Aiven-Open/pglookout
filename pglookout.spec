Name:           pglookout
Version:        %{major_version}
Release:        %{minor_version}%{?dist}
Url:            http://github.com/ohmu/pglookout
Summary:        PostgreSQL replication monitoring and failover daemon
License:        ASL 2.0
Source0:        pglookout-rpm-src.tar.gz
Requires:       python-psycopg2, python-requests, python-setuptools, systemd
Requires(pre):  shadow-utils
BuildRequires:  pytest, pylint, %{requires}
BuildArch:      noarch

%description
pglookout is a PostgreSQL replication monitoring and failover daemon.

%prep
%setup -q -n pglookout

%build
python setup.py build

%install
python setup.py install -O1 --skip-build --prefix=%{_prefix} --root=%{buildroot}
%{__install} -Dm0644 pglookout.unit %{buildroot}%{_unitdir}/pglookout.service
%{__mkdir_p} %{buildroot}%{_localstatedir}/lib/pglookout

%check
make test

%pre
getent group pglookout >/dev/null || groupadd -r pglookout
getent passwd pglookout >/dev/null || \
    useradd -r -g pglookout -d %{_localstatedir}/lib/pglookout -s /usr/bin/sh \
	    -c "pglookout account" pglookout

%files
%defattr(-,root,root,-)
%doc LICENSE README.rst pglookout.json
%{_unitdir}/pglookout.service
%{_bindir}/pglookout*
%{python_sitelib}/*
%attr(0755, pglookout, pglookout) %{_localstatedir}/lib/pglookout

%changelog
* Fri Feb 27 2015 Oskari Saarenmaa <os@ohmu.fi> - 1.1.0
- Refactored

* Tue Dec 16 2014 Hannu Valtonen <hannu.valtonen@ohmu.fi> - 1.0.0
- Initial RPM package spec
