%{!?python_sitelib: %global python_sitelib %(%{__python} -c "from distutils.sysconfig import get_python_lib; print get_python_lib()")}

Name:           pglookout
Version:        %{major_version}
Release:        %{minor_version}%{?dist}
Url:            http://github.com/ohmu/pglookout
Summary:        PostgreSQL replication monitoring and failover daemon
License:        Apache V2
Source:         pglookout-rpm-src.tar.gz
BuildRoot:      %{_tmppath}/%{name}-%{version}-build
BuildRequires:  python-devel
BuildRequires:  python-distribute
BuildRequires:  python-nose
Requires:       python-psycopg2, python-requests
BuildArch:      noarch

%description
pglookout is a PostgreSQL replication monitoring and failover daemon.

%prep
%setup -q -n pglookout

%build
python setup.py build

%install
python setup.py install -O1 --skip-build --prefix=%{_prefix} --root=%{buildroot}

%check
python setup.py test

%files
%defattr(-,root,root,-)
%doc LICENSE README.rst
%{_bindir}/pglookout*
%{python_sitelib}/*

%changelog
* Tue Dec 16 2014 hannu.valtonen@ohmu.fi
- Initial RPM package spec
