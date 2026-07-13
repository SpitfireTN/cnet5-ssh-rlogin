#!/usr/bin/env python3
"""
Inbound RLOGIN gateway for Reign of Fire BBS (C-Net/5), modeled on Mystic
BBS's RLOGIN server: a caller connects, presents a username via the standard
rlogin handshake, and - if that username has a stored credential here - is
dropped straight into the BBS logged in as that account, no prompts answered
by hand.

This does NOT modify the bbs engine. It's an external process, same pattern
as ssh-proxy/relay.py: speak the caller-facing protocol, then drive the
BBS's existing telnet port (127.0.0.1:6800) programmatically. C-Net/5 itself
has no native RLOGIN support and isn't touched.

Protocol (RFC 1282-style, what Mystic and most BBS packages implement):
  client -> server, immediately on connect:
      NUL  client_user_name NUL  server_user_name NUL  term_type/speed NUL
  server_user_name is the BBS handle to log in as. That handle must have an
  entry in accounts.json (see accounts.example.json) or the connection is
  refused - there is no auto-create-account support in this version.

Login automation: after the handshake, this connects to the BBS telnet port
and scripts the pre-login sequence that's otherwise typed by hand:
  "Enter Terminal Type:"          -> configured term letter (A/I/S)
  "ENTER/RETURN to Fuel the Fire" -> Enter
  "Enter your handle to logon"    -> the handle
  (password prompt)               -> the stored password, sent after a short
                                      quiet gap once no further bytes have
                                      arrived from the BBS - the exact prompt
                                      text isn't a fixed SysText string (it's
                                      built at runtime), so this reacts to
                                      timing rather than pattern-matching it.
Once the password is sent (or a ~30s automation budget expires, whichever
first - this always fails open into plain relay rather than hanging a
caller), the connection becomes a plain bidirectional byte relay identical
in spirit to relay.py, so if the stored credential is stale the caller just
sees the BBS's own login retry instead of a silent hang.
"""

import json
import os
import select
import socket
import socketserver
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
from telnet_util import BbsStreamFilter, escape_caller_bytes  # noqa: E402

BBS_HOST = "127.0.0.1"
BBS_PORT = 6800
RLOGIN_PORT = int(os.environ.get("RLOGIN_PORT", "513"))
ACCOUNTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "accounts.json")

DEFAULT_TERM = "I"
HANDSHAKE_TIMEOUT = 10.0
AUTOMATION_BUDGET = 30.0
PASSWORD_QUIET_GAP = 0.6

WAIT_TERM, WAIT_CONTINUE, WAIT_HANDLE, WAIT_PASSWORD, DONE = range(5)


def load_accounts():
    try:
        with open(ACCOUNTS_FILE) as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


def read_rlogin_handshake(sock: socket.socket) -> tuple[str, str, str]:
    """Blocking read of the standard rlogin handshake: a leading NUL, then
    three NUL-terminated strings (client_user, server_user, term/speed)."""
    sock.settimeout(HANDSHAKE_TIMEOUT)
    buf = b""
    fields = []
    # Optional leading NUL some clients send before the first field.
    first = sock.recv(1)
    if first != b"\x00":
        buf = first
    while len(fields) < 3:
        chunk = sock.recv(1)
        if not chunk:
            raise ConnectionError("client closed during handshake")
        if chunk == b"\x00":
            fields.append(buf.decode("latin-1"))
            buf = b""
        else:
            buf += chunk
            if len(buf) > 512:
                raise ValueError("handshake field too long")
    return fields[0], fields[1], fields[2]


def bridge(client: socket.socket, bbs: socket.socket, handle: str, password: str, term: str):
    client.setblocking(False)
    bbs.setblocking(False)

    bbs_writes = []

    def write_to_bbs(data: bytes):
        bbs_writes.append(data)

    bbs_filter = BbsStreamFilter(write_to_bbs)

    state = WAIT_TERM
    text_buf = ""
    start = time.time()
    last_bbs_byte_at = start
    handle_sent_at = None

    def flush_bbs_writes():
        for chunk in bbs_writes:
            try:
                bbs.sendall(chunk)
            except OSError:
                pass
        bbs_writes.clear()

    while True:
        now = time.time()
        if state != DONE and now - start > AUTOMATION_BUDGET:
            state = DONE  # fail open: stop scripting, just relay from here on

        timeout = 0.3 if state == WAIT_PASSWORD else 1.0
        try:
            rlist, _, _ = select.select([client, bbs], [], [], timeout)
        except OSError:
            break

        if bbs in rlist:
            try:
                chunk = bbs.recv(4096)
            except (BlockingIOError, InterruptedError):
                chunk = None
            except OSError:
                break
            if chunk == b"":
                break
            if chunk:
                last_bbs_byte_at = now
                out = bbs_filter.filter(chunk)
                flush_bbs_writes()
                if out:
                    try:
                        client.sendall(out)
                    except OSError:
                        break
                    if state in (WAIT_TERM, WAIT_CONTINUE, WAIT_HANDLE):
                        text_buf = (text_buf + out.decode("latin-1"))[-2000:]

        if client in rlist:
            try:
                chunk = client.recv(4096)
            except (BlockingIOError, InterruptedError):
                chunk = None
            except OSError:
                break
            if chunk == b"":
                break
            if chunk:
                try:
                    bbs.sendall(escape_caller_bytes(chunk))
                except OSError:
                    break

        if state == WAIT_TERM and "Enter Terminal Type:" in text_buf:
            bbs.sendall((term + "\r").encode())
            state = WAIT_CONTINUE
            text_buf = ""
        elif state == WAIT_CONTINUE and "Fuel the Fire" in text_buf:
            bbs.sendall(b"\r")
            state = WAIT_HANDLE
            text_buf = ""
        elif state == WAIT_HANDLE and "Enter your handle to logon" in text_buf:
            bbs.sendall((handle + "\r").encode())
            state = WAIT_PASSWORD
            text_buf = ""
            handle_sent_at = now
        elif (
            state == WAIT_PASSWORD
            and handle_sent_at is not None
            and now - handle_sent_at > 0.3
            and now - last_bbs_byte_at > PASSWORD_QUIET_GAP
        ):
            bbs.sendall((password + "\r").encode())
            state = DONE


class RloginHandler(socketserver.BaseRequestHandler):
    def handle(self):
        client = self.request
        peer = self.client_address
        try:
            client_user, server_user, term_speed = read_rlogin_handshake(client)
        except Exception as e:
            print(f"[rlogin] {peer}: handshake failed: {e}", file=sys.stderr)
            client.close()
            return

        accounts = load_accounts()
        account = accounts.get(server_user)
        print(f"[rlogin] {peer}: client_user={client_user!r} server_user={server_user!r} "
              f"term={term_speed!r} known={account is not None}", file=sys.stderr)

        if account is None:
            try:
                client.sendall(
                    f"\r\nNo RLOGIN-enabled account named '{server_user}' on this BBS.\r\n".encode()
                )
            except OSError:
                pass
            client.close()
            return

        # rlogin protocol: server ACKs a trusted handshake with a single NUL byte.
        try:
            client.sendall(b"\x00")
        except OSError:
            client.close()
            return

        try:
            bbs = socket.create_connection((BBS_HOST, BBS_PORT), timeout=10)
        except OSError as e:
            try:
                client.sendall(f"\r\nCould not reach the BBS ({e}).\r\n".encode())
            except OSError:
                pass
            client.close()
            return

        term = account.get("term", DEFAULT_TERM)
        password = account.get("password", "")
        try:
            bridge(client, bbs, server_user, password, term)
        finally:
            bbs.close()
            client.close()


class RloginServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    accounts = load_accounts()
    print(f"[rlogin] {len(accounts)} account(s) loaded from {ACCOUNTS_FILE}", file=sys.stderr)
    server = RloginServer(("0.0.0.0", RLOGIN_PORT), RloginHandler)
    print(f"[rlogin] listening on port {RLOGIN_PORT}, forwarding to {BBS_HOST}:{BBS_PORT}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
