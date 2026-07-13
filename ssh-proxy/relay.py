#!/usr/bin/env python3
"""
SSH-to-telnet relay for Reign of Fire BBS (C-Net/5).

Run as the ForceCommand for a dedicated, locked-down SSH gateway account.
Connects stdin/stdout (wired to the caller's SSH pty by sshd) to the BBS's
existing telnet port on this host (Amiberry's bsdsocket_emu exposes the
emulated Amiga's listening sockets directly on the host network stack).

Strips/refuses telnet IAC option negotiation from the BBS side so the caller
never sees raw negotiation bytes, and escapes any literal 0xFF byte typed by
the caller (IAC IAC) so it can't be misread as a telnet command by the BBS.
"""

import os
import socket
import sys
import select
import termios
import tty

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared"))
from telnet_util import BbsStreamFilter, escape_caller_bytes  # noqa: E402

BBS_HOST = "127.0.0.1"
BBS_PORT = 6800


_bbs_sock = None


def sock_write_bbs(data: bytes):
    if _bbs_sock is not None:
        _bbs_sock.sendall(data)


def main():
    global _bbs_sock
    try:
        bbs = socket.create_connection((BBS_HOST, BBS_PORT), timeout=10)
    except OSError as e:
        sys.stdout.buffer.write(f"\r\nCould not reach the BBS ({e}). Try again shortly.\r\n".encode())
        sys.stdout.buffer.flush()
        return 1
    bbs.setblocking(False)
    _bbs_sock = bbs
    bbs_filter = BbsStreamFilter(sock_write_bbs)

    stdin_fd = sys.stdin.fileno()
    stdout = sys.stdout.buffer

    # Put the SSH pty into raw mode: without this, the kernel's line
    # discipline buffers/echoes/edits input itself (cooked mode), which
    # can silently swallow or delay bytes instead of passing every
    # keystroke straight through immediately - exactly what a telnet
    # relay needs, since the BBS on the other end does its own echo.
    raw_mode_saved = None
    if sys.stdin.isatty():
        try:
            raw_mode_saved = termios.tcgetattr(stdin_fd)
            tty.setraw(stdin_fd)
        except termios.error:
            raw_mode_saved = None

    try:
        while True:
            rlist, _, _ = select.select([stdin_fd, bbs], [], [], 60)
            if not rlist:
                continue
            if bbs in rlist:
                try:
                    chunk = bbs.recv(4096)
                except (BlockingIOError, InterruptedError):
                    chunk = b"\x00"  # spurious wakeup, loop again
                if chunk == b"":
                    break
                if chunk != b"\x00":
                    out = bbs_filter.filter(chunk)
                    if out:
                        stdout.write(out)
                        stdout.flush()
            if stdin_fd in rlist:
                try:
                    chunk = os.read(stdin_fd, 4096)
                except OSError:
                    chunk = b""
                if chunk == b"":
                    break
                bbs.sendall(escape_caller_bytes(chunk))
    finally:
        try:
            bbs.close()
        except Exception:
            pass
        if raw_mode_saved is not None:
            try:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, raw_mode_saved)
            except termios.error:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
