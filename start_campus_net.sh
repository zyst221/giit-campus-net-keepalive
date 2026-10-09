#!/bin/sh

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd) || exit 1
exec python3 "$SCRIPT_DIR/campus_net_keepalive.py" "$@"