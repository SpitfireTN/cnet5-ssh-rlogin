#!/bin/bash
set -euo pipefail

echo "== installing relay.py to /usr/local/bin (proper location, avoids touching home dir permissions) =="
sudo cp /home/spitfiretn/cnet-re/ssh-proxy/relay.py /usr/local/bin/cnet-ssh-relay.py
sudo chown root:root /usr/local/bin/cnet-ssh-relay.py
sudo chmod 755 /usr/local/bin/cnet-ssh-relay.py

echo "== updating sshd Match block to point at the new location =="
sudo tee /etc/ssh/sshd_config.d/bbs-gateway.conf > /dev/null <<'EOF'
Match User bbsguest
    PermitEmptyPasswords yes
    ForceCommand /usr/local/bin/cnet-ssh-relay.py
    PermitTTY yes
    X11Forwarding no
    AllowTcpForwarding no
    AllowAgentForwarding no
    PermitOpen none
    Banner none
EOF

echo "== checking sshd config syntax =="
sudo sshd -t

echo "== reloading sshd =="
sudo systemctl reload ssh

echo "Done."
