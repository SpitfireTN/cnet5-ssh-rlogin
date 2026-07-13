#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== checking accounts.json exists =="
if [ ! -f "$DIR/accounts.json" ]; then
    echo "No $DIR/accounts.json yet."
    echo "Copy accounts.example.json to accounts.json, fill in real handle/password pairs, then re-run this script."
    exit 1
fi

echo "== locking down accounts.json permissions (contains plaintext BBS passwords) =="
chmod 600 "$DIR/accounts.json"

echo "== installing systemd unit =="
sudo cp "$DIR/rlogin-gateway.service" /etc/systemd/system/rlogin-gateway.service
sudo systemctl daemon-reload

echo "== enabling and starting rlogin-gateway =="
sudo systemctl enable --now rlogin-gateway

echo "== status =="
sudo systemctl --no-pager status rlogin-gateway

echo
echo "Done. Test with:  rlogin -l <handle> <this-host>"
echo "(or: echo -ne '\\0client\\0<handle>\\0ansi/38400\\0' | nc <this-host> 513)"
