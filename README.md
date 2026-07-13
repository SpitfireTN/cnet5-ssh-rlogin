# C-Net/5 SSH/RLogin

SSH and RLOGIN gateways in front of a live CNet/5 BBS (a closed-source Amiga
BBS engine, running under emulation, telnet-only on `127.0.0.1:6800`). Both
gateways drive the BBS from the outside rather than patching it.

## shared/

- `telnet_util.py` — telnet IAC negotiation + ANSI cursor-position handling
  shared by both gateways below. Exports `BbsStreamFilter` (strips/answers
  negotiation from BBS-sourced bytes) and `escape_caller_bytes` (doubles
  literal `0xFF` from the caller so it isn't misread as IAC).

## ssh-proxy/ — live SSH-to-telnet gateway

Lets people `ssh bbsguest@<host>` and land straight in the BBS's telnet port.

- `relay.py` — the deployed relay, invoked as sshd's `ForceCommand` for the
  `bbsguest` account. Puts the pty in raw mode and uses `shared/telnet_util.py`
  to strip/answer telnet negotiation and synthesize the ANSI cursor-position
  reply the BBS expects during terminal auto-detect.
- `fix_auth.sh` / `fix_auth2.sh` / `fix_shell.sh` / `redeploy_relay.sh` /
  `relocate_relay.sh` / `rename_account.sh` — one-off setup/repair scripts
  for the `bbsguest` account and the sshd `Match User bbsguest` block.
  Historical repair steps, not a repeatable install script — read one
  before rerunning it.

Testing changes to `relay.py` requires reloading sshd
(`sudo sshd -t && sudo systemctl reload ssh`) since it's invoked per-connection.

## rlogin-gateway/ — RLOGIN auto-login + outbound DoorParty bridge

Two independent services that both speak RLOGIN (RFC 1282) at the BBS's
telnet port, run as always-on systemd services (unlike `relay.py`, which is
spawned per-connection).

- `rlogin_server.py` — **inbound**: listens on port 513, accepts the RLOGIN
  handshake, and if the requested `server_user` has an entry in
  `accounts.json` (copy from `accounts.example.json`, `chmod 600` — plaintext
  BBS passwords, not committed), scripts the pre-login prompts so the caller
  lands already logged in. Falls open to a plain relay after a 30s automation
  budget or once the scripted steps finish.
- `doorparty_bridge.py` — **outbound**: bridges the BBS's telnet-only
  `ctelnet` door to `dpc2` (DoorParty Connector v2, RLOGIN). Binds
  `127.0.0.1` only. Picks up the caller's handle from
  `SysData:anet_identity`, falling back to the shared `system_tag` in
  `doorparty.json` (copy from `doorparty.example.json`, not committed).
- `test_rlogin_target.py` — throwaway RLOGIN echo server standing in for
  `dpc2` to validate the `ctelnet → doorparty_bridge → target` chain
  end-to-end before `dpc2`/DoorParty access exists.
- `setup_rlogin.sh` / `setup_doorparty_bridge.sh` — install the respective
  systemd unit and `chmod 600` the local config; each refuses to run until
  its `.json` config has been copied from the `.example.json` and filled in.

After editing either service's `.py`:
`sudo systemctl restart rlogin-gateway` or
`sudo systemctl restart doorparty-bridge` (no reload path — these run
always-on, not per-connection like `relay.py`).
