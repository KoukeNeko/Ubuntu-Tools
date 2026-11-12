#!/bin/sh
# Build dist/tuneup_<version>_all.deb from the working tree.
# The version comes from VERSION in tuneup.py so there is one source of truth.
set -eu

cd "$(dirname "$0")/.."

version=$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' tuneup.py)
[ -n "$version" ] || { echo "VERSION not found in tuneup.py" >&2; exit 1; }

# MAINTAINER can be set by CI; locally it falls back to the git identity.
maintainer=${MAINTAINER:-"$(git config user.name) <$(git config user.email)>"}

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
app="$stage/usr/share/tuneup"

install -d "$app/i18n" "$stage/usr/bin" "$stage/usr/share/applications" "$stage/DEBIAN"
install -m 644 tuneup.py store.py system.py audio.py market.py "$app/"
install -m 644 i18n/*.json "$app/i18n/"
install -m 644 packaging/io.github.doeshing.Tuneup.desktop "$stage/usr/share/applications/"

cat > "$stage/usr/bin/tuneup" <<'LAUNCHER'
#!/bin/sh
exec python3 /usr/share/tuneup/tuneup.py "$@"
LAUNCHER
chmod 755 "$stage/usr/bin/tuneup"

# mktemp and the caller's umask leave directories too tight or too loose for a package
chmod -R u=rwX,go=rX "$stage"

size=$(du -sk "$stage/usr" | cut -f1)
sed -e "s/@VERSION@/$version/" -e "s/@SIZE@/$size/" -e "s/@MAINTAINER@/$maintainer/" \
    packaging/control.in > "$stage/DEBIAN/control"

mkdir -p dist
out="dist/tuneup_${version}_all.deb"
fakeroot dpkg-deb --build "$stage" "$out" >/dev/null
echo "$out"
