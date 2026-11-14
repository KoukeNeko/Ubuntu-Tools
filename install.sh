#!/bin/sh
# Install the latest Tuneup release from GitHub:
#   curl -fsSL https://raw.githubusercontent.com/KoukeNeko/Ubuntu-Tools/main/install.sh | sh
set -eu

repo=KoukeNeko/Ubuntu-Tools

url=$(curl -fsSL "https://api.github.com/repos/$repo/releases/latest" |
    sed -n 's/.*"browser_download_url": *"\([^"]*_all\.deb\)".*/\1/p' | head -n 1)
[ -n "$url" ] || { echo "No .deb found in the latest release of $repo" >&2; exit 1; }

# apt reads local packages as the _apt user, so the file must be readable by it.
dir=$(mktemp -d)
trap 'rm -rf "$dir"' EXIT
chmod 755 "$dir"
deb="$dir/$(basename "$url" | sed 's/%2B/+/g')"
echo "Downloading $url"
curl -fL --progress-bar -o "$deb" "$url"
chmod 644 "$deb"

sudo apt install -y "$deb"
echo "Installed. Log out and back in to load the top bar and Dock extensions."
