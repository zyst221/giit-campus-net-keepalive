#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
VERSION=1.0.3
PACKAGE_NAME=campus-net-keepalive
BUILD_ROOT=$(mktemp -d)
PACKAGE_ROOT="$BUILD_ROOT/package"
OUTPUT_DIR="$ROOT_DIR/dist"
OUTPUT_FILE="$OUTPUT_DIR/${PACKAGE_NAME}_${VERSION}_all.deb"
trap 'rm -rf "$BUILD_ROOT"' EXIT HUP INT TERM

install -Dm644 "$ROOT_DIR/campus_net_keepalive.py" \
    "$PACKAGE_ROOT/usr/lib/campus-net-keepalive/campus_net_keepalive.py"
install -Dm755 "$ROOT_DIR/packaging/campus-net-keepalive" \
    "$PACKAGE_ROOT/usr/bin/campus-net-keepalive"
install -Dm644 "$ROOT_DIR/packaging/debian/campus-net-keepalive.service" \
    "$PACKAGE_ROOT/usr/lib/systemd/user/campus-net-keepalive.service"
install -Dm644 "$ROOT_DIR/packaging/debian/campus-net-keepalive.desktop" \
    "$PACKAGE_ROOT/usr/share/applications/campus-net-keepalive.desktop"
install -Dm644 "$ROOT_DIR/packaging/campus-net-keepalive.svg" \
    "$PACKAGE_ROOT/usr/share/icons/hicolor/scalable/apps/campus-net-keepalive.svg"
install -Dm644 "$ROOT_DIR/packaging/campus-net-keepalive.png" \
    "$PACKAGE_ROOT/usr/share/icons/hicolor/128x128/apps/campus-net-keepalive.png"
install -Dm644 "$ROOT_DIR/packaging/debian/control" \
    "$PACKAGE_ROOT/DEBIAN/control"

mkdir -p "$OUTPUT_DIR"
dpkg-deb --build --root-owner-group "$PACKAGE_ROOT" "$OUTPUT_FILE"
printf 'Built %s\n' "$OUTPUT_FILE"