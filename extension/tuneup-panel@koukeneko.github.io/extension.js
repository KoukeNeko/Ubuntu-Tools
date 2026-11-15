// Top bar manager: hide items, reorder them within their box, and give the
// items of a box the same padding, with overrides for single items. Menus
// opened from the bar appear and go at once, as a menu bar's do. Holding
// Ctrl, an item can be dragged to another place in its box.
//
// Items go by their status area role. App indicators get theirs from a D-Bus
// name that changes every session, so they go by the id the app reports.
// The items found are written back to the settings for Tuneup to list.

import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';

import * as BoxPointer from 'resource:///org/gnome/shell/ui/boxpointer.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension, InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';

const BOXES = [['left', '_leftBox'], ['center', '_centerBox'], ['right', '_rightBox']];
const APP_INDICATOR_ROLE = 'appindicator-';
const GLASS_MENUS_CLASS = 'tuneup-glass-menus';

// Blur my Shell draws a menu's blur a frame or two after the menu first
// shows, which a fade used to hide. Without one, the menu stays invisible for
// those frames and then appears at once, already blurred.
const BLUR_SETTLE_FRAMES = 2;

function holdUntilBlurred(boxPointer, opening) {
    if (boxPointer._tuneupHoldId) {
        global.stage.disconnect(boxPointer._tuneupHoldId);
        boxPointer._tuneupHoldId = 0;
    }
    if (!opening)
        return;

    boxPointer.opacity = 0;
    let frames = BLUR_SETTLE_FRAMES;
    boxPointer._tuneupHoldId = global.stage.connect('after-paint', () => {
        if (--frames > 0)
            return;
        global.stage.disconnect(boxPointer._tuneupHoldId);
        boxPointer._tuneupHoldId = 0;
        boxPointer.opacity = 255;
    });
}

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
        this._injections = new InjectionManager();
        for (const method of ['open', 'close']) {
            this._injections.overrideMethod(BoxPointer.BoxPointer.prototype, method, original =>
                function (animate, onComplete) {
                    const fromBar = this._sourceActor && Main.panel.contains(this._sourceActor);
                    original.call(this, fromBar ? BoxPointer.PopupAnimation.NONE : animate, onComplete);
                    if (fromBar)
                        holdUntilBlurred(this, method === 'open');
                });
        }
        this._drag = null;
        Main.panel.connectObject('captured-event', (_panel, event) => this._onPanelEvent(event), this);
        this._sync();
    }

    disable() {
        this._endDrag();
        Main.panel.disconnectObject(this);
        this._injections.clear();
        this._injections = null;
        if (this._syncId)
            GLib.source_remove(this._syncId);
        this._syncId = 0;
        for (const [, name] of BOXES)
            Main.panel[name].disconnectObject(this);
        this._settings.disconnectObject(this);
        Main.uiGroup.remove_style_class_name(GLASS_MENUS_CLASS);
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
        if (this._settings.get_boolean('glass-menus'))
            Main.uiGroup.add_style_class_name(GLASS_MENUS_CLASS);
        else
            Main.uiGroup.remove_style_class_name(GLASS_MENUS_CLASS);
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

    _onPanelEvent(event) {
        const type = event.type();
        if (!this._drag) {
            if (type !== Clutter.EventType.BUTTON_PRESS || event.get_button() !== Clutter.BUTTON_PRIMARY ||
                !(event.get_state() & Clutter.ModifierType.CONTROL_MASK))
                return Clutter.EVENT_PROPAGATE;
            const boxes = BOXES.map(([, name]) => Main.panel[name]);
            let container = global.stage.get_event_actor(event);
            while (container && !boxes.includes(container.get_parent()))
                container = container.get_parent();
            if (!container)
                return Clutter.EVENT_PROPAGATE;
            this._drag = {container, grab: global.stage.grab(Main.panel), opacity: container.opacity};
            container.opacity = 128;
            return Clutter.EVENT_STOP;
        }
        if (type === Clutter.EventType.MOTION) {
            const {container} = this._drag;
            const parent = container.get_parent();
            // The item went away under the pointer, such as an app indicator quitting.
            if (!parent) {
                this._endDrag();
                return Clutter.EVENT_STOP;
            }
            const [, x] = parent.transform_stage_point(...event.get_coords());
            const others = parent.get_children().filter(child => child !== container);
            const at = others.findIndex(child => child.visible && child.x + child.width / 2 > x);
            const index = at < 0 ? others.length : at;
            if (parent.get_child_at_index(index) !== container)
                parent.set_child_at_index(container, index);
        } else if (type === Clutter.EventType.BUTTON_RELEASE) {
            this._endDrag();
            this._settings.set_strv('order', this._items().map(i => i.id));
        }
        return Clutter.EVENT_STOP;
    }

    _endDrag() {
        if (!this._drag)
            return;
        this._drag.grab.dismiss();
        this._drag.container.opacity = this._drag.opacity;
        this._drag = null;
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
        const boxSpacing = this._settings.get_value('box-spacing').deepUnpack();
        const overrides = this._settings.get_value('item-spacing').deepUnpack();
        for (const {id, button, box} of items) {
            const spacing = boxSpacing[box];
            // A box without a padding of its own is left to the shell theme.
            if (spacing === undefined) {
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
