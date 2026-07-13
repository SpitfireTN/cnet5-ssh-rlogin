#!/bin/bash
set -euo pipefail

echo "== creating 'call' account =="
sudo useradd -m -s /bin/bash call
sudo passwd -d call

echo "== updating sshd Match block to use 'call' instead of 'bbsguest' =="
sudo tee /etc/ssh/sshd_config.d/bbs-gateway.conf > /dev/null <<'EOF'
Match User call
    PermitEmptyPasswords yes
    ForceCommand /usr/local/bin/cnet-ssh-relay.py
    PermitTTY yes
    X11Forwarding no
    AllowTcpForwarding no
    AllowAgentForwarding no
    PermitOpen none
    Banner none
EOF

echo "== checking syntax =="
sudo sshd -t

echo "== reloading sshd =="
sudo systemctl reload ssh

echo "== removing old bbsguest account =="
sudo userdel -r bbsguest

echo "Done. New command for callers:  ssh call@call.rofbbs.com"
