# Tuneup

Tweaks, extensions and system tools for Ubuntu, in one application styled
like GNOME Settings. Every change it makes to settings is recorded and can be
undone.

## Run

```sh
git clone --recurse-submodules https://github.com/KoukeNeko/Ubuntu-Tools.git
python3 Ubuntu-Tools/tuneup.py
```

Needs Python 3.12+, PyGObject, GTK 4 and libadwaita 1.5+.

## Build the package

```sh
./packaging/build-deb.sh
sudo apt install ./dist/tuneup_*.deb
```

## Test

```sh
python3 -m unittest discover tests
```

## Licence

Tuneup is free software under the GNU General Public License, version 3 or
later. See [LICENSE](LICENSE).

It bundles [Dhruva](https://github.com/NarkAgni/dhruva), a dock for GNOME
Shell, Copyright © 2026 NarkAgni, licensed under the GNU General Public
License version 3. Dhruva is included unmodified as a git submodule in
`extension/dhruva@narkagni`; its licence is in that folder.
