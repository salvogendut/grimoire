import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Shell from 'gi://Shell';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';

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
    <method name="ListApps">
      <arg type="s" name="apps_json" direction="out"/>
    </method>
    <method name="LaunchApp">
      <arg type="s" name="query" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="PasteText">
      <arg type="s" name="text" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="PressKey">
      <arg type="s" name="key" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
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
    <method name="RefreshHandles">
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="SetDaemonStatus">
      <arg type="b" name="running" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <signal name="WindowsChanged"/>
  </interface>
</node>`;

const FRAME_BORDER_WIDTH = 6;
const VERTICAL_TAB_WIDTH = 34;
const VERTICAL_TAB_MIN_HEIGHT = 92;
const VERTICAL_TAB_LETTER_HEIGHT = 13;
const VERTICAL_TAB_HEADER_OFFSET = 36;
const VERTICAL_TAB_LEFT_INSET = 16;
const HORIZONTAL_TAB_HEIGHT = 34;
const HORIZONTAL_TAB_MIN_WIDTH = 116;
const HORIZONTAL_TAB_LETTER_WIDTH = 11;
const HORIZONTAL_TAB_RIGHT_INSET = 0;
const DAEMON_HEARTBEAT_TIMEOUT_MS = 6000;
const DAEMON_WATCH_INTERVAL_SECONDS = 2;
const KEY_PAUSE_MS = 15;

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

const APP_ALIASES = {
    calculator: ['org.gnome.Calculator.desktop'],
    files: ['org.gnome.Nautilus.desktop'],
    firefox: ['org.mozilla.firefox.desktop'],
    browser: ['org.mozilla.firefox.desktop'],
    settings: ['org.gnome.Settings.desktop'],
    software: ['org.gnome.Software.desktop'],
    terminal: ['org.gnome.Ptyxis.desktop', 'org.gnome.Terminal.desktop'],
};

const KEY_ALIASES = {
    enter: Clutter.KEY_Return,
    return: Clutter.KEY_Return,
};

function normalizeName(name) {
    return `${name}`.trim().toLowerCase();
}

function normalizeSearchTerm(name) {
    return normalizeName(name).replace(/[^a-z0-9]+/g, ' ').trim();
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

function handleMemory() {
    if (!globalThis.__grimoireHandleMemory) {
        globalThis.__grimoireHandleMemory = {
            exact: new Map(),
            app: new Map(),
        };
    }

    return globalThis.__grimoireHandleMemory;
}

class GrimoireDaemonIndicator extends PanelMenu.Button {
    static {
        GObject.registerClass(this);
    }

    constructor() {
        super(0.0, 'Grimoire', true);

        this._icon = new St.Icon({
            icon_name: 'audio-input-microphone-symbolic',
            style_class: 'system-status-icon',
        });
        this.add_child(this._icon);
        this.setRunning(false);
    }

    setRunning(running) {
        this._icon.set_style(running ? 'color: #57e389;' : 'color: #8b8e91;');
        this.opacity = running ? 255 : 160;
        this.set_accessible_name(
            running ? 'Grimoire daemon running' : 'Grimoire daemon inactive');
    }
}

export default class GrimoireExtension extends Extension {
    enable() {
        this._records = new Map();
        this._busNameId = 0;
        this._dbusImpl = null;
        this._appSystem = Shell.AppSystem.get_default();
        this._handleMemory = handleMemory();
        this._daemonLastSeen = 0;
        this._daemonMonitorId = 0;
        this._daemonRunning = false;
        this._indicator = new GrimoireDaemonIndicator();
        Main.panel.addToStatusArea('grimoire-daemon', this._indicator);
        this._startDaemonMonitor();

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

        this._stopDaemonMonitor();
        this._indicator?.destroy();
        this._indicator = null;

        this._unexportDbus();
        this._appSystem = null;

        for (const window of [...this._records.keys()])
            this._removeWindow(window, false, true);

        this._records = null;
        this._handleMemory = null;
        this._daemonLastSeen = 0;
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

    ListApps() {
        return JSON.stringify(this._listApps());
    }

    LaunchApp(query) {
        const match = this._findApp(query);
        if (!match)
            return false;

        try {
            if (match.app)
                match.app.open_new_window(-1);
            else
                match.appInfo.launch([], null);
        } catch (error) {
            console.warn(`Grimoire: launch app failed: ${error}`);
            return false;
        }

        return true;
    }

    PasteText(text) {
        if (!text)
            return false;

        try {
            St.Clipboard.get_default().set_text(St.ClipboardType.CLIPBOARD, text);
            this._emitPasteShortcut();
        } catch (error) {
            console.warn(`Grimoire: paste text failed: ${error}`);
            return false;
        }

        return true;
    }

    PressKey(key) {
        const keyval = KEY_ALIASES[normalizeName(key)];
        if (!keyval)
            return false;

        try {
            this._emitKeyvals([keyval]);
        } catch (error) {
            console.warn(`Grimoire: press key failed: ${error}`);
            return false;
        }

        return true;
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

    RefreshHandles() {
        this._clearHandleMemory();

        for (const window of [...this._records.keys()])
            this._removeWindow(window, false, false);

        this._syncWindows();
        return true;
    }

    SetDaemonStatus(running) {
        if (running) {
            this._daemonLastSeen = Date.now();
            this._setDaemonRunning(true);
        } else {
            this._daemonLastSeen = 0;
            this._setDaemonRunning(false);
        }

        return true;
    }

    _startDaemonMonitor() {
        this._stopDaemonMonitor();
        this._daemonMonitorId = GLib.timeout_add_seconds(
            GLib.PRIORITY_DEFAULT,
            DAEMON_WATCH_INTERVAL_SECONDS,
            () => {
                if (
                    this._daemonLastSeen &&
                    Date.now() - this._daemonLastSeen > DAEMON_HEARTBEAT_TIMEOUT_MS
                )
                    this._setDaemonRunning(false);

                return GLib.SOURCE_CONTINUE;
            });
    }

    _stopDaemonMonitor() {
        if (!this._daemonMonitorId)
            return;

        GLib.source_remove(this._daemonMonitorId);
        this._daemonMonitorId = 0;
    }

    _setDaemonRunning(running) {
        this._daemonRunning = running;
        this._indicator?.setRunning(running);
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
        const assignment = this._nextAssignmentForWindow(window);
        const color = assignment?.color;
        const bird = assignment?.bird;
        if (!color || !bird)
            return;

        const marker = new St.Widget({
            style_class: 'grimoire-marker',
            reactive: false,
            visible: false,
        });
        const frame = new St.Widget({
            style_class: 'grimoire-frame',
            reactive: false,
        });
        const verticalTab = new St.BoxLayout({
            style_class: 'grimoire-tab grimoire-tab-vertical',
            vertical: true,
            reactive: false,
        });
        const horizontalTab = new St.BoxLayout({
            style_class: 'grimoire-tab grimoire-tab-horizontal',
            reactive: false,
        });
        const tabStyle = `background-color: ${color.hex}; color: ${contrastForColor(color.name)};`;

        frame.set_style(`border: ${FRAME_BORDER_WIDTH}px solid ${color.hex};`);
        verticalTab.set_style(tabStyle);
        horizontalTab.set_style(tabStyle);

        for (const letter of bird.name.toUpperCase()) {
            const label = new St.Label({
                text: letter,
                style_class: 'grimoire-tab-vertical-letter',
                x_align: Clutter.ActorAlign.CENTER,
            });
            label.set_width(VERTICAL_TAB_WIDTH);
            label.set_height(VERTICAL_TAB_LETTER_HEIGHT);
            verticalTab.add_child(label);
        }

        horizontalTab.add_child(new St.Label({
            text: bird.name.toUpperCase(),
            style_class: 'grimoire-tab-horizontal-label',
            x_expand: true,
            y_expand: true,
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
        }));

        marker.add_child(frame);
        marker.add_child(verticalTab);
        marker.add_child(horizontalTab);

        this._records.set(window, {
            window,
            actor,
            color,
            bird,
            marker,
            frame,
            verticalTab,
            horizontalTab,
            exactSignature: this._exactWindowSignature(window),
            appSignature: this._appWindowSignature(window),
            handleSource: assignment.source,
        });
        this._rememberAssignment(this._records.get(window));
        this._attachMarker(window);

        window.connectObject(
            'size-changed', () => this._syncSidebar(window),
            'position-changed', () => this._syncSidebar(window),
            'workspace-changed', () => this._syncSidebar(window),
            'notify::title', () => this._rememberWindowAssignment(window),
            'notify::minimized', () => this._syncSidebar(window),
            'notify::skip-taskbar', () => this._syncWindows(),
            'unmanaging', () => this._removeWindow(window),
            this);
    }

    _removeWindow(window, emitChanged = true, remember = true) {
        const record = this._records.get(window);
        if (!record)
            return;

        if (remember)
            this._rememberAssignment(record);

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

    _nextAssignmentForWindow(window) {
        const usedColors = new Set([...this._records.values()]
            .map(record => record.color.name));
        const usedBirds = new Set([...this._records.values()]
            .map(record => record.bird.name));
        const exactSignature = this._exactWindowSignature(window);
        const appSignature = this._appWindowSignature(window);
        const exact = this._rememberedAssignment(
            this._handleMemory.exact,
            exactSignature,
            usedColors,
            usedBirds);

        if (exact)
            return {...exact, source: 'remembered'};

        if (appSignature && !this._hasLiveAppSignature(appSignature)) {
            const app = this._rememberedAssignment(
                this._handleMemory.app,
                appSignature,
                usedColors,
                usedBirds);
            if (app)
                return {...app, source: 'app-remembered'};
        }

        const color = PALETTE.find(entry => !usedColors.has(entry.name)) ?? null;
        const bird = BIRDS.find(entry => !usedBirds.has(entry.name)) ?? null;
        if (!color || !bird)
            return null;

        return {color, bird, source: 'new'};
    }

    _rememberedAssignment(memory, key, usedColors, usedBirds) {
        if (!key)
            return null;

        const remembered = memory.get(key);
        if (!remembered)
            return null;

        if (usedColors.has(remembered.colorName) || usedBirds.has(remembered.birdName))
            return null;

        const color = PALETTE.find(entry => entry.name === remembered.colorName) ?? null;
        const bird = BIRDS.find(entry => entry.name === remembered.birdName) ?? null;
        if (!color || !bird)
            return null;

        return {color, bird};
    }

    _rememberWindowAssignment(window) {
        const record = this._records.get(window);
        if (!record)
            return;

        this._rememberAssignment(record);
    }

    _rememberAssignment(record) {
        if (!record || !this._handleMemory)
            return;

        record.exactSignature = this._exactWindowSignature(record.window);
        record.appSignature = this._appWindowSignature(record.window);

        const assignment = {
            colorName: record.color.name,
            birdName: record.bird.name,
            lastSeen: Date.now(),
        };

        if (record.exactSignature)
            this._handleMemory.exact.set(record.exactSignature, assignment);

        if (record.appSignature && !this._hasLiveAppSignature(record.appSignature, record.window))
            this._handleMemory.app.set(record.appSignature, assignment);
    }

    _clearHandleMemory() {
        if (!this._handleMemory)
            return;

        this._handleMemory.exact.clear();
        this._handleMemory.app.clear();
    }

    _exactWindowSignature(window) {
        const wmClass = normalizeSearchTerm(safeCall(window, 'get_wm_class', '') ?? '');
        const title = normalizeSearchTerm(safeCall(window, 'get_title', '') ?? '');
        if (!wmClass && !title)
            return '';

        return `${wmClass}|${title}`;
    }

    _appWindowSignature(window) {
        return normalizeSearchTerm(safeCall(window, 'get_wm_class', '') ?? '');
    }

    _hasLiveAppSignature(appSignature, exceptWindow = null) {
        for (const record of this._records.values()) {
            if (record.window === exceptWindow)
                continue;

            const currentSignature = record.appSignature || this._appWindowSignature(record.window);
            if (currentSignature === appSignature)
                return true;
        }

        return false;
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
        if (actorWidth <= 0 || actorHeight <= 0) {
            record.marker.hide();
            return;
        }

        const frameRect = window.get_frame_rect();
        const frameX = Math.round(Math.max(0, frameRect.x - actorX));
        const frameY = Math.round(Math.max(0, frameRect.y - actorY));
        const frameWidth = Math.round(Math.max(
            1,
            Math.min(frameRect.width, actorWidth - frameX)));
        const frameHeight = Math.round(Math.max(
            1,
            Math.min(frameRect.height, actorHeight - frameY)));

        this._attachMarker(window);
        record.marker.set_position(0, 0);
        record.marker.set_size(actorWidth, actorHeight);
        record.frame.set_position(frameX, frameY);
        record.frame.set_size(frameWidth, frameHeight);

        if (this._isMaximized(window))
            this._syncVerticalTab(record, frameX, frameY, actorWidth, actorHeight);
        else
            this._syncHorizontalTab(record, frameX, frameY, frameWidth, actorWidth);

        record.marker.show();
        actor.set_child_above_sibling(record.marker, null);
    }

    _syncHorizontalTab(record, frameX, frameY, frameWidth, actorWidth) {
        record.verticalTab.hide();

        const tabWidth = Math.round(Math.max(
            HORIZONTAL_TAB_MIN_WIDTH,
            record.bird.name.length * HORIZONTAL_TAB_LETTER_WIDTH + 38));
        const maxX = Math.max(0, actorWidth - tabWidth);
        const targetX = frameX + frameWidth - tabWidth - HORIZONTAL_TAB_RIGHT_INSET;
        const x = Math.round(Math.max(0, Math.min(targetX, maxX)));
        const y = Math.round(Math.max(0, frameY - HORIZONTAL_TAB_HEIGHT));

        record.horizontalTab.set_position(x, y);
        record.horizontalTab.set_size(tabWidth, HORIZONTAL_TAB_HEIGHT);
        record.horizontalTab.show();
    }

    _syncVerticalTab(record, frameX, frameY, actorWidth, actorHeight) {
        record.horizontalTab.hide();

        const tabWidth = Math.round(Math.max(1, Math.min(VERTICAL_TAB_WIDTH, actorWidth)));
        const localX = frameX + VERTICAL_TAB_LEFT_INSET;
        const localY = frameY + VERTICAL_TAB_HEADER_OFFSET;
        const x = Math.round(Math.max(0, Math.min(localX, actorWidth - tabWidth)));
        const y = Math.round(Math.max(0, Math.min(localY, actorHeight - 1)));
        const tabHeight = Math.round(Math.min(
            Math.max(1, actorHeight - y),
            Math.max(
                VERTICAL_TAB_MIN_HEIGHT,
                record.bird.name.length * VERTICAL_TAB_LETTER_HEIGHT + 14)));

        record.verticalTab.set_position(x, y);
        record.verticalTab.set_size(tabWidth, tabHeight);
        record.verticalTab.show();
    }

    _isMaximized(window) {
        const maximized = safeCall(window, 'get_maximized', 0);
        if (typeof maximized === 'number')
            return maximized !== 0;

        return Boolean(maximized);
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
            handle_source: record.handleSource,
        }));
    }

    _listApps() {
        return Gio.AppInfo.get_all()
            .filter(appInfo => appInfo.should_show())
            .map(appInfo => ({
                id: appInfo.get_id() ?? '',
                name: appInfo.get_name() ?? '',
            }))
            .sort((left, right) => left.name.localeCompare(right.name));
    }

    _findApp(query) {
        const normalizedQuery = normalizeSearchTerm(query);
        if (!normalizedQuery)
            return null;

        for (const appId of APP_ALIASES[normalizedQuery] ?? []) {
            const app = this._appSystem.lookup_app(appId);
            if (app)
                return {app, appInfo: app.get_app_info()};
        }

        const apps = Gio.AppInfo.get_all()
            .filter(appInfo => appInfo.should_show())
            .map(appInfo => ({
                appInfo,
                id: appInfo.get_id() ?? '',
                name: appInfo.get_name() ?? '',
            }));

        const exact = apps.find(entry =>
            normalizeSearchTerm(entry.name) === normalizedQuery ||
            normalizeSearchTerm(entry.id.replace(/\.desktop$/, '')) === normalizedQuery);
        if (exact)
            return this._appMatch(exact);

        const contains = apps.filter(entry =>
            normalizeSearchTerm(entry.name).includes(normalizedQuery) ||
            normalizeSearchTerm(entry.id).includes(normalizedQuery));
        if (contains.length === 1)
            return this._appMatch(contains[0]);

        return null;
    }

    _appMatch(entry) {
        const app = entry.id ? this._appSystem.lookup_app(entry.id) : null;
        return {app, appInfo: entry.appInfo};
    }

    _emitPasteShortcut() {
        const keyvals = this._focusedWindowUsesTerminalPaste()
            ? [Clutter.KEY_Control_L, Clutter.KEY_Shift_L, Clutter.KEY_v]
            : [Clutter.KEY_Control_L, Clutter.KEY_v];

        this._emitKeyvals(keyvals);
    }

    _emitKeyvals(keyvals) {
        const backend = Clutter.get_default_backend();
        const seat = backend.get_default_seat();
        const device = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);

        let offset = 0;
        for (const keyval of keyvals) {
            this._notifyKeyval(device, keyval, Clutter.KeyState.PRESSED, offset);
            offset += KEY_PAUSE_MS;
        }

        for (const keyval of [...keyvals].reverse()) {
            this._notifyKeyval(device, keyval, Clutter.KeyState.RELEASED, offset);
            offset += KEY_PAUSE_MS;
        }
    }

    _notifyKeyval(device, keyval, state, offset) {
        device.notify_keyval(global.get_current_time() + offset, keyval, state);
    }

    _focusedWindowUsesTerminalPaste() {
        const window = global.display.focus_window;
        if (!window)
            return false;

        const wmClass = normalizeSearchTerm(safeCall(window, 'get_wm_class', '') ?? '');
        return [
            'terminal',
            'ptyxis',
            'kgx',
            'konsole',
            'kitty',
            'alacritty',
            'wezterm',
        ].some(name => wmClass.includes(name));
    }

    _emitWindowsChanged() {
        if (this._dbusImpl)
            this._dbusImpl.emit_signal('WindowsChanged', null);
    }
}
