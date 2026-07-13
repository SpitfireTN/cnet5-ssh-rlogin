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

The BBS speaks CP437 (confirmed: SyncTERM callers use Font=Codepage 437
English / TerminalType=ANSI). Modern SSH clients default to UTF-8, so
without transcoding, every caller not running a CP437-aware terminal sees
garbled ANSI art. We transcode CP437 -> UTF-8 here, once, for everyone,
instead of requiring each caller to run a special client-side tool.
"""

import os
import socket
import sys
import select
import termios
import tty

BBS_HOST = "127.0.0.1"
BBS_PORT = 6800

IAC = 0xFF
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250
SE = 240


CPR_QUERY = b"\x1b[6n"       # ANSI "report cursor position" query the BBS sends during terminal auto-detect
CPR_REPLY = b"\x1b[24;80R"   # synthetic reply - the BBS only checks that *a* well-formed reply arrives

_pending = b""  # carries a partial escape sequence split across two recv() calls


def handle_bbs_to_caller(data: bytes) -> bytes:
    """Strip telnet IAC negotiation and answer the ANSI cursor-position query ourselves
    (rather than relying on the caller's terminal to auto-respond, which not all clients do)."""
    global _pending
    data = _pending + data
    _pending = b""

    # Hold back a trailing partial prefix of CPR_QUERY (of any length) until
    # the rest arrives. A fixed 1- or 2-byte-only check would miss a split
    # after "\x1b[6" (3 bytes), leaking raw escape bytes to the caller and
    # starving the BBS of its cursor-position reply.
    max_check = min(len(CPR_QUERY) - 1, len(data))
    for n in range(max_check, 0, -1):
        if data[-n:] == CPR_QUERY[:n]:
            _pending = data[-n:]
            data = data[:-n]
            break

    if CPR_QUERY in data:
        data = data.replace(CPR_QUERY, b"")
        try:
            sock_write_bbs(CPR_REPLY)
        except Exception:
            pass

    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b != IAC:
            out.append(b)
            i += 1
            continue
        # We have an IAC. Look at the next byte.
        if i + 1 >= n:
            # incomplete sequence at buffer boundary; drop it
            i += 1
            continue
        cmd = data[i + 1]
        if cmd == IAC:
            # escaped literal 0xFF
            out.append(IAC)
            i += 2
            continue
        if cmd in (DO, DONT, WILL, WONT):
            if i + 2 >= n:
                i += 2
                continue
            opt = data[i + 2]
            reply = respond_to_option(cmd, opt)
            if reply:
                try:
                    sock_write_bbs(reply)
                except Exception:
                    pass
            i += 3
            continue
        if cmd == SB:
            # subnegotiation: skip until IAC SE
            j = i + 2
            while j + 1 < n and not (data[j] == IAC and data[j + 1] == SE):
                j += 1
            i = j + 2
            continue
        # other single-byte telnet commands (NOP, AYT, etc.) - just skip
        i += 2
    return _lf_to_crlf(bytes(out))


def _lf_to_crlf(data: bytes) -> bytes:
    """The Amiga-side BBS emits bare LF for newlines; our raw pty has OPOST
    disabled (required so caller keystrokes pass through unprocessed), so the
    usual automatic LF->CRLF translation never happens. Do it ourselves,
    without doubling a CR that's already there."""
    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def cp437_transcode(data: bytes) -> bytes:
    """CP437 -> UTF-8. CP437 is single-byte, so each byte maps to a fixed
    code point independent of its neighbors - safe to transcode a chunk of
    any size (including a chunk split mid-escape-sequence) without buffering,
    unlike a multi-byte source encoding."""
    return data.decode("cp437", errors="replace").encode("utf-8")


ECHO = 1
SUPPRESS_GA = 3
BINARY = 0

# Options the BBS offers via WILL that we should accept (reply DO).
ACCEPT_WILL = {ECHO, SUPPRESS_GA}

# Options the BBS asks us to provide via DO that we should accept (reply WILL).
ACCEPT_DO = {BINARY}


def respond_to_option(cmd: int, opt: int) -> bytes:
    """Reply correctly to each telnet option: accept ECHO/SGA/BINARY, decline the rest
    (NAWS, TERMINAL-SPEED, LINEMODE, STATUS, etc.) to keep the session simple."""
    if cmd == WILL:
        return bytes([IAC, DO if opt in ACCEPT_WILL else DONT, opt])
    if cmd == DO:
        return bytes([IAC, WILL if opt in ACCEPT_DO else WONT, opt])
    return b""


def handle_caller_to_bbs(data: bytes) -> bytes:
    """Escape any literal 0xFF byte the caller typed so it isn't misread as telnet IAC,
    and remap DEL (0x7F - what most modern terminals send for the Backspace key) to BS
    (0x08). Confirmed directly against the BBS: it silently drops 0x7F (no echo, no
    erase) but treats 0x08 as destructive backspace, emitting the expected
    backspace/space/backspace erase sequence."""
    if IAC not in data and 0x7F not in data:
        return data
    out = bytearray()
    for b in data:
        if b == 0x7F:
            b = 0x08
        out.append(b)
        if b == IAC:
            out.append(IAC)
    return bytes(out)


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
                    out = handle_bbs_to_caller(chunk)
                    if out:
                        utf8_out = cp437_transcode(out)
                        if utf8_out:
                            stdout.write(utf8_out)
                            stdout.flush()
            if stdin_fd in rlist:
                try:
                    chunk = os.read(stdin_fd, 4096)
                except OSError:
                    chunk = b""
                if chunk == b"":
                    break
                bbs.sendall(handle_caller_to_bbs(chunk))
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
