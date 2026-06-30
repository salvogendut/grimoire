EXTENSION_UUID := grimoire@salvogendut.github.io
EXTENSION_SOURCE := extension/$(EXTENSION_UUID)
EXTENSION_TARGET := $(HOME)/.local/share/gnome-shell/extensions/$(EXTENSION_UUID)
EXTENSION_BUNDLE := build/$(EXTENSION_UUID).shell-extension.zip

.PHONY: install-extension enable-extension disable-extension list-windows dry-focus-yellow test

install-extension:
	mkdir -p build
	gnome-extensions pack --force --out-dir build "$(EXTENSION_SOURCE)"
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

test:
	python3 -m unittest discover -s tests
