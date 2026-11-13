# Tuneup

Tweaks, extensions and system tools for Ubuntu, in one application styled
like GNOME Settings. Every change it makes to settings is recorded and can be
undone.

## Run

```sh
git clone --recurse-submodules https://github.com/KoukeNeko/Ubuntu-Tools.git
python3 Ubuntu-Tools/tuneup.py
```

Needs Python 3.12+, PyGObject, GTK 4 and libadwaita 1.5+. PyGObject comes
from the system packages, so use the system Python (`/usr/bin/python3`) if
another one is first on your `PATH`.

## Build the package

```sh
./packaging/build-deb.sh
sudo apt install ./dist/tuneup_*.deb
```

## Move your setup to another machine

A profile holds the tweaks Tuneup manages, which extensions are enabled, and
the settings of every installed extension that ships its own schema, such as
Dhruva and Blur my Shell.

1. On the old machine, open **Profiles**, create a profile from
   the current state and export it as a `.tuneup` file.
2. On the new machine, install Tuneup and the same extensions, then import
   the file and apply it.
3. Log out and back in.

A profile does not install extensions, and it skips settings of extensions
that are missing. Sidebar blur is a stylesheet in `~/.config/gtk-4.0/gtk.css`
and not a setting, so turn it on again in Tuneup. Settings tied to one
machine, such as a monitor number or a path to a picture, may need changing
by hand.

## Test

```sh
python3 -m unittest discover tests
```

## Licence

Tuneup is free software under the GNU General Public License, version 3 or
later. See [LICENSE](LICENSE).

It bundles [Dhruva](https://github.com/NarkAgni/dhruva), a dock for GNOME
Shell, Copyright © 2026 NarkAgni, licensed under the GNU General Public
License version 3. It is included as a git submodule in
`extension/dhruva@narkagni`, from [a fork](https://github.com/KoukeNeko/dhruva)
with changes that are not all upstream yet; the fork's history lists them.
Dhruva's licence is in that folder.
