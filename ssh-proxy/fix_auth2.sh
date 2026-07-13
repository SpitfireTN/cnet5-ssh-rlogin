#!/bin/bash
set -euo pipefail

echo "== updating sshd Match block: dropping AuthenticationMethods restriction (was rejecting the PAM keyboard-interactive completion) =="
sudo tee /etc/ssh/sshd_config.d/bbs-gateway.conf > /dev/null <<'EOF'
Match User bbsguest
    PermitEmptyPasswords yes
    ForceCommand /home/spitfiretn/cnet-re/ssh-proxy/relay.py
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

echo
echo "Done."
