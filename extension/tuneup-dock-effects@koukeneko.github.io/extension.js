// Dock effects for Ubuntu Dock / Dash to Dock: magnification under the
// pointer and a bounce while an application starts.
//
// The dock is found by its actor name rather than by importing the dock
// extension, so this keeps working with either dock and neither is modified.
// Every handler is wrapped: a failure here must never take the shell down.

import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const DOCK_NAME = 'dashtodockContainer';
const RANGE = 2.5;            // how many icon widths the magnification reaches
const RESET_MS = 160;
const BOUNCE_MS = 280;
const BOUNCE_HEIGHT = 0.35;   // fraction of the icon size
const MAX_BOUNCES = 6;
const RESCAN_SECONDS = 3;     // docks are recreated on monitor or setting changes

function findDocks(actor, depth = 0, found = []) {
    if (actor.get_name?.() === DOCK_NAME)
        found.push(actor);
    else if (depth < 4)
        actor.get_children().forEach(child => findDocks(child, depth + 1, found));
    return found;
}

function safely(what, fn) {
    try {
        return fn();
    } catch (e) {
        console.error(`tuneup-dock-effects: ${what}: ${e}`);
        return undefined;
    }
}

export default class DockEffects extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._docks = new Map();   // dock actor -> signal handler ids
        this._appSystem = Shell.AppSystem.get_default();
        this._appStateId = this._appSystem.connect('app-state-changed',
            (_system, app) => safely('bounce', () => this._onAppState(app)));
        this._settingsId = this._settings.connect('changed::magnify',
            () => safely('reset', () => this._docks.forEach((_ids, dock) => this._reset(dock))));
        this._rescanId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, RESCAN_SECONDS, () => {
            safely('rescan', () => this._attachDocks());
            return GLib.SOURCE_CONTINUE;
        });
        safely('attach', () => this._attachDocks());
    }

    disable() {
        if (this._rescanId)
            GLib.source_remove(this._rescanId);
        this._rescanId = null;
        this._appSystem?.disconnect(this._appStateId);
        this._settings?.disconnect(this._settingsId);
        this._docks?.forEach((ids, dock) => safely('detach', () => {
            ids.forEach(id => dock.disconnect(id));
            this._reset(dock, false);
        }));
        this._docks = null;
        this._appSystem = null;
        this._settings = null;
    }

    _attachDocks() {
        const present = new Set(findDocks(Main.layoutManager.uiGroup));
        for (const dock of this._docks.keys()) {
            if (!present.has(dock))
                this._docks.delete(dock);   // destroyed; its signals went with it
        }
        for (const dock of present) {
            if (this._docks.has(dock))
                continue;
            const ids = [
                dock.connect('captured-event', (_actor, event) => {
                    safely('pointer', () => this._onEvent(dock, event));
                    return Clutter.EVENT_PROPAGATE;
                }),
                dock.connect('leave-event', () => {
                    safely('leave', () => this._resetIfOutside(dock));
                    return Clutter.EVENT_PROPAGATE;
                }),
                dock.connect('destroy', () => this._docks?.delete(dock)),
            ];
            this._docks.set(dock, ids);
        }
    }

    // The icon containers inside the dock, in visual order.
    _items(dock) {
        const box = dock.dash?._box;
        return box ? box.get_children().filter(item => item.child?.icon && item.visible) : [];
    }

    // Which screen edge the dock sits on decides the axis and the pivot.
    _geometry(dock) {
        const [x, y] = dock.get_transformed_position();
        const [w, h] = dock.get_transformed_size();
        const monitor = Main.layoutManager.findMonitorForActor(dock) ?? Main.layoutManager.primaryMonitor;
        const vertical = h > w;
        if (vertical) {
            const left = x + w / 2 < monitor.x + monitor.width / 2;
            return {vertical, pivot: [left ? 0 : 1, 0.5], outward: left ? 1 : -1, bounds: [x, y, w, h]};
        }
        const top = y + h / 2 < monitor.y + monitor.height / 2;
        return {vertical, pivot: [0.5, top ? 0 : 1], outward: top ? 1 : -1, bounds: [x, y, w, h]};
    }

    _onEvent(dock, event) {
        const type = event.type();
        if (type === Clutter.EventType.MOTION) {
            if (this._settings.get_boolean('magnify')) {
                const [x, y] = event.get_coords();
                this._magnify(dock, x, y);
            }
        } else if (type === Clutter.EventType.LEAVE) {
            this._resetIfOutside(dock);
        }
    }

    _resetIfOutside(dock) {
        const [px, py] = global.get_pointer();
        const [x, y, w, h] = this._geometry(dock).bounds;
        if (px < x || px > x + w || py < y || py > y + h)
            this._reset(dock);
    }

    _magnify(dock, pointerX, pointerY) {
        const items = this._items(dock);
        if (!items.length)
            return;
        const {vertical, pivot} = this._geometry(dock);
        const maxScale = this._settings.get_double('magnify-scale');
        const pointer = vertical ? pointerY : pointerX;

        // Distances use each item's resting position, so the result depends
        // only on the pointer and does not feed back into itself.
        const sizes = [];
        const scales = items.map(item => {
            const [ix, iy] = item.get_transformed_position();
            const [iw, ih] = item.get_transformed_size();
            const size = vertical ? ih : iw;
            const start = vertical ? iy - item.translation_y : ix - item.translation_x;
            const distance = Math.abs(pointer - (start + size / 2)) / Math.max(size, 1);
            const t = Math.max(0, 1 - distance / RANGE);
            sizes.push(size);
            return 1 + (maxScale - 1) * (0.5 - 0.5 * Math.cos(Math.PI * t));
        });

        // Spread the neighbours apart by the space the enlarged icons need,
        // keeping the whole row centred as macOS does.
        const extras = scales.map((s, i) => sizes[i] * (s - 1));
        let offset = -extras.reduce((a, b) => a + b, 0) / 2;
        items.forEach((item, i) => {
            const shift = offset + extras[i] / 2;
            offset += extras[i];
            const icon = item.child;
            icon.remove_all_transitions();
            item.remove_all_transitions();
            icon.set_pivot_point(pivot[0], pivot[1]);
            icon.set_scale(scales[i], scales[i]);
            if (vertical)
                item.translation_y = shift;
            else
                item.translation_x = shift;
        });
    }

    _reset(dock, animate = true) {
        this._items(dock).forEach(item => {
            const icon = item.child;
            if (animate) {
                icon.ease({scale_x: 1, scale_y: 1, duration: RESET_MS, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
                item.ease({translation_x: 0, translation_y: 0, duration: RESET_MS, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
            } else {
                icon.remove_all_transitions();
                item.remove_all_transitions();
                icon.set_scale(1, 1);
                item.translation_x = 0;
                item.translation_y = 0;
            }
        });
    }

    _onAppState(app) {
        if (!this._settings.get_boolean('bounce') || app.state !== Shell.AppState.STARTING)
            return;
        this._docks.forEach((_ids, dock) => {
            const item = this._items(dock).find(i => i.child.app === app);
            if (item)
                this._bounce(dock, item, app, 0);
        });
    }

    // Bounce the inner icon, not its container: the container's translation
    // belongs to the magnification.
    _bounce(dock, item, app, count) {
        const icon = item.child?.icon;
        if (!icon || !this._settings)
            return;
        const {vertical, outward} = this._geometry(dock);
        const [iw, ih] = item.get_transformed_size();
        const property = vertical ? 'translation_x' : 'translation_y';
        const height = outward * BOUNCE_HEIGHT * (vertical ? iw : ih);
        icon.ease({
            [property]: height,
            duration: BOUNCE_MS,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            onComplete: () => icon.ease({
                [property]: 0,
                duration: BOUNCE_MS,
                mode: Clutter.AnimationMode.EASE_IN_QUAD,
                onComplete: () => safely('bounce', () => {
                    if (count + 1 < MAX_BOUNCES && app.state === Shell.AppState.STARTING)
                        this._bounce(dock, item, app, count + 1);
                }),
            }),
        });
    }
}
