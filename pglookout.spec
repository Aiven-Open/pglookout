%{!?python_sitelib: %global python_sitelib %(%{__python} -c "from distutils.sysconfig import get_python_lib; print get_python_lib()")}

Name:           pglookout
Version:        %{major_version}
Release:        %{minor_version}%{?dist}
Url:            http://github.com/ohmu/pglookout
Summary:        PostgreSQL replication monitoring and failover daemon
License:        Apache V2
Source0:        pglookout-rpm-src.tar.gz
Source1:        pglookout.unit
BuildRoot:      %{_tmppath}/%{name}-%{version}-build
BuildRequires:  python-devel
BuildRequires:  python-distribute
BuildRequires:  python-nose
Requires:       python-psycopg2, python-requests
Requires(pre):  shadow-utils
BuildArch:      noarch

%description
pglookout is a PostgreSQL replication monitoring and failover daemon.

%pre
mkdir -p /var/lib/pglookout
getent group pglookout >/dev/null || groupadd -r pglookout
getent passwd pglookout >/dev/null || \
    useradd -r -g pglookout -d /var/lib/pglookout -s /usr/bin/sh \
	    -c "pglookout account" pglookout
chown pglookout.pglookout /var/lib/pglookout
exit 0

%prep
%setup -q -n pglookout

%build
python setup.py build

%install
python setup.py install -O1 --skip-build --prefix=%{_prefix} --root=%{buildroot}
%{__mkdir_p} ${RPM_BUILD_ROOT}/usr/lib/systemd/system
%{__install} -m0644 %{SOURCE1} ${RPM_BUILD_ROOT}/usr/lib/systemd/system/pglookout.service

%check
python setup.py test

%files
/usr/lib/systemd/system/*

%defattr(-,root,root,-)

%doc LICENSE README.rst pglookout.json
%{_bindir}/pglookout*

%{python_sitelib}/*

%changelog
* Tue Dec 16 2014 hannu.valtonen@ohmu.fi
- Initial RPM package spec
