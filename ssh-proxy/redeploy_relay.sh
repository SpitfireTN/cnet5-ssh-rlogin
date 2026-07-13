#!/bin/bash
set -euo pipefail
sudo cp /home/spitfiretn/cnet-re/ssh-proxy/relay.py /usr/local/bin/cnet-ssh-relay.py
sudo chown root:root /usr/local/bin/cnet-ssh-relay.py
sudo chmod 755 /usr/local/bin/cnet-ssh-relay.py
echo "Done."
