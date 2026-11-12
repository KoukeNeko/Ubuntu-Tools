// Top bar manager: hide items, reorder them within their box, and give every
// item the same padding, with overrides for single items.
//
// Items go by their status area role. App indicators get theirs from a D-Bus
// name that changes every session, so they go by the id the app reports.
// The items found are written back to the settings for Tuneup to list.

import GLib from 'gi://GLib';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BOXES = [['left', '_leftBox'], ['center', '_centerBox'], ['right', '_rightBox']];
const APP_INDICATOR_ROLE = 'appindicator-';

export default class PanelManager extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._hidden = new Map();   // container -> whether the panel wants it shown
        this._styled = new Map();   // button -> its style before ours
        this._syncId = 0;
        this._settings.connectObject('changed', (_settings, key) => {
            if (key !== 'items')
                this._queueSync();
        }, this);
        for (const [, name] of BOXES) {
            Main.panel[name].connectObject(
                'child-added', () => this._queueSync(),
                'child-removed', () => this._queueSync(),
                this);
        }
        this._sync();
    }

    disable() {
        if (this._syncId)
            GLib.source_remove(this._syncId);
        this._syncId = 0;
        for (const [, name] of BOXES)
            Main.panel[name].disconnectObject(this);
        this._settings.disconnectObject(this);
        for (const container of [...this._hidden.keys()])
            this._unhide(container);
        for (const button of [...this._styled.keys()])
            this._unstyle(button);
        this._settings = null;
    }

    _queueSync() {
        if (this._syncId)
            return;
        this._syncId = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._syncId = 0;
            this._sync();
            return GLib.SOURCE_REMOVE;
        });
    }

    _sync() {
        this._reorder();
        const items = this._items();
        this._applyHidden(items);
        this._applySpacing(items);
        const published = JSON.stringify(items.map(({id, name, box}) => ({id, name, box})));
        if (published !== this._settings.get_string('items'))
            this._settings.set_string('items', published);
    }

    // Every item on the bar, box by box, in the order shown.
    _items() {
        const byContainer = new Map();
        for (const [role, button] of Object.entries(Main.panel.statusArea)) {
            if (!button?.container)
                continue;
            const app = role.startsWith(APP_INDICATOR_ROLE) ? button._indicator : null;
            byContainer.set(button.container, {
                id: app?.id ? `appindicator:${app.id}` : role,
                name: app?.title || app?.id || role,
                button,
                container: button.container,
            });
        }
        return BOXES.flatMap(([box, name]) => Main.panel[name].get_children()
            .filter(container => byContainer.has(container))
            .map(container => ({...byContainer.get(container), box})));
    }

    // The listed items take the places the listed items had, in the listed
    // order; anything not listed, such as an item new since, stays put.
    _reorder() {
        const order = this._settings.get_strv('order');
        const items = this._items();
        for (const [box, name] of BOXES) {
            const parent = Main.panel[name];
            const listed = new Map(items.filter(i => i.box === box && order.includes(i.id)).map(i => [i.container, i.id]));
            const queue = [...listed.keys()].sort((a, b) => order.indexOf(listed.get(a)) - order.indexOf(listed.get(b)));
            const wanted = parent.get_children().map(child => listed.has(child) ? queue.shift() : child);
            wanted.forEach((child, i) => {
                if (parent.get_child_at_index(i) !== child)
                    parent.set_child_at_index(child, i);
            });
        }
    }

    _applyHidden(items) {
        const hidden = new Set(this._settings.get_strv('hidden'));
        for (const {id, container} of items) {
            if (hidden.has(id) && !this._hidden.has(container)) {
                this._hidden.set(container, container.visible);
                container.hide();
                // Items show and hide their button; the container is the panel's, which shows it on every add.
                container.connectObject(
                    'notify::visible', () => this._keepHidden(container),
                    'destroy', () => this._hidden.delete(container),
                    this);
            } else if (!hidden.has(id) && this._hidden.has(container)) {
                this._unhide(container);
            }
        }
    }

    // A hidden container cannot be hidden again, so the panel's wish only
    // ever changes to shown while it is ours.
    _keepHidden(container) {
        if (!container.visible)
            return;
        this._hidden.set(container, true);
        container.hide();
    }

    _unhide(container) {
        const wantsShown = this._hidden.get(container);
        container.disconnectObject(this);
        this._hidden.delete(container);
        if (wantsShown)
            container.show();
    }

    _applySpacing(items) {
        const custom = this._settings.get_boolean('custom-spacing');
        const spacing = this._settings.get_int('spacing');
        const overrides = this._settings.get_value('item-spacing').deepUnpack();
        for (const {id, button} of items) {
            if (!custom) {
                this._unstyle(button);
                continue;
            }
            if (!this._styled.has(button)) {
                this._styled.set(button, button.get_style());
                button.connectObject('destroy', () => this._styled.delete(button), this);
            }
            const padding = overrides[id] ?? spacing;
            // A crowded bar falls back to the minimum padding, which would undo the chosen one.
            button.set_style(`${this._styled.get(button) ?? ''} -natural-hpadding: ${padding}px; ` +
                `-minimum-hpadding: ${padding}px;`);
            // The button reads its padding on style changes but only uses it on the next layout.
            button.queue_relayout();
        }
    }

    _unstyle(button) {
        if (!this._styled.has(button))
            return;
        button.set_style(this._styled.get(button));
        button.queue_relayout();
        button.disconnectObject(this);
        this._styled.delete(button);
    }
}
