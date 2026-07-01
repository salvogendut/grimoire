Name:           grimoire
Version:        0.1.0
Release:        6%{?dist}
Summary:        GNOME voice-control handles and daemon

# TODO: choose the project license before publishing this package.
License:        LicenseRef-Unknown
URL:            https://github.com/salvogendut/grimoire
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  glib2
BuildRequires:  make
BuildRequires:  python3
BuildRequires:  systemd-rpm-macros

Requires:       gnome-shell >= 50
Requires:       python3
Requires:       glib2
Requires:       pipewire-utils
Requires:       systemd

%description
Grimoire is an experimental GNOME Shell extension and Python daemon that gives
windows speech-addressable visual handles, then dispatches voice-style commands
such as focus, maximize, open application, and dictation over the session bus.

%prep
%autosetup

%build
# No build step is needed for the current JavaScript and Python prototype.

%install
%make_install PREFIX=%{_prefix} SYSTEMD_USER_UNIT_DIR=%{_userunitdir}

%check
%{python3} -m unittest discover -s tests

%post
%systemd_user_post grimoired.service

%preun
%systemd_user_preun grimoired.service

%postun
%systemd_user_postun_with_restart grimoired.service

%files
%doc README.md docs/architecture.md docs/protocol.md
%dir %{_libexecdir}/grimoire
%dir %{_libexecdir}/grimoire/grimoire
%{_bindir}/grimoired
%{_libexecdir}/grimoire/grimoired.py
%{_libexecdir}/grimoire/grimoire/__init__.py
%{_libexecdir}/grimoire/grimoire/commands.py
%{_datadir}/gnome-shell/extensions/grimoire@salvogendut.github.io/
%{_userunitdir}/grimoired.service

%changelog
* Wed Jul 01 2026 Salvo Gendut <salvogendut@users.noreply.github.com> - 0.1.0-6
- Add an opt-in AI interpreter layer for command normalization.

* Wed Jul 01 2026 Salvo Gendut <salvogendut@users.noreply.github.com> - 0.1.0-5
- Add an ASR setup diagnostic command.

* Wed Jul 01 2026 Salvo Gendut <salvogendut@users.noreply.github.com> - 0.1.0-4
- Add real-time daemon phase reporting to the top-bar indicator.

* Wed Jul 01 2026 Salvo Gendut <salvogendut@users.noreply.github.com> - 0.1.0-3
- Add keyboard, command-line, and Makefile controls for the execution gate.

* Wed Jul 01 2026 Salvo Gendut <salvogendut@users.noreply.github.com> - 0.1.0-2
- Add shell-controlled execution gate for the daemon service.

* Wed Jul 01 2026 Salvo Gendut <salvogendut@users.noreply.github.com> - 0.1.0-1
- Initial local packaging scaffold.
