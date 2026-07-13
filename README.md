# CNet/5 `bbs` reverse-engineering workbench

Goal: recover editable/maintainable source for CNet/5 (Amiga BBS software,
closed-source, no updates in years, original source unavailable). Starting
with the core engine binary `DH3/CNet/bbs`.

## Binary facts

- `DH3/CNet/bbs`: AmigaOS Hunk-format `loadseg()`-able executable, 324,644
  bytes on disk.
- Exactly 2 hunks: `HUNK_CODE` (297,076 bytes) + `HUNK_DATA` (22,820 bytes).
  No `HUNK_BSS`, no `HUNK_SYMBOL`, no `HUNK_DEBUG`, no `HUNK_EXT` — fully
  stripped release build. Zero function/variable names anywhere in the file.
- Compiled with a SAS/C-style "resident small data" model: the CODE hunk is
  pure position-independent code (safe to stay resident/shared across
  concurrently-running BBS nodes); each running instance gets its own
  private DATA+BSS area.

## The A4 addressing scheme (this was the hard part)

Startup sequence (code offset 0x0-0x62ish):
1. `moveal 0x4,%fp` — A6 = SysBase (ExecBase always lives at absolute
   address 4 on AmigaOS).
2. `movel #41992,%d0` / `movel #65537,%d1` / `jsr %fp@(-198)` —
   `AllocMem(41992, MEMF_PUBLIC|MEMF_CLEAR)` (exec.library LVO -198).
   0x10001 = MEMF_PUBLIC(1) | MEMF_CLEAR(0x10000).
3. The 22,820 initialized bytes of the DATA hunk get copied into the front
   of that fresh 41,992-byte buffer; the remaining ~19KB stays
   zero-initialized (that's the program's BSS).
4. `%a4` is left pointing 32,768 bytes into that buffer (confirmed via the
   file's own `HUNK_RELOC32` table: the `lea 0x8000,%a4` at code+0xA is
   itself a CODE→DATA relocation with pre-fixup value 0x8000).

**Net result: every `%a4@(N)` instruction in the disassembly addresses a
real byte offset in the DATA hunk via `DATA_offset = N + 32768`.** Verified
against known string locations (830/1039 extracted strings are reachable
this way). This is what makes the whole binary crackable without symbols.

## Files in this directory

- `bbs_CODE.bin` / `bbs_DATA.bin` — raw extracted hunk payloads.
- `bbs_CODE.disasm.txt` — full m68k disassembly (95,841 lines,
  `m68k-linux-gnu-objdump -D -b binary -m m68k:68000`).
- `code_targets.txt` — 457 addresses that are targets of absolute JSR/JMP
  calls, per the file's `HUNK_RELOC32` table. Strong function-entry-point
  candidates (only real call targets get a relocation entry).
- `a4_string_xrefs.txt` — every extracted DATA-segment string mapped to the
  code address(es) that reference it via `%a4@(N)`.
- `a4_globals_inventory.txt` — every distinct A4-relative global slot
  referenced anywhere in the code, with reference counts, and the string
  label when the slot falls inside a known string.
- `string_xrefs.txt` — earlier, mostly-empty attempt at xreffing via the
  hunk relocation table directly (only catches ~5 hardcoded absolute
  pointers; superseded by `a4_string_xrefs.txt`). Kept for reference.

## Tooling

- No third-party binary-analysis code runs against the binary. Hunk parsing
  is a from-scratch Python script (inline in this session's history, not
  yet saved as a standalone file — TODO: extract to `hunk_parse.py`).
  Disassembly uses `m68k-linux-gnu-objdump` (official Debian/Ubuntu
  binutils package, installed via `apt install binutils-m68k-linux-gnu`).
- A Ghidra 12.0.1 install + the community `ghidra-amiga` Hunk-loader
  extension were downloaded to `~/tools/` but are NOT in use per explicit
  decision to avoid running third-party analysis code against the binary.
  They're sitting there unused if that decision ever gets revisited.

## Not done yet / open work

- No function boundaries identified yet beyond the raw `code_targets.txt`
  candidate list — haven't walked any function to completion and named it.
- No naming/annotation pass over `a4_globals_inventory.txt` — it's a raw
  offset list, not yet turned into meaningful variable names.
- Haven't checked whether other CNet binaries (`control`, `useredit`,
  `toss`, etc.) share the same compiler/runtime conventions (likely, since
  they're presumably built with the same toolchain, but unverified).
- Nowhere near "editable C source" yet — this is disassembly + a working
  cross-reference scheme, the scaffolding needed to start reconstructing
  functions by hand, not reconstructed source itself.
