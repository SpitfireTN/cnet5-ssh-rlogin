#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== checking doorparty.json exists =="
if [ ! -f "$DIR/doorparty.json" ]; then
    echo "No $DIR/doorparty.json yet."
    echo "Copy doorparty.example.json to doorparty.json, fill in your real DoorParty system tag, then re-run this script."
    exit 1
fi

echo "== locking down doorparty.json permissions =="
chmod 600 "$DIR/doorparty.json"

echo "== reminder: dpc2 must be installed and running separately, bound to 127.0.0.1 =="
echo "   (this script does not install dpc2 - see https://github.com/echicken/dpc2)"
echo "   dpc2's default bind is 0.0.0.0 - override it to 127.0.0.1 in its own config."

echo "== installing systemd unit =="
sudo cp "$DIR/doorparty-bridge.service" /etc/systemd/system/doorparty-bridge.service
sudo systemctl daemon-reload

echo "== enabling and starting doorparty-bridge =="
sudo systemctl enable --now doorparty-bridge

echo "== status =="
sudo systemctl --no-pager status doorparty-bridge

echo
echo "Done. Next steps inside a live BBS session:"
echo "  1. Launch ctelnet, use AH (Add Host) to add a host named 'DoorParty'"
echo "     pointing at 127.0.0.1:${RLOGIN_BRIDGE_PORT:-6513} (this bridge, NOT dpc2's port)."
echo "  2. Add to bbsmenu:  #2 Doors:internet_support/ctelnet DoorParty}"
