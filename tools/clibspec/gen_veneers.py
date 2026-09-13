#!/usr/bin/env python3
"""gen_veneers.py — generate the SharedCLibrary client veneer tables.

The client contract, established from the library's own sources (h_stubs
macros, cl_stub.s registration, cl_data.s/k_data.s statics) and verified
against the shipped DDE stubs:

  * every entry is one word, `MOV pc, #0` (0xE3A0F000) — the module fills
    each slot with a veneer at registration;
  * the client carries a list of chunk descriptors, each
        { chunk type, entries start, entries end, data start, data end }
    terminated by a -1 word:
        1  kernel: k_entries slots + the &31C-byte kernel statics (PRM-fixed)
        2  clib classics: cl_entries slots + the &B48-byte clib statics
           (__errno, the ten FILE blocks of __iob, __ctype, ...)
        5  the C99 generation: cl_entry2 + cl_entry2x, no data area
  * all statics are zero-reserved — the module writes the real values at
    registration ("InitWord/InitByte ... force the module version");
  * registration is SWI &80683 (SharedCLibrary LibInitAPCS_32), see
    rostrt/roclib_init.s;
  * slot N of a table is the module's Nth entry of that chunk — order is
    the contract, so tables reproduce the module's entry order exactly.

Which entries an AAPCS caller may call is a separate question, answered
by clib.json (the binding spec); entries it marks unsafe exist as slots
but are not declared to callers.

The order source is the SharedCLibrary's own entry/static files
(ROOL-licensed; pass the RISC_OSLib root — the generator reads them
where they live and copies nothing into the repo).

Usage:
  gen_veneers.py <rool-riscoslib-root> <clib.json> <out-roclib.s>
"""

import json
import re
import sys
from pathlib import Path

ENTRY_RE = re.compile(r"^\s*(Entry2?)\s+(.+)$", re.IGNORECASE)


def parse_entry_operands(rest: str):
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
    inner = name.strip()
    if inner.startswith("|") and inner.endswith("|"):
        inner = inner[1:-1]
    if re.fullmatch(r"[A-Za-z_.$][\w.$]*", inner):
        return inner
    return inner.replace(" ", "_")


def gen_slots(files, sdir, unsafe):
    """One .word slot per Entry, in the module's order."""
    out = []
    count = 0
    for fname in files:
        path = sdir / fname
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
            count += 1
            note = ""
            if any("varargs" in op for op in ops[1:]):
                note = " @ varargs entry: not callable from AAPCS"
            elif names[0] in unsafe:
                note = " @ unsafe class: slot only"
            for alias in names[1:]:
                out.append(f".global {alias}")
                out.append(f"{alias}:")
            out.append(f".global {names[0]}")
            out.append(f"{names[0]}:")
            out.append(f"    .word 0xE3A0F000{note}")
    return out, count



DATA_MACROS = ("ExportedVariable", "VariableByte", "ExportedWord",
               "Variable", "InitWord", "InitByte")


def parse_size(expr):
    """`10*16`, `&31C`-free simple decimal expressions, default 1."""
    expr = expr.strip().rstrip(";").strip()
    if not expr:
        return 1
    if not re.fullmatch(r"[\d\s*+-]+", expr):
        return 1
    try:
        return int(eval(expr, {"__builtins__": {}}, {}))
    except Exception:
        return 1


def gen_data(path):
    """The zero-reserved statics area, in the module's layout.

    objasm macro invocations carry the label first: `__errno
    ExportedVariable`, `__iob ExportedVariable 10*16`.  Variable sizes
    are words (default 1); VariableByte sizes are bytes; Exported*
    carry global symbols; InitWord/InitByte continue the previous
    reservation; everything initialises to zero — the module writes the
    real values at registration.
    """
    out = []
    pending_bytes = 0
    if not path.exists():
        print(f"warning: {path} not found", file=sys.stderr)
        return out
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.split(";")[0].rstrip()
        toks = line.split()
        if not toks:
            continue
        if toks[0] in DATA_MACROS:
            macro, label, rest = toks[0], None, toks[1:]
        elif len(toks) >= 2 and toks[1] in DATA_MACROS:
            label, macro, rest = toks[0], toks[1], toks[2:]
        else:
            continue
        nbytes = parse_size(" ".join(rest)) * (
            1 if macro in ("VariableByte", "InitByte") else 4)
        if macro in ("InitWord", "InitByte"):
            if pending_bytes:
                out.append(f"    .space {pending_bytes}")
                pending_bytes = 0
            out.append(f"    .space {nbytes}")
            continue
        if macro != "VariableByte" and pending_bytes:
            out.append(f"    .space {pending_bytes}  @ byte tail")
            pending_bytes = 0
        if label:
            if macro in ("ExportedVariable", "ExportedWord"):
                out.append(f".global {mangle(label)}")
                out.append(f"{mangle(label)}:")
            else:
                out.append(f"_stub_{mangle(label)}:")
        if macro == "VariableByte":
            pending_bytes += nbytes
        else:
            out.append(f"    .space {nbytes}")
    if pending_bytes:
        out.append(f"    .space {pending_bytes}")
    return out


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(2)
    root, spec_path, out_path = (Path(a) for a in sys.argv[1:4])
    clib_s = root / "clib" / "s"
    kernel_s = root / "kernel" / "s"

    spec = json.loads(spec_path.read_text())
    unsafe = {d["name"] for d in spec if d["class"] != "identical"}

    k_slots, k_n = gen_slots(["k_entries"], kernel_s, unsafe)
    c_slots, c_n = gen_slots(["cl_entries"], clib_s, unsafe)
    g5_slots, g5_n = gen_slots(["cl_entry2", "cl_entry2x"], clib_s, unsafe)
    k_data = gen_data(kernel_s / "k_data")
    c_data = gen_data(clib_s / "cl_data") + gen_data(clib_s / "clibdata")

    asm = []
    asm.append("@ roclib veneer tables — GENERATED by tools/clibspec/gen_veneers.py")
    asm.append("@ Chunk descriptors {type, entries, data}, -1 terminated;")
    asm.append("@ one MOV pc,#0 slot per module entry; statics zero-reserved.")
    asm.append("")
    asm.append("    .syntax unified")
    asm.append("    .arm")
    asm.append("")
    # CHUNKS selects which chunks to carry.  The default is the set
    # proven on CLib 6.23 (ROM 5.30): kernel + classics.  Generation 5
    # is held back until its slot count is matched to the ROM's own
    # table — the 5.31-dev sources run three ahead of the module and a
    # patch overflow lands on the code that follows the table.
    import os
    chunks = os.environ.get("CHUNKS", "1,2").split(",")
    asm.append('    .section Stub$$Init,"a",%progbits')
    asm.append("    .global _clib_stub_init")
    asm.append("_clib_stub_init:")
    if "1" in chunks:
        asm.append(f"    .word 1          @ kernel: {k_n} slots + &31C statics")
        asm.append("    .word _k_entries_start, _k_entries_end")
        asm.append("    .word _k_data_start, _k_data_end")
    if "2" in chunks:
        asm.append(f"    .word 2          @ clib classics: {c_n} slots + &B48 statics")
        asm.append("    .word _clib2_start, _clib2_end")
        asm.append("    .word _clib_data_start, _clib_data_end")
    if "5" in chunks:
        asm.append(f"    .word 5          @ C99 generation: {g5_n} slots, no statics")
        asm.append("    .word _clib5_start, _clib5_end")
        asm.append("    .word 0, 0")
    asm.append("    .word -1         @ descriptor list terminator")
    asm.append("")
    asm.append('    .section Stub$$Entries,"ax",%progbits')
    asm.append("    .align 2")
    asm.append("    .global _k_entries_start")
    asm.append("    .global _k_entries_end")
    asm.append("_k_entries_start:")
    asm.append("\n".join(k_slots))
    asm.append("_k_entries_end:")
    asm.append("")
    asm.append("    .global _clib2_start")
    asm.append("    .global _clib2_end")
    asm.append("_clib2_start:")
    asm.append("\n".join(c_slots))
    asm.append("_clib2_end:")
    asm.append("")
    asm.append("    .global _clib5_start")
    asm.append("    .global _clib5_end")
    asm.append("_clib5_start:")
    asm.append("\n".join(g5_slots))
    asm.append("_clib5_end:")
    asm.append("")
    asm.append('    .section Stub$$Data,"aw",%progbits')
    asm.append("    .align 2")
    asm.append("    .global _k_data_start")
    asm.append("    .global _k_data_end")
    asm.append("_k_data_start:")
    asm.append("\n".join(k_data))
    asm.append("_k_data_end:")
    asm.append("")
    asm.append("    .global _clib_data_start")
    asm.append("    .global _clib_data_end")
    asm.append("_clib_data_start:")
    asm.append("\n".join(c_data))
    asm.append("_clib_data_end:")
    asm.append("")

    Path(out_path).write_text("\n".join(asm) + "\n")
    print(
        f"chunks: kernel {k_n} slots, classics {c_n}, gen5 {g5_n} "
        f"-> {out_path}"
    )


if __name__ == "__main__":
    main()
