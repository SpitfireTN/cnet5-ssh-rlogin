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

### IPv6, and why a caller has to be *told* their own address

The BBS engine is **IPv4-only** — under emulation it binds `0.0.0.0` and there
is no IPv6 listener for it anywhere. An IPv6 session therefore can never
terminate on the BBS. It terminates on a relay on a second host (the site's
mail hub, which owns the public `AAAA` because **IPv6 has no NAT**, so one
hostname cannot be split across two machines by port the way IPv4 is) and is
forwarded inward over IPv4.

The consequence is easy to miss: the BBS — and `sshd` on the BBS host — see
the *relay* as the client, not the caller. `SSH_CONNECTION` carries the
relay's address. The caller's real IPv6 address exists at exactly one point in
the path, the relay, which is the last hop that still knows it.

That matters because IPv6 BBS listings expect a caller to be able to verify
their call really did arrive over IPv6, and nothing downstream of the relay
can show them. Note the useful corollary: arriving *from* the relay's address
is itself proof the session came in over IPv6, since the relay listens
`ipv6only` and an IPv4 caller is NAT-forwarded by the router keeping their own
address.

Two mechanisms, because the two protocols permit different things:

- **telnet** — the relay writes the address into the stream before bridging.
  `socat ... EXEC:` a small wrapper that reads **`SOCAT_PEERADDR`** (set by
  socat for `EXEC` children; it arrives *bracketed*, `[2001:db8::1]`), prints
  it, then `exec socat - TCP4:<bbs-host>:<port>`. The banner is plain ASCII
  with CRLF on purpose — C64 and Amiga terminals read it too, so no ANSI, no
  UTF-8, no colour.
- **SSH** — the stream is encrypted and framed, so nothing can be injected
  into it without corrupting it. Instead the relay *remembers* the caller,
  keyed by **the source port of its own outbound connection** to the BBS host.
  `sshd` reports that port in `SSH_CONNECTION`, and it is unique per live
  session, so it is an exact join key. The relay answers port→address lookups
  on a LAN-bound socket; `relay.py`'s `ipv6_greeting()` asks, and greets the
  caller with their own address.

`ipv6_greeting()` is **fail-open at every step** — no `SSH_CONNECTION`, not
via the gateway, lookup refused, entry expired, hub unreachable — returning
either nothing or the address-less `IPv6 connection verified.` rather than
raising. A cosmetic greeting must never cost a caller their session. When the
lookup service is absent the connect is *refused* (RST) rather than timing
out, so there is no caller-visible delay either.

The two relay-side pieces run on the gateway host, not from this checkout, and
so are not tracked here — the same arrangement as `/usr/local/bin/cnet-ssh-relay.py`.
`relay.py` reaches them through the `GATEWAY_*` constants at the top of the
file; a single-machine install with no IPv6 front end leaves them unused and
the greeting simply never fires.

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
