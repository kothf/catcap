#!/usr/bin/env bash
# Install the system packages CatCap needs to run from this folder (Linux Mint 21/22, Ubuntu 22.04+).
set -euo pipefail

required=(python3 python3-gi python3-gi-cairo python3-cairo gir1.2-gtk-3.0 python3-pil gir1.2-wnck-3.0 xdg-utils)
optional=(gir1.2-xapp-1.0 gir1.2-ayatanaappindicator3-0.1 gir1.2-keybinder-3.0 xdg-desktop-portal
          xdg-desktop-portal-xapp xdg-desktop-portal-gtk dpkg-dev)

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
