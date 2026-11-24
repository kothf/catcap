#!/usr/bin/env bash
# Build dist/catcap_<version>_all.deb with plain dpkg-deb (no debhelper needed).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/catcap/__init__.py")"
PKG="catcap"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
STAGE="$BUILD/${PKG}_${VERSION}_all"

install -d "$STAGE/DEBIAN" "$STAGE/usr/bin" "$STAGE/usr/lib/catcap" \
    "$STAGE/usr/share/applications" "$STAGE/usr/share/metainfo" "$STAGE/usr/share/doc/catcap" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps" "$STAGE/usr/share/icons/hicolor/symbolic/apps"

# Python package (no caches)
(cd "$ROOT" && find catcap -name '*.py' -print0 | while IFS= read -r -d '' f; do
    install -D -m 0644 "$f" "$STAGE/usr/lib/catcap/$f"
done)

cat > "$STAGE/usr/bin/catcap" <<'LAUNCH'
#!/usr/bin/python3
import sys
sys.path.insert(0, "/usr/lib/catcap")
from catcap.app import main
sys.exit(main())
LAUNCH
chmod 0755 "$STAGE/usr/bin/catcap"

install -m 0644 "$ROOT/data/io.github.kothf.CatCap.desktop" "$STAGE/usr/share/applications/"
install -m 0644 "$ROOT/data/io.github.kothf.CatCap.metainfo.xml" "$STAGE/usr/share/metainfo/"
install -m 0644 "$ROOT/data/icons/hicolor/scalable/apps/io.github.kothf.CatCap.svg" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps/"
install -m 0644 "$ROOT/data/icons/hicolor/symbolic/apps/io.github.kothf.CatCap-symbolic.svg" \
    "$STAGE/usr/share/icons/hicolor/symbolic/apps/"

{
    echo "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/"
    echo "Upstream-Name: CatCap"
    echo "Source: https://github.com/kothf/catcap"
    echo
    echo "Files: *"
    echo "Copyright: 2026 CatCap contributors"
    echo "License: MIT"
    sed 's/^/ /; s/^ $/ ./' "$ROOT/LICENSE"
} > "$STAGE/usr/share/doc/catcap/copyright"
printf 'catcap (%s) unstable; urgency=low\n\n  * Release %s.\n\n -- CatCap contributors <noreply@github.com>  %s\n' \
    "$VERSION" "$VERSION" "$(date -R)" | gzip -9n > "$STAGE/usr/share/doc/catcap/changelog.gz"

SIZE="$(du -sk "$STAGE/usr" | cut -f1)"
cat > "$STAGE/DEBIAN/control" <<CONTROL
Package: ${PKG}
Version: ${VERSION}
Section: graphics
Priority: optional
Architecture: all
Installed-Size: ${SIZE}
Depends: python3 (>= 3.10), python3-pyqt6, python3-pyqt6.qtsvg, python3-xlib
Recommends: qt6-image-formats-plugins, qt6-wayland, xdg-desktop-portal, xdg-desktop-portal-xapp | xdg-desktop-portal-gtk, xdg-utils, libglib2.0-bin
Suggests: gnome-session-canberra
Maintainer: CatCap contributors <noreply@github.com>
Homepage: https://github.com/kothf/catcap
Description: Screenshot capture, annotation and library app
 CatCap captures regions, windows or the whole screen, lets you annotate
 captures with arrows, text, callouts, numbered steps, blur and more, and keeps
 every capture in a searchable library with tags and collections.
 Works on X11 and Wayland (through xdg-desktop-portal).
CONTROL

cat > "$STAGE/DEBIAN/postinst" <<'POST'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
fi
exit 0
POST
cp "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"
sed -i 's/"\$1" = "configure"/"$1" = "remove" ] || [ "$1" = "purge"/' "$STAGE/DEBIAN/postrm"
chmod 0755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

mkdir -p "$ROOT/dist"
dpkg-deb --root-owner-group --build "$STAGE" "$ROOT/dist/${PKG}_${VERSION}_all.deb" >/dev/null
echo "Built $ROOT/dist/${PKG}_${VERSION}_all.deb"
