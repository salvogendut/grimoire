EXTENSION_UUID := grimoire@salvogendut.github.io
EXTENSION_SOURCE := extension/$(EXTENSION_UUID)
EXTENSION_SCHEMA := $(EXTENSION_SOURCE)/schemas/org.grimoire.gschema.xml
EXTENSION_SCHEMA_REL := schemas/org.grimoire.gschema.xml
EXTENSION_TARGET := $(HOME)/.local/share/gnome-shell/extensions/$(EXTENSION_UUID)
EXTENSION_BUNDLE := build/$(EXTENSION_UUID).shell-extension.zip
VERSION ?= 0.1.0
PREFIX ?= /usr
BINDIR ?= $(PREFIX)/bin
LIBEXECDIR ?= $(PREFIX)/libexec/grimoire
DATADIR ?= $(PREFIX)/share
SYSTEMD_USER_UNIT_DIR ?= $(PREFIX)/lib/systemd/user
GNOME_EXTENSION_DIR ?= $(DATADIR)/gnome-shell/extensions/$(EXTENSION_UUID)

.PHONY: compile-extension-schemas install install-extension enable-extension disable-extension list-windows dry-focus-yellow \
	arm-execution disarm-execution execution-mode start-daemon stop-daemon restart-daemon \
	status-daemon logs-daemon check-asr check-ai test dist rpm

compile-extension-schemas:
	glib-compile-schemas "$(EXTENSION_SOURCE)/schemas"

install-extension: compile-extension-schemas
	mkdir -p build
	gnome-extensions pack --force --out-dir build --schema "$(EXTENSION_SCHEMA_REL)" "$(EXTENSION_SOURCE)"
	gnome-extensions install --force "$(EXTENSION_BUNDLE)"
	@echo "Installed $(EXTENSION_UUID) to $(EXTENSION_TARGET)"

enable-extension:
	gnome-extensions enable "$(EXTENSION_UUID)"

disable-extension:
	gnome-extensions disable "$(EXTENSION_UUID)"

list-windows:
	python3 daemon/grimoired.py --list-windows

dry-focus-yellow:
	python3 daemon/grimoired.py --dry-run --command "focus yellow"

arm-execution:
	python3 daemon/grimoired.py --arm-execution

disarm-execution:
	python3 daemon/grimoired.py --disarm-execution

execution-mode:
	python3 daemon/grimoired.py --execution-mode

check-asr:
	python3 daemon/grimoired.py --check-asr

check-ai:
	python3 daemon/grimoired.py --check-ai

start-daemon:
	systemctl --user start grimoired.service

stop-daemon:
	systemctl --user stop grimoired.service

restart-daemon:
	systemctl --user restart grimoired.service

status-daemon:
	systemctl --user status grimoired.service

logs-daemon:
	journalctl --user -u grimoired.service -f

test:
	python3 -m unittest discover -s tests

install:
	install -d "$(DESTDIR)$(BINDIR)"
	install -d "$(DESTDIR)$(LIBEXECDIR)/grimoire"
	install -d "$(DESTDIR)$(GNOME_EXTENSION_DIR)"
	install -d "$(DESTDIR)$(GNOME_EXTENSION_DIR)/schemas"
	install -d "$(DESTDIR)$(SYSTEMD_USER_UNIT_DIR)"
	install -m 0755 daemon/grimoired.py "$(DESTDIR)$(LIBEXECDIR)/grimoired.py"
	install -m 0644 daemon/grimoire/__init__.py daemon/grimoire/commands.py "$(DESTDIR)$(LIBEXECDIR)/grimoire/"
	install -m 0644 "$(EXTENSION_SOURCE)/extension.js" "$(EXTENSION_SOURCE)/metadata.json" "$(EXTENSION_SOURCE)/stylesheet.css" "$(DESTDIR)$(GNOME_EXTENSION_DIR)/"
	install -m 0644 "$(EXTENSION_SCHEMA)" "$(DESTDIR)$(GNOME_EXTENSION_DIR)/schemas/"
	glib-compile-schemas "$(DESTDIR)$(GNOME_EXTENSION_DIR)/schemas"
	sed "s|@LIBEXECDIR@|$(LIBEXECDIR)|g" packaging/bin/grimoired.in > "$(DESTDIR)$(BINDIR)/grimoired"
	chmod 0755 "$(DESTDIR)$(BINDIR)/grimoired"
	sed "s|@BINDIR@|$(BINDIR)|g" packaging/systemd/grimoired.service.in > "$(DESTDIR)$(SYSTEMD_USER_UNIT_DIR)/grimoired.service"
	chmod 0644 "$(DESTDIR)$(SYSTEMD_USER_UNIT_DIR)/grimoired.service"

dist:
	mkdir -p dist
	git archive --format=tar --prefix="grimoire-$(VERSION)/" HEAD | gzip -n > "dist/grimoire-$(VERSION).tar.gz"

rpm: dist
	rpmbuild -ba packaging/rpm/grimoire.spec \
		--define "_sourcedir $(CURDIR)/dist" \
		--define "_srcrpmdir $(CURDIR)/dist" \
		--define "_rpmdir $(CURDIR)/dist"
