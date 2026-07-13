"""
Shared telnet IAC negotiation + ANSI cursor-position handling for talking to
C-Net/5's telnet port (127.0.0.1:6800). Used by both ssh-proxy/relay.py and
rlogin-gateway/rlogin_server.py so the two gateways don't carry independent,
divergent copies of the same fragile byte-parsing logic.
"""

IAC = 0xFF
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250
SE = 240

ECHO = 1
SUPPRESS_GA = 3
BINARY = 0

# Options the BBS offers via WILL that we should accept (reply DO).
ACCEPT_WILL = {ECHO, SUPPRESS_GA}

# Options the BBS asks us to provide via DO that we should accept (reply WILL).
ACCEPT_DO = {BINARY}

CPR_QUERY = b"\x1b[6n"       # ANSI "report cursor position" query the BBS sends during terminal auto-detect
CPR_REPLY = b"\x1b[24;80R"   # synthetic reply - the BBS only checks that *a* well-formed reply arrives


def respond_to_option(cmd: int, opt: int) -> bytes:
    """Reply correctly to each telnet option: accept ECHO/SGA/BINARY, decline the rest
    (NAWS, TERMINAL-SPEED, LINEMODE, STATUS, etc.) to keep the session simple."""
    if cmd == WILL:
        return bytes([IAC, DO if opt in ACCEPT_WILL else DONT, opt])
    if cmd == DO:
        return bytes([IAC, WILL if opt in ACCEPT_DO else WONT, opt])
    return b""


class BbsStreamFilter:
    """Strips telnet IAC negotiation from bytes arriving from the BBS and answers
    the ANSI cursor-position query itself. Holds back partial IAC/ESC sequences
    split across recv() calls. One instance per BBS-side connection."""

    def __init__(self, write_to_bbs):
        self._pending = b""
        self._write_to_bbs = write_to_bbs

    def filter(self, data: bytes) -> bytes:
        data = self._pending + data
        self._pending = b""

        # Hold back a trailing partial ESC sequence until the rest arrives.
        if data and data[-1:] == b"\x1b":
            self._pending = data[-1:]
            data = data[:-1]
        elif len(data) >= 2 and data[-2:-1] == b"\x1b" and data[-1:] == b"[":
            self._pending = data[-2:]
            data = data[:-2]

        if CPR_QUERY in data:
            data = data.replace(CPR_QUERY, b"")
            try:
                self._write_to_bbs(CPR_REPLY)
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
            if i + 1 >= n:
                i += 1
                continue
            cmd = data[i + 1]
            if cmd == IAC:
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
                        self._write_to_bbs(reply)
                    except Exception:
                        pass
                i += 3
                continue
            if cmd == SB:
                j = i + 2
                while j + 1 < n and not (data[j] == IAC and data[j + 1] == SE):
                    j += 1
                i = j + 2
                continue
            i += 2
        return bytes(out)


def escape_caller_bytes(data: bytes) -> bytes:
    """Escape any literal 0xFF byte typed by the caller so it isn't misread as telnet IAC."""
    if IAC not in data:
        return data
    out = bytearray()
    for b in data:
        out.append(b)
        if b == IAC:
            out.append(IAC)
    return bytes(out)
