#!/bin/bash
set -euo pipefail

echo "== clearing bbsguest's password (was locked, needs to be genuinely empty) =="
sudo passwd -d bbsguest

echo "== updating sshd Match block: AuthenticationMethods none isn't a real bypass, switching to password auth with an empty password =="
sudo tee /etc/ssh/sshd_config.d/bbs-gateway.conf > /dev/null <<'EOF'
Match User bbsguest
    AuthenticationMethods password
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
echo "Done. Test with:  ssh bbsguest@localhost"
echo "You'll see a 'Password:' prompt - just press Enter with nothing typed."
