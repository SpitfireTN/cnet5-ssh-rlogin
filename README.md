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

Lets people `ssh call@<host>` and land straight in the BBS's telnet port.

- `relay.py` — the deployed relay, invoked as sshd's `ForceCommand` for the
  `call` account (renamed from `bbsguest`; see `rename_account.sh`). Puts
  the pty in raw mode, strips/answers telnet negotiation, synthesizes the
  ANSI cursor-position reply the BBS expects during terminal auto-detect,
  and transcodes the BBS's CP437 output to UTF-8 so callers on modern
  terminals see ANSI art correctly instead of raw high-byte garbage. This
  logic used to be imported from `shared/telnet_util.py` (still used by
  `rlogin-gateway/`) but is now self-contained in `relay.py` — CP437 is a
  single-byte encoding, so the transcode is safe to apply per-chunk with no
  buffering needed, even across an arbitrarily-fragmented TCP stream.
  Deployed to `/usr/local/bin/cnet-ssh-relay.py` (root:root, 755), not run
  from this checkout — see `relocate_relay.sh`.
- `fix_auth.sh` / `fix_auth2.sh` / `fix_shell.sh` / `redeploy_relay.sh` /
  `relocate_relay.sh` / `rename_account.sh` — one-off setup/repair scripts,
  roughly chronological, for the gateway account and the sshd `Match User`
  block. Historical repair steps, not a repeatable install script — read
  one before rerunning it.

Testing changes means editing/testing a copy, then `sudo cp`-ing it to
`/usr/local/bin/cnet-ssh-relay.py` (`redeploy_relay.sh` does this) — no sshd
reload needed, since it's invoked fresh per-connection.

## rlogin-gateway/ — RLOGIN auto-login + outbound DoorParty bridge

Two independent services that both speak RLOGIN (RFC 1282) at the BBS's
telnet port, run as always-on systemd services (unlike `relay.py`, which is
spawned per-connection).

- `rlogin_server.py` — **inbound**: listens on port 513, accepts the RLOGIN
  handshake, and if the requested `server_user` has an entry in
  `accounts.json` (copy from `accounts.example.json`, `chmod 600` — plaintext
  BBS passwords, not committed), scripts the pre-login prompts so the caller
  lands already logged in. Falls open to a plain relay after a 30s automation
  budget or once the scripted steps finish. Was written but never actually
  installed — port 513 had nothing listening, no `rlogin-gateway.service`
  unit existed, and `accounts.json` didn't exist yet. Fixed by running
  `setup_rlogin.sh`; it's now `enable --now`d and will survive reboots.
- `doorparty_bridge.py` — **outbound**: bridges the BBS's telnet-only
  `ctelnet` door to `dpc2` (DoorParty Connector v2, RLOGIN). Binds
  `127.0.0.1` only. Picks up the caller's handle from
  `SysData:anet_identity`, falling back to the shared `system_tag` in
  `doorparty.json` (copy from `doorparty.example.json`, not committed).
  Now installed properly: `doorparty-bridge`, `anet-bridge`,
  `rlogin-gateway` and `doorparty-connector` are all `enable --now`d and
  come up at boot (verified 2026-08-23: all four active+enabled, 513, 6513
  and 6514 listening).
  On the BBS-content side (not part of this repo — lives in
  `Amiberry/HardDrives/DH3/CNet/Doors/rlogin/a-net.rexx`), the caller-facing
  door that's supposed to hand off into this bridge was launching with
  `{& ANET;Q}`, an MCI "run a built-in system command" code — `ANET` was
  never a real system command, so CNet just rejected it and returned to the
  prompt. Fixed to use `{#2 cnet:doors/internet_support/ctelnet 127.0.0.1
  6513}`, the "run a program" MCI code (matching the working pattern in the
  BBSLink door), pointed at the bridge's actual listen address.
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
