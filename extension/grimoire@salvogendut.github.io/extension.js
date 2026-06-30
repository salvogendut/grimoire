import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const BUS_NAME = 'org.grimoire.Shell';
const OBJECT_PATH = '/org/grimoire/Shell';
const INTERFACE_NAME = 'org.grimoire.Shell';

const DBUS_XML = `
<node>
  <interface name="${INTERFACE_NAME}">
    <method name="ListWindows">
      <arg type="s" name="windows_json" direction="out"/>
    </method>
    <method name="GetPalette">
      <arg type="as" name="colors" direction="out"/>
    </method>
    <method name="GetBirds">
      <arg type="as" name="birds" direction="out"/>
    </method>
    <method name="FocusColor">
      <arg type="s" name="handle" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="RunWindowCommand">
      <arg type="s" name="handle" direction="in"/>
      <arg type="s" name="command" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="Refresh">
      <arg type="b" name="ok" direction="out"/>
    </method>
    <signal name="WindowsChanged"/>
  </interface>
</node>`;

const TAB_WIDTH = 34;
const TAB_MIN_HEIGHT = 92;
const TAB_LETTER_HEIGHT = 13;
const TAB_HEADER_OFFSET = 36;
const TAB_LEFT_INSET = 16;

const PALETTE = [
    {name: 'yellow', hex: '#f2c94c'},
    {name: 'blue', hex: '#2f80ed'},
    {name: 'green', hex: '#27ae60'},
    {name: 'red', hex: '#eb5757'},
    {name: 'purple', hex: '#9b51e0'},
    {name: 'orange', hex: '#f2994a'},
    {name: 'cyan', hex: '#00bcd4'},
    {name: 'pink', hex: '#ff4fa3'},
    {name: 'white', hex: '#f2f2f2'},
    {name: 'black', hex: '#111111'},
];

const BIRDS = [
    {name: 'sparrow'},
    {name: 'crow'},
    {name: 'dove'},
    {name: 'owl'},
    {name: 'robin'},
    {name: 'raven'},
    {name: 'finch'},
    {name: 'hawk'},
    {name: 'wren'},
    {name: 'swan'},
];

const COMMANDS = new Set([
    'focus',
    'close',
    'minimize',
    'unminimize',
    'maximize',
    'unmaximize',
    'fullscreen',
    'unfullscreen',
]);

function normalizeName(name) {
    return `${name}`.trim().toLowerCase();
}

function contrastForColor(colorName) {
    if (colorName === 'black' || colorName === 'blue' || colorName === 'purple')
        return '#ffffff';

    return '#111111';
}

function safeCall(object, methodName, fallback = null) {
    try {
        if (typeof object?.[methodName] === 'function')
            return object[methodName]();
    } catch (error) {
        console.warn(`Grimoire: ${methodName} failed: ${error}`);
    }

    return fallback;
}

export default class GrimoireExtension extends Extension {
    enable() {
        this._records = new Map();
        this._busNameId = 0;
        this._dbusImpl = null;

        this._exportDbus();

        global.display.connectObject(
            'window-created', () => this._syncWindows(),
            'notify::focus-window', () => this._syncAllSidebars(),
            this);

        global.window_manager.connectObject(
            'switch-workspace', () => this._syncAllSidebars(),
            this);

        Main.layoutManager.connectObject(
            'monitors-changed', () => this._syncAllSidebars(),
            this);

        this._syncWindows();
    }

    disable() {
        Main.layoutManager.disconnectObject(this);
        global.window_manager.disconnectObject(this);
        global.display.disconnectObject(this);

        this._unexportDbus();

        for (const window of [...this._records.keys()])
            this._removeWindow(window, false);

        this._records = null;
    }

    ListWindows() {
        return JSON.stringify(this._listWindows());
    }

    GetPalette() {
        return PALETTE.map(color => color.name);
    }

    GetBirds() {
        return BIRDS.map(bird => bird.name);
    }

    FocusColor(handle) {
        return this.RunWindowCommand(handle, 'focus');
    }

    RunWindowCommand(handle, command) {
        const record = this._findByHandle(handle);
        const normalizedCommand = normalizeName(command);

        if (!record || !COMMANDS.has(normalizedCommand))
            return false;

        return this._runWindowCommand(record.window, normalizedCommand);
    }

    Refresh() {
        this._syncWindows();
        this._syncAllSidebars();
        return true;
    }

    _exportDbus() {
        this._dbusImpl = Gio.DBusExportedObject.wrapJSObject(DBUS_XML, this);
        this._dbusImpl.export(Gio.DBus.session, OBJECT_PATH);
        this._busNameId = Gio.bus_own_name_on_connection(
            Gio.DBus.session,
            BUS_NAME,
            Gio.BusNameOwnerFlags.REPLACE,
            null,
            null);
    }

    _unexportDbus() {
        if (this._busNameId) {
            Gio.bus_unown_name(this._busNameId);
            this._busNameId = 0;
        }

        if (this._dbusImpl) {
            this._dbusImpl.unexport();
            this._dbusImpl = null;
        }
    }

    _syncWindows() {
        const windowActors = global.get_window_actors()
            .filter(actor => this._isEligibleWindow(actor.meta_window))
            .sort((left, right) =>
                left.meta_window.get_stable_sequence() -
                right.meta_window.get_stable_sequence());

        const current = new Set(windowActors.map(actor => actor.meta_window));

        for (const window of [...this._records.keys()]) {
            if (!current.has(window))
                this._removeWindow(window, false);
        }

        for (const actor of windowActors) {
            const window = actor.meta_window;
            if (this._records.has(window))
                this._records.get(window).actor = actor;
            else
                this._addWindow(window, actor);
        }

        this._syncAllSidebars();
        this._emitWindowsChanged();
    }

    _isEligibleWindow(window) {
        if (!window)
            return false;

        if (safeCall(window, 'is_skip_taskbar', window.skip_taskbar))
            return false;

        if (safeCall(window, 'is_override_redirect', false))
            return false;

        return true;
    }

    _addWindow(window, actor) {
        const color = this._nextAvailableColor();
        const bird = this._nextAvailableBird();
        if (!color || !bird)
            return;

        const marker = new St.Widget({
            style_class: 'grimoire-marker',
            reactive: false,
            visible: false,
        });
        const tab = new St.BoxLayout({
            style_class: 'grimoire-tab',
            vertical: true,
            reactive: false,
        });

        tab.set_style(`background-color: ${color.hex}; color: ${contrastForColor(color.name)};`);

        for (const letter of bird.name.toUpperCase()) {
            const label = new St.Label({
                text: letter,
                style_class: 'grimoire-tab-letter',
                x_align: Clutter.ActorAlign.CENTER,
            });
            label.set_width(TAB_WIDTH);
            label.set_height(TAB_LETTER_HEIGHT);
            tab.add_child(label);
        }

        marker.add_child(tab);

        this._records.set(window, {window, actor, color, bird, marker, tab});
        this._attachMarker(window);

        window.connectObject(
            'size-changed', () => this._syncSidebar(window),
            'position-changed', () => this._syncSidebar(window),
            'workspace-changed', () => this._syncSidebar(window),
            'notify::minimized', () => this._syncSidebar(window),
            'notify::skip-taskbar', () => this._syncWindows(),
            'unmanaging', () => this._removeWindow(window),
            this);
    }

    _removeWindow(window, emitChanged = true) {
        const record = this._records.get(window);
        if (!record)
            return;

        try {
            window.disconnectObject(this);
        } catch (error) {
            console.warn(`Grimoire: disconnect failed: ${error}`);
        }

        record.marker.destroy();
        this._records.delete(window);

        if (emitChanged)
            this._emitWindowsChanged();
    }

    _nextAvailableColor() {
        const used = new Set([...this._records.values()]
            .map(record => record.color.name));
        return PALETTE.find(color => !used.has(color.name)) ?? null;
    }

    _nextAvailableBird() {
        const used = new Set([...this._records.values()]
            .map(record => record.bird.name));
        return BIRDS.find(bird => !used.has(bird.name)) ?? null;
    }

    _findByHandle(handle) {
        const normalizedHandle = normalizeName(handle);
        return [...this._records.values()]
            .find(record =>
                record.color.name === normalizedHandle ||
                record.bird.name === normalizedHandle) ?? null;
    }

    _syncAllSidebars() {
        for (const window of this._records.keys())
            this._syncSidebar(window);
    }

    _syncSidebar(window) {
        const record = this._records.get(window);
        if (!record)
            return;

        const actor = record.actor;
        const shouldShow = !window.minimized &&
            actor &&
            !safeCall(window, 'is_skip_taskbar', window.skip_taskbar) &&
            safeCall(window, 'showing_on_its_workspace', true);

        if (!shouldShow) {
            record.marker.hide();
            return;
        }

        const [actorX, actorY] = actor.get_position();
        const [actorWidth, actorHeight] = actor.get_size();
        const frameRect = window.get_frame_rect();
        const markerWidth = Math.round(Math.max(1, Math.min(TAB_WIDTH, actorWidth)));
        const localX = frameRect.x - actorX + TAB_LEFT_INSET;
        const localY = frameRect.y - actorY + TAB_HEADER_OFFSET;
        const x = Math.round(Math.max(0, Math.min(localX, actorWidth - markerWidth)));
        const y = Math.round(Math.max(0, Math.min(localY, actorHeight - 1)));
        const tabHeight = Math.round(Math.min(
            Math.max(1, actorHeight - y),
            Math.max(TAB_MIN_HEIGHT, record.bird.name.length * TAB_LETTER_HEIGHT + 14)));

        this._attachMarker(window);
        record.marker.set_position(x, y);
        record.marker.set_size(markerWidth, tabHeight);
        record.tab.set_position(0, 0);
        record.tab.set_size(markerWidth, tabHeight);
        record.marker.show();
        actor.set_child_above_sibling(record.marker, null);
    }

    _attachMarker(window) {
        const record = this._records.get(window);
        if (!record?.actor)
            return;

        const parent = record.marker.get_parent();
        if (parent === record.actor)
            return;

        if (parent)
            parent.remove_child(record.marker);

        record.actor.add_child(record.marker);
    }

    _runWindowCommand(window, command) {
        switch (command) {
        case 'focus':
            Main.activateWindow(window);
            return true;
        case 'close':
            if (!window.can_close())
                return false;
            window.delete(global.get_current_time());
            return true;
        case 'minimize':
            if (!window.can_minimize())
                return false;
            window.minimize();
            return true;
        case 'unminimize':
            window.unminimize();
            Main.activateWindow(window);
            return true;
        case 'maximize':
            if (!window.can_maximize())
                return false;
            window.maximize();
            return true;
        case 'unmaximize':
            window.unmaximize();
            return true;
        case 'fullscreen':
            window.make_fullscreen();
            return true;
        case 'unfullscreen':
            window.unmake_fullscreen();
            return true;
        default:
            return false;
        }
    }

    _listWindows() {
        return [...this._records.values()].map(record => ({
            color: record.color.name,
            bird: record.bird.name,
            title: safeCall(record.window, 'get_title', '') ?? '',
            wm_class: safeCall(record.window, 'get_wm_class', '') ?? '',
            pid: safeCall(record.window, 'get_pid', 0) ?? 0,
            stable_sequence: safeCall(record.window, 'get_stable_sequence', 0) ?? 0,
            focused: global.display.focus_window === record.window,
        }));
    }

    _emitWindowsChanged() {
        if (this._dbusImpl)
            this._dbusImpl.emit_signal('WindowsChanged', null);
    }
}
