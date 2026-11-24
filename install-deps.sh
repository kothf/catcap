#!/usr/bin/env bash
# Install the system packages CatCap needs to run from this folder (Linux Mint 21/22, Ubuntu 22.04+).
set -euo pipefail

required=(python3 python3-xlib xdg-utils libglib2.0-bin)
qt=(python3-pyqt6 python3-pyqt6.qtsvg)
optional=(qt6-svg-plugins qt6-image-formats-plugins qt6-wayland xdg-desktop-portal xdg-desktop-portal-xapp
          xdg-desktop-portal-gtk dpkg-dev)

available=()
for pkg in "${optional[@]}"; do
    if apt-cache show "$pkg" >/dev/null 2>&1; then
        available+=("$pkg")
    fi
done

if apt-cache show python3-pyqt6 >/dev/null 2>&1; then
    required+=("${qt[@]}")
    use_pip=0
else
    # Linux Mint 21 / Ubuntu 22.04 have no PyQt6 package: take it from PyPI for this user instead.
    required+=(python3-pip)
    use_pip=1
fi

echo "Installing: ${required[*]} ${available[*]}"
sudo apt-get install -y "${required[@]}" "${available[@]}"

if [ "$use_pip" = 1 ]; then
    echo "PyQt6 is not packaged for this release; installing it with pip for $USER."
    python3 -m pip install --user "PyQt6>=6.2"
fi
echo
echo "Done. Start CatCap with: $(dirname "$(realpath "$0")")/run-catcap"
