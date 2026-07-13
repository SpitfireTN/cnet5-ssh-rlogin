#!/usr/bin/env python3
"""
Standalone RLOGIN test target - NOT dpc2, NOT DoorParty. A minimal but
protocol-correct rlogin server to stand in for dpc2 while validating the
ctelnet -> doorparty_bridge.py -> [this] chain end-to-end, since dpc2 isn't
installed yet and DoorParty/Exodus both require an approved account.

Accepts the standard rlogin handshake, prints a small banner, then echoes
back anything typed (prefixed) so a real BBS session dialing in through
ctelnet can visibly confirm bytes are making the full round trip.

Run with: python3 test_rlogin_target.py [port]   (default port 19999)
"""

import socket
import sys
import threading

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 19999


def read_handshake(sock: socket.socket):
    fields = []
    buf = b""
    first = sock.recv(1)
    if first != b"\x00":
        buf = first
    while len(fields) < 3:
        b = sock.recv(1)
        if not b:
            raise ConnectionError("closed during handshake")
        if b == b"\x00":
            fields.append(buf.decode(errors="replace"))
            buf = b""
        else:
            buf += b
    return fields


def handle(conn: socket.socket, addr):
    try:
        client_user, server_user, term = read_handshake(conn)
    except Exception as e:
        print(f"[test-target] {addr}: handshake failed: {e}", file=sys.stderr)
        conn.close()
        return

    print(f"[test-target] {addr}: handshake ok - client_user={client_user!r} "
          f"server_user={server_user!r} term={term!r}", file=sys.stderr)
    conn.sendall(b"\x00")  # ack

    banner = (
        "\r\n"
        "=== RLOGIN TEST TARGET (not dpc2 / not DoorParty) ===\r\n"
        f"You arrived as server_user='{server_user}' term='{term}'\r\n"
        "Anything you type will be echoed back. Ctrl-C the ctelnet session "
        "or disconnect to end.\r\n\r\n"
    )
    conn.sendall(banner.encode())

    try:
        while True:
            data = conn.recv(4096)
            if not data:
                break
            conn.sendall(b"[echo] " + data)
    except OSError:
        pass
    finally:
        conn.close()
        print(f"[test-target] {addr}: session ended", file=sys.stderr)


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", PORT))
    srv.listen(5)
    print(f"[test-target] listening on 127.0.0.1:{PORT}", file=sys.stderr)
    try:
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=handle, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
