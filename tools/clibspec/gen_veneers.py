#!/usr/bin/env python3
"""gen_veneers.py — generate the SharedCLibrary client veneer table.

The client contract (established from the CLib sources and the shipped
stubs, August 2026):

  * one entry per library function, exactly one word: `MOV pc, #0`
    (0xE3A0F000) — the SharedCLibrary module scans the table at
    registration and patches every slot into a veneer;
  * the table is bracketed by two labels and announced by a descriptor
    area: { 9, entries_start, entries_end, 0, 0 }  (9 = the APCS-32 stub
    format the module expects);
  * registration is SWI &80683  (SharedCLibrary Lib_Init+3, LibInitAPCS_32)
    with  r0 = descriptor, r1 = workspace start, r2 = workspace end,
    r3 = -1 (no zero-init base), r4 = 0, r5 = -1 (no statics copy),
    r6 = stack size in K<<16 | bit 0 (32-bit client);
  * slot N maps to the module's Nth entry — order is the contract, so the
    table reproduces the module's entry order exactly, all of it.  Which
    entries an AAPCS caller may actually *call* is a separate question
    answered by clib.json (the binding spec); entries it marks unsafe
    exist as slots but are not declared in roclib.h.

The entry-order source is the SharedCLibrary's own cl_entr*.s files
(ROOL-licensed; pass their directory as an argument — the generator reads
them where they live and neither copies nor embeds them).

Usage:
  gen_veneers.py <rool-clib-s-dir> <clib.json> <out-roclib.s>
"""

import json
import re
import sys
from pathlib import Path

ENTRY_RE = re.compile(
    r"^\s*(Entry2?)\s+(.+)$", re.IGNORECASE
)


def parse_entry_operands(rest: str):
    """Split `sym, import, sym2, direct, varargs, ...` on top-level commas."""
    parts, cur, depth = [], "", 0
    for ch in rest:
        if ch == "|":
            depth ^= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    parts.append(cur.strip())
    return parts


def mangle(name: str) -> str:
    """|x$stack_overflow| -> a clang-safe symbol spelling."""
    inner = name.strip()
    if inner.startswith("|") and inner.endswith("|"):
        inner = inner[1:-1]
    if re.fullmatch(r"[A-Za-z_.$][\w.$]*", inner):
        return inner
    # Fall back to quoting the whole thing as one line label; clang's
    # integrated assembler accepts symbols with $ but not with spaces.
    return inner.replace(" ", "_")


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(2)
    sdir, spec_path, out_path = (Path(a) for a in sys.argv[1:4])

    spec = json.loads(spec_path.read_text())
    unsafe = {
        d["name"] for d in spec if d["class"] != "identical"
    }

    # The client's descriptor list (walked by the module until a -1 word):
    #   {1..} kernel/rlib tables (base stub), {2..} rlib, {3..} the clib
    #   classics (cl_entries: fopen, strlen, malloc, printf's slots, ...),
    #   then the later generations as separate stub objects: {5..} the
    #   entry2+2x set (186 slots, verified against the shipped stubs,ffd),
    #   {8..} entry4, {9..} entry5.  We carry classics + generation 5 and
    #   terminate the list; the kernel table is unnecessary (rostrt reaches
    #   SWIs natively).  Slot N of a table is the module's Nth entry of
    #   that generation — order is the contract.
    tables = [
        ("3", "_clib3", ["cl_entries"]),
        ("5", "_clib5", ["cl_entry2", "cl_entry2x"]),
    ]

    lines = []
    entries = 0
    aliases = 0
    descriptors = []
    for magic, prefix, files in tables:
        dlines = []
        count = 0
        for fname in files:
            path = sdir / fname
            if not path.exists():
                path = sdir / (fname + ".s")
            if not path.exists():
                print(f"warning: {fname} not found", file=sys.stderr)
                continue
            for raw in path.read_text(errors="replace").splitlines():
                m = ENTRY_RE.match(raw)
                if not m:
                    continue
                kind, rest = m.groups()
                ops = parse_entry_operands(rest)
                names = [mangle(ops[0])]
                if kind.lower() == "entry2" and len(ops) >= 7 and ops[6]:
                    names.append(mangle(ops[6]))  # alias at the same slot
                names = [n for n in names if n]
                if not names:
                    continue
                label = names[0]
                for alias in names[1:]:
                    aliases += 1
                count += 1
                flags = [op for op in ops[1:] if op and not op.startswith("imported")]
                note = ""
                if any("varargs" in f for f in flags):
                    note = " @ varargs entry: not callable from AAPCS"
                elif names[0] in unsafe:
                    note = " @ unsafe class: slot only"
                dlines.append(f".global {label}")
                for alias in names[1:]:
                    dlines.append(f".global {alias}")
                    dlines.append(f"{alias}:")
                dlines.append(f"{label}:")
                dlines.append(f"    .word 0xE3A0F000{note}")  # MOV pc, #0
        descriptors.append((magic, prefix, count))
        lines.append(f"    .global {prefix}_start")
        lines.append(f"    .global {prefix}_end")
        lines.append(f"{prefix}_start:")
        lines.append("\n".join(dlines))
        lines.append(f"{prefix}_end:")
        lines.append("")
        entries += count  # MOV pc, #0

    asm = []
    asm.append("@ roclib veneer table — GENERATED by tools/clibspec/gen_veneers.py")
    asm.append("@ One MOV pc,#0 per module entry; SharedCLibrary patches each slot")
    asm.append("@ at LibInitAPCS_32.  Slot N is the module's Nth entry: order is the contract.")
    asm.append("")
    asm.append("    .syntax unified")
    asm.append("    .arm")
    asm.append("")
    asm.append("    .section Stub$$Init")
    asm.append("    .global _clib_stub_init")
    asm.append("_clib_stub_init:")
    for magic, prefix, count in descriptors:
        asm.append(f"    .word {magic}          @ generation {magic}: {count} slots")
        asm.append(f"    .word {prefix}_start")
        asm.append(f"    .word {prefix}_end")
        asm.append("    .word 0")
        asm.append("    .word 0")
    asm.append("    .word -1               @ descriptor list terminator")
    asm.append("")
    asm.append('    .section Stub$$Entries,"ax",%progbits')
    asm.append("    .align 2")
    asm.append("\n".join(lines))

    Path(out_path).write_text("\n".join(asm) + "\n")
    words = entries
    print(
        f"{entries} entries ({aliases} aliases) -> {out_path} "
        f"({words} words of slots, {words * 4} bytes)"
    )


if __name__ == "__main__":
    main()
