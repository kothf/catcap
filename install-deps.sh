#!/usr/bin/env bash
# Install the system packages CatCap needs to run from this folder (Linux Mint 21/22, Ubuntu 22.04+).
set -euo pipefail

required=(python3 python3-pyqt6 python3-pyqt6.qtsvg python3-xlib xdg-utils libglib2.0-bin)
optional=(qt6-image-formats-plugins qt6-wayland xdg-desktop-portal xdg-desktop-portal-xapp
          xdg-desktop-portal-gtk dpkg-dev)

available=()
for pkg in "${optional[@]}"; do
    if apt-cache show "$pkg" >/dev/null 2>&1; then
        available+=("$pkg")
    fi
done

echo "Installing: ${required[*]} ${available[*]}"
sudo apt-get install -y "${required[@]}" "${available[@]}"
echo
echo "Done. Start CatCap with: $(dirname "$(realpath "$0")")/run-catcap"
