#!/usr/bin/env python3
"""
Outbound bridge: C-Net/5's ctelnet door -> DoorParty (via dpc2).

ctelnet (Doors:internet_support/ctelnet) is a telnet client baked into the
Amiga BBS - it has no RLOGIN support. dpc2 (DoorParty Connector v2) is an
RLOGIN *server* listening locally, which SSH-tunnels out to DoorParty's real
RLOGIN server. Neither speaks the other's protocol, so this process sits
between them:

    ctelnet --(telnet)--> [this bridge] --(rlogin)--> dpc2 --(ssh tunnel)--> DoorParty

Setup on the C-Net/5 side (once this bridge is running):
  1. From a BBS session, launch ctelnet and use its "AH" (Add Host) command
     to register a host named e.g. "DoorParty" pointing at
     127.0.0.1:<RLOGIN_BRIDGE_PORT> (this bridge's port, NOT dpc2's port -
     ctelnet must never talk to dpc2 directly, it doesn't speak RLOGIN).
  2. Add a line to bbsmenu: `#2 Doors:internet_support/ctelnet DoorParty}`
     so users can reach it as a menu command.

This bridge does NOT expose RLOGIN or telnet to the internet - it binds
127.0.0.1 only. dpc2 must independently be configured to also bind
127.0.0.1 (its default is 0.0.0.0, which must be overridden).
"""

import json
import os
import select
import socket
import socketserver
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
from telnet_util import BbsStreamFilter, escape_caller_bytes  # noqa: E402

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "doorparty.json")
BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = int(os.environ.get("RLOGIN_BRIDGE_PORT", "6513"))
RLOGIN_ACK_TIMEOUT = 10.0

# Real host path for SysData:anet_identity - a-net.rexx writes the caller's
# handle here immediately before triggering the connection. Consumed once
# (read then deleted) per connection; a stale/missing file just means the
# caller reached this bridge some other way (e.g. TEL directly), so it
# falls back to the shared identity in doorparty.json's system_tag.
IDENTITY_FILE = "/home/spitfiretn/Amiberry/HardDrives/DH3/CNet/SysData/anet_identity"


def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def consume_caller_identity():
    """Read and delete the handle a-net.rexx left for this connection, if any.
    Racy across simultaneous callers on different nodes (single shared file,
    no per-connection correlation) - acceptable for this BBS's scale, but a
    known limitation, not a guarantee."""
    try:
        with open(IDENTITY_FILE) as f:
            handle = f.read().strip()
    except FileNotFoundError:
        return None
    try:
        os.remove(IDENTITY_FILE)
    except OSError:
        pass
    return handle or None


def rlogin_client_handshake(sock: socket.socket, client_user: str, server_user: str, term: str):
    """Standard rlogin client handshake (RFC 1282): send a leading NUL, then
    three NUL-terminated fields, then block for the server's single-NUL ack
    before treating the connection as a raw session."""
    payload = (
        b"\x00"
        + client_user.encode() + b"\x00"
        + server_user.encode() + b"\x00"
        + term.encode() + b"\x00"
    )
    sock.sendall(payload)
    sock.settimeout(RLOGIN_ACK_TIMEOUT)
    ack = sock.recv(1)
    if ack != b"\x00":
        raise ConnectionError(f"dpc2 did not ack the rlogin handshake (got {ack!r})")


def bridge(ctelnet_sock: socket.socket, dpc2_sock: socket.socket):
    ctelnet_sock.setblocking(False)
    dpc2_sock.setblocking(False)

    # Telnet negotiation replies must go back to whichever peer sent the
    # negotiation request (ctelnet) - never onto the RLOGIN side (dpc2),
    # which has no IAC framing and would treat raw negotiation bytes as
    # literal application data.
    ctelnet_writes = []

    def write_to_ctelnet(data: bytes):
        ctelnet_writes.append(data)

    telnet_filter = BbsStreamFilter(write_to_ctelnet)

    def flush():
        for chunk in ctelnet_writes:
            try:
                ctelnet_sock.sendall(chunk)
            except OSError:
                pass
        ctelnet_writes.clear()

    while True:
        try:
            rlist, _, _ = select.select([ctelnet_sock, dpc2_sock], [], [], 60)
        except OSError:
            break
        if not rlist:
            continue

        if ctelnet_sock in rlist:
            try:
                chunk = ctelnet_sock.recv(4096)
            except (BlockingIOError, InterruptedError):
                chunk = None
            except OSError:
                break
            if chunk == b"":
                break
            if chunk:
                out = telnet_filter.filter(chunk)
                flush()
                if out:
                    try:
                        dpc2_sock.sendall(out)
                    except OSError:
                        break

        if dpc2_sock in rlist:
            try:
                chunk = dpc2_sock.recv(4096)
            except (BlockingIOError, InterruptedError):
                chunk = None
            except OSError:
                break
            if chunk == b"":
                break
            if chunk:
                try:
                    ctelnet_sock.sendall(escape_caller_bytes(chunk))
                except OSError:
                    break


class BridgeHandler(socketserver.BaseRequestHandler):
    def handle(self):
        ctelnet_sock = self.request
        peer = self.client_address
        try:
            cfg = load_config()
        except FileNotFoundError:
            print(f"[doorparty-bridge] {peer}: no doorparty.json - refusing connection", file=sys.stderr)
            ctelnet_sock.close()
            return

        try:
            ctelnet_sock.sendall(b"\r\nPlease hold, connecting - this could take a minute...\r\n")
        except OSError:
            pass

        try:
            dpc2 = socket.create_connection((cfg["dpc2_host"], cfg["dpc2_port"]), timeout=10)
        except OSError as e:
            print(f"[doorparty-bridge] {peer}: could not reach dpc2: {e}", file=sys.stderr)
            ctelnet_sock.close()
            return

        handle = consume_caller_identity()
        server_user = handle if handle else cfg["system_tag"]
        print(f"[doorparty-bridge] {peer}: identity = {server_user!r} "
              f"({'per-caller' if handle else 'shared fallback'})", file=sys.stderr)

        try:
            rlogin_client_handshake(
                dpc2,
                cfg.get("client_user", "cnetbbs"),
                server_user,
                cfg.get("term", "ansi/38400"),
            )
        except Exception as e:
            print(f"[doorparty-bridge] {peer}: rlogin handshake to dpc2 failed: {e}", file=sys.stderr)
            dpc2.close()
            ctelnet_sock.close()
            return

        print(f"[doorparty-bridge] {peer}: connected to dpc2, bridging", file=sys.stderr)
        try:
            bridge(ctelnet_sock, dpc2)
        finally:
            dpc2.close()
            ctelnet_sock.close()
            print(f"[doorparty-bridge] {peer}: session ended", file=sys.stderr)


class BridgeServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    if not os.path.exists(CONFIG_FILE):
        print(f"[doorparty-bridge] {CONFIG_FILE} not found. Copy doorparty.example.json to "
              f"doorparty.json, fill in the real system tag, then restart.", file=sys.stderr)
        sys.exit(1)

    server = BridgeServer((BRIDGE_HOST, BRIDGE_PORT), BridgeHandler)
    print(f"[doorparty-bridge] listening on {BRIDGE_HOST}:{BRIDGE_PORT} (loopback only), "
          f"forwarding to dpc2", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
