// Dock effects for Ubuntu Dock / Dash to Dock: magnification under the
// pointer, a bounce while an application starts, and window previews when
// the pointer rests on an icon.
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
const WATCH_MS = 150;         // how often an open preview checks where the pointer is
const LEAVE_MS = 300;         // grace period for crossing the gap between icon and preview
const MENU_MARGIN = 12;

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
        this._docks = new Map();   // dock actor -> [actor, signal handler id] pairs
        this._unclipped = new Map();   // dock actor -> what to restore once effects end
        this._bouncing = 0;
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
        this._clearHoverTimer();
        if (this._restoreId)
            GLib.source_remove(this._restoreId);
        this._restoreId = null;
        if (this._watchId)
            GLib.source_remove(this._watchId);
        this._watchId = null;
        safely('close preview', () => this._preview?.icon._previewMenu?.close());
        this._preview = null;
        this._hoverItem = null;
        this._appSystem?.disconnect(this._appStateId);
        this._settings?.disconnect(this._settingsId);
        this._docks?.forEach((ids, dock) => safely('detach', () => {
            ids.forEach(([actor, id]) => actor.disconnect(id));
            this._reset(dock, false);
            this._reclip(dock);
        }));
        this._unclipped = null;
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
            // Clutter only delivers events to reactive actors. The dock's outer
            // container is not reactive; its inner box (the one the dock itself
            // watches for hover) is, so listen there.
            const target = dock._box?.reactive ? dock._box : dock;
            const ids = [
                [target, target.connect('captured-event', (_actor, event) => {
                    safely('pointer', () => this._onEvent(dock, event));
                    return Clutter.EVENT_PROPAGATE;
                })],
                [target, target.connect('leave-event', () => {
                    safely('leave', () => this._resetIfOutside(dock));
                    return Clutter.EVENT_PROPAGATE;
                })],
                [dock, dock.connect('destroy', () => this._docks?.delete(dock))],
            ];
            this._docks.set(dock, ids);
        }
    }

    // The dock confines its icons to its own bar in several ways: the slide
    // container sets a clip, the scroll view has a fade effect and the dash is
    // drawn through an offscreen buffer, both sized to the bar. Any of them
    // cuts off an enlarged or bouncing icon. Lift them while an effect runs
    // and put them back afterwards: the slide clip is what hides an
    // auto-hiding dock.
    _unclip(dock) {
        if (this._unclipped.has(dock))
            return;
        const saved = [];
        const first = this._items(dock)[0];
        for (let actor = first?.get_parent(); actor && actor !== dock.get_parent(); actor = actor.get_parent()) {
            const entry = {actor, clip: actor.has_clip ? actor.get_clip() : null, toAllocation: actor.clip_to_allocation};
            if (entry.clip)
                actor.remove_clip();
            if (entry.toAllocation)
                actor.clip_to_allocation = false;
            if ('clip_to_view' in actor) {
                entry.toView = actor.clip_to_view;
                actor.clip_to_view = false;
            }
            entry.effects = actor.get_effects().filter(effect => effect.enabled);
            entry.effects.forEach(effect => (effect.enabled = false));
            entry.redirect = actor.offscreen_redirect;
            if (entry.redirect)
                actor.offscreen_redirect = 0;
            saved.push(entry);
        }
        this._unclipped.set(dock, saved);
    }

    _reclip(dock) {
        const saved = this._unclipped?.get(dock);
        if (!saved)
            return;
        this._unclipped.delete(dock);
        saved.forEach(({actor, clip, toAllocation, toView, effects, redirect}) => safely('reclip', () => {
            if (clip)
                actor.set_clip(...clip);
            if (toAllocation)
                actor.clip_to_allocation = true;
            if (toView !== undefined)
                actor.clip_to_view = toView;
            effects.forEach(effect => (effect.enabled = true));
            if (redirect)
                actor.offscreen_redirect = redirect;
        }));
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
            const [x, y] = event.get_coords();
            if (this._settings.get_boolean('magnify'))
                this._magnify(dock, x, y);
            if (this._settings.get_boolean('hover-previews'))
                this._onHover(dock, x, y);
        } else if (type === Clutter.EventType.LEAVE) {
            this._resetIfOutside(dock);
        }
    }

    // Hit-test against the icon as drawn: while magnified it is larger than,
    // and offset from, the container that holds it.
    _itemAt(dock, x, y) {
        return this._items(dock).find(item => {
            const [ix, iy] = item.child.get_transformed_position();
            const [iw, ih] = item.child.get_transformed_size();
            return x >= ix && x <= ix + iw && y >= iy && y <= iy + ih;
        });
    }

    _clearHoverTimer() {
        if (this._hoverTimer)
            GLib.source_remove(this._hoverTimer);
        this._hoverTimer = null;
    }

    _windowsOf(item) {
        const icon = item.child;
        return icon.getInterestingWindows?.() ?? icon.app?.get_windows() ?? [];
    }

    // The preview itself is the dock's own window preview menu, the one it
    // shows for its "previews" click action; this only opens it on hover.
    _canPreview(item) {
        const icon = item?.child;
        return typeof icon?._windowPreviews === 'function' && this._windowsOf(item).length > 0;
    }

    _onHover(dock, x, y) {
        const item = this._itemAt(dock, x, y);
        if (item === this._hoverItem)
            return;
        this._hoverItem = item;
        this._clearHoverTimer();
        if (!item || this._preview)
            return;   // an open preview follows the pointer from its own watcher
        this._hoverTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, this._settings.get_int('hover-delay'), () => {
            this._hoverTimer = null;
            safely('preview', () => {
                const [px, py] = global.get_pointer();
                if (this._itemAt(dock, px, py) === item)
                    this._showPreview(dock, item);
            });
            return GLib.SOURCE_REMOVE;
        });
    }

    _showPreview(dock, item) {
        const icon = item.child;
        if (Main.overview.visible || !this._canPreview(item) || icon._previewMenu?.isOpen || icon._menu?.isOpen)
            return;
        icon._windowPreviews();
        this._preview = {dock, item, icon};
        this._awayMs = 0;
        if (!this._watchId) {
            this._watchId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, WATCH_MS,
                () => safely('watch', () => this._watchPreview()) ?? this._stopWatching());
        }
    }

    // The menu's input grab also swallowed the pointer leaving the dock, so
    // once the preview is gone, undo the magnification if the pointer left.
    _stopWatching() {
        const dock = this._preview?.dock;
        this._preview = null;
        this._watchId = null;
        if (dock)
            safely('leave', () => this._resetIfOutside(dock));
        return GLib.SOURCE_REMOVE;
    }

    // While a preview is open the menu holds the input grab, so the dock no
    // longer sees pointer motion; poll instead. Close when the pointer has
    // left both the icon and the preview, or move to the icon it went to.
    _watchPreview() {
        const preview = this._preview;
        const menu = preview?.icon._previewMenu;
        if (!menu?.isOpen)
            return this._stopWatching();
        const [px, py] = global.get_pointer();
        const over = this._itemAt(preview.dock, px, py);
        const [mx, my] = menu.actor.get_transformed_position();
        const [mw, mh] = menu.actor.get_transformed_size();
        const inMenu = px >= mx - MENU_MARGIN && px <= mx + mw + MENU_MARGIN &&
            py >= my - MENU_MARGIN && py <= my + mh + MENU_MARGIN;
        if (over === preview.item || inMenu) {
            this._awayMs = 0;
            return GLib.SOURCE_CONTINUE;
        }
        if (over && this._canPreview(over)) {
            menu.close();
            this._showPreview(preview.dock, over);
            return this._preview ? GLib.SOURCE_CONTINUE : this._stopWatching();
        }
        this._awayMs += WATCH_MS;
        if (this._awayMs < LEAVE_MS)
            return GLib.SOURCE_CONTINUE;
        menu.close();
        return this._stopWatching();
    }

    _resetIfOutside(dock) {
        const [px, py] = global.get_pointer();
        const [x, y, w, h] = this._geometry(dock).bounds;
        if (px < x || px > x + w || py < y || py > y + h) {
            this._reset(dock);
            // Forget the hovered icon, or coming back to the same one would
            // look like "still hovering" and never open its preview again.
            this._hoverItem = null;
            this._clearHoverTimer();
        }
    }

    _magnify(dock, pointerX, pointerY) {
        const items = this._items(dock);
        if (!items.length)
            return;
        const {vertical, pivot, bounds} = this._geometry(dock);
        const maxScale = this._settings.get_double('magnify-scale');
        const pointer = vertical ? pointerY : pointerX;

        // Work from each item's resting position, so the result depends only
        // on the pointer and does not feed back into itself.
        const starts = [];
        const sizes = [];
        const scales = items.map(item => {
            const [ix, iy] = item.get_transformed_position();
            const [iw, ih] = item.get_transformed_size();
            const size = vertical ? ih : iw;
            const start = vertical ? iy - item.translation_y : ix - item.translation_x;
            const distance = Math.abs(pointer - (start + size / 2)) / Math.max(size, 1);
            const t = Math.max(0, 1 - distance / RANGE);
            starts.push(start);
            sizes.push(size);
            return 1 + (maxScale - 1) * (0.5 - 0.5 * Math.cos(Math.PI * t));
        });

        // Enlarged icons need more room, so the row spreads. Spread it around
        // the pointer: the spot under the pointer must stay under the pointer,
        // otherwise the icon slides away from the cursor as it grows.
        const extras = scales.map((s, i) => sizes[i] * (s - 1));
        const beforePointer = extras.reduce((sum, extra, i) => {
            const covered = Math.min(1, Math.max(0, (pointer - starts[i]) / Math.max(sizes[i], 1)));
            return sum + extra * covered;
        }, 0);
        let running = -beforePointer;
        const shifts = extras.map(extra => {
            const shift = running + extra / 2;
            running += extra;
            return shift;
        });

        // A dock that spans the whole screen edge has no room to grow outwards:
        // keep the first and last icons within the dock's own ends.
        const [dockStart, dockLength] = vertical ? [bounds[1], bounds[3]] : [bounds[0], bounds[2]];
        const last = items.length - 1;
        const roomBefore = Math.max(0, starts[0] - dockStart);
        const roomAfter = Math.max(0, dockStart + dockLength - (starts[last] + sizes[last]));
        const overEnd = Math.max(0, shifts[last] + extras[last] / 2 - roomAfter);
        const overStart = Math.max(0, -roomBefore - (shifts[0] - extras[0] / 2));
        const nudge = overStart > 0 ? overStart : -overEnd;

        this._unclip(dock);
        items.forEach((item, i) => {
            const icon = item.child;
            icon.remove_all_transitions();
            item.remove_all_transitions();
            icon.set_pivot_point(pivot[0], pivot[1]);
            icon.set_scale(scales[i], scales[i]);
            if (vertical)
                item.translation_y = shifts[i] + nudge;
            else
                item.translation_x = shifts[i] + nudge;
        });
    }

    _reset(dock, animate = true) {
        this._items(dock).forEach(item => {
            const icon = item.child;
            if (animate) {
                icon.ease({scale_x: 1, scale_y: 1, duration: RESET_MS, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
                item.ease({translation_x: 0, translation_y: 0, duration: RESET_MS, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
                if (this._restoreId)
                    GLib.source_remove(this._restoreId);
                this._restoreId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, RESET_MS + 40, () => {
                    this._restoreId = null;
                    safely('reclip', () => this._reclipIfIdle(dock));
                    return GLib.SOURCE_REMOVE;
                });
            } else {
                icon.remove_all_transitions();
                item.remove_all_transitions();
                icon.set_scale(1, 1);
                item.translation_x = 0;
                item.translation_y = 0;
            }
        });
    }

    // Only put the clips back when nothing is enlarged and nothing is bouncing.
    _reclipIfIdle(dock) {
        const enlarged = this._items(dock).some(item => item.child.scale_x > 1.001);
        if (!enlarged && !this._bouncing)
            this._reclip(dock);
    }

    _onAppState(app) {
        if (!this._settings.get_boolean('bounce') || app.state !== Shell.AppState.STARTING)
            return;
        this._docks.forEach((_ids, dock) => {
            const item = this._items(dock).find(i => i.child.app === app);
            if (item) {
                this._bouncing++;
                this._unclip(dock);
                this._bounce(dock, item, app, 0);
            }
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
                    if (count + 1 < MAX_BOUNCES && app.state === Shell.AppState.STARTING) {
                        this._bounce(dock, item, app, count + 1);
                    } else {
                        this._bouncing = Math.max(0, this._bouncing - 1);
                        this._reclipIfIdle(dock);
                    }
                }),
            }),
        });
    }
}
