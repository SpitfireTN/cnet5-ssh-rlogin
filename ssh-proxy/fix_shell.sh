#!/bin/bash
set -euo pipefail

echo "== switching bbsguest's shell from nologin to a valid one (ForceCommand still fully restricts what runs) =="
sudo usermod -s /bin/bash bbsguest

echo "== verifying =="
getent passwd bbsguest

echo "Done."
