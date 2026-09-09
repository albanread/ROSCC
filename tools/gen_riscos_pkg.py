#!/usr/bin/env python3
"""Generate the `riscos` Mojo package AND matching C shims from
riscos_prm.sqlite.

Every emitted Mojo binding calls external_call["<SWI name>"]; every
shim (compiler/rostrt/swis_<chunk>.c) defines a C function of exactly
that name which performs the SWI with explicit register bindings.

Register model (from the PRM database):
  - up to 4 entry registers map to C args in register order
    (pointer-ish meanings -> void*; everything else -> int)
  - an entry register that is also an exit register is an in/out buffer
  - exit registers that are 'preserved'/'corrupted' are ignored
  - the first real output register (preferring R0) becomes the return
    value; further output registers become trailing int* out-params
  - reason-multiplexed SWIs ('reason' in the entry text) and those with
    magic entry constants ('TASK') are skipped for hand binding;
    hand-override SWIs live in rostrt/wimp.c instead.

Usage: gen_riscos_pkg.py [--chunks OS,Wimp] [--out DIR] [--shim-dir DIR]
"""
import argparse
import json
import os
import re
import sqlite3

CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# SWIs implemented by hand (magic constants) — never generate.
HAND_OVERRIDE = {"Wimp_Initialise", "Wimp_CloseDown"}


def snake(name: str) -> str:
    return CAMEL.sub("_", name).lower()


def reg_num(r: str) -> int:
    m = re.match(r"R(\d)", r.replace(" ", ""))
    return int(m.group(1)) if m else 99


def is_ptr(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in ("pointer", "block", "buffer", "address"))


def arg_name(reg: str, text: str) -> str:
    low = text.lower()
    for kw, name in [
        ("mask", "mask"),
        ("window block", "window_block"),
        ("icon block", "icon_block"),
        ("state block", "state_block"),
        ("task handle", "task_handle"),
        ("window handle", "window_handle"),
        ("handle", "handle"),
        ("string", "string"),
        ("buffer", "buffer"),
        ("value", "value"),
        ("pointer to", "ptr"),
        ("flag", "flags"),
    ]:
        if kw in low:
            return name
    return f"r{reg_num(reg)}"


def classify(rows, chunk, shim_lines, mojo_lines, stats):
    for r in rows:
        name = r["name"]
        if name in HAND_OVERRIDE:
            stats["hand"] += 1
            continue
        entry = json.loads(r["entry_regs"] or "[]")
        exit_ = json.loads(r["exit_regs"] or "[]")
        on_entry = (r["on_entry"] or "").lower()

        if "reason" in on_entry or "reason code" in on_entry:
            stats["reason"] += 1
            continue
        if any(reg_num(e[0]) > 3 for e in entry) or len(entry) > 4:
            stats["toomany"] += 1
            continue
        # magic entry constants we cannot express generically
        if "‘task’" in on_entry or "× 100" in on_entry or "'task'" in on_entry:
            stats["magic"] += 1
            continue

        ins = sorted(((reg_num(e[0]), e[1]) for e in entry))
        # outputs: real outputs only
        ins_by_rn = {rn: is_ptr(text) for rn, text in ins}
        outs = []
        seen_out_rns = set()
        for reg, text in exit_:
            t = text.lower()
            if "preserved" in t or "corrupt" in t:
                continue
            rn = reg_num(reg)
            if rn in seen_out_rns:
                continue
            if rn in ins_by_rn and ins_by_rn[rn]:
                continue  # in/out buffer pointer: stays a plain arg
            seen_out_rns.add(rn)
            outs.append((rn, text))
        outs.sort()

        # ---- C shim ----
        num = r["number"]
        cparams = []
        cargs = []
        seen_names = set()
        seen_ins = set()
        for rn, text in ins:
            if rn in seen_ins:
                continue  # DB repeats a register line: keep the first
            seen_ins.add(rn)
            nm = arg_name(f"R{rn}", text)
            base = nm
            k = 2
            while nm in seen_names:
                nm = f"{base}{k}"
                k += 1
            seen_names.add(nm)
            if is_ptr(text):
                cparams.append(f"void *{nm}")
            else:
                cparams.append(f"int {nm}")
            cargs.append((rn, nm, is_ptr(text)))
        ret_outs = outs[1:]  # after the return register
        for rn, text in ret_outs:
            cparams.append(f"int *out_r{rn}")

        retreg = outs[0][0] if outs else None
        cret = "int" if retreg is not None else "void"

        # register classes: in-only, in-and-out (read-modified), out-only
        out_rns = set(rn for rn, _ in outs)
        ins_only = [rn for rn, _n, _p in cargs if rn not in out_rns]
        inouts = [rn for rn, _n, _p in cargs if rn in out_rns]
        out_only = [rn for rn in out_rns if rn not in [i[0] for i in cargs]]

        decl = ""
        for rn, nm, ptr in cargs:
            decl += f"    register {'void *' if ptr else 'int'} reg{rn} __asm(\"r{rn}\") = {nm};\n"
        for rn in out_only:
            decl += f"    register int reg{rn} __asm(\"r{rn}\");\n"

        outputs = ", ".join(
            [f'"+r"(reg{rn})' for rn in inouts]
            + [f'"=r"(reg{rn})' for rn in out_only]
        )
        inputs = ", ".join(f'"r"(reg{rn})' for rn in ins_only)
        used = set(ins_only) | set(inouts) | set(out_only)
        clobbers = ", ".join(
            [f'"r{n}"' for n in (0, 1, 2, 3, 12) if n not in used] + ['"memory"']
        )
        asm = f'"swi 0x{num:X}"'
        full_asm = f"__asm__ volatile({asm}"
        if outputs:
            full_asm += f" : {outputs}"
            full_asm += f" : {inputs}" if inputs else " :"
        elif inputs:
            full_asm += f" : : {inputs}"
        full_asm += f" : : : {clobbers}" if not (outputs or inputs) else f" : {clobbers}"
        full_asm += ");"

        body_ret = ""
        if retreg is not None:
            body_ret = f"    return reg{retreg};\n"
        for rn, _t in ret_outs:
            body_ret = f"    if (out_r{rn}) *out_r{rn} = reg{rn};\n" + body_ret

        shim_lines.append(f"/* {name} (SWI &{num:X}): {(r['one_line'] or '').strip().rstrip('.')} */")
        shim_lines.append(
            f"{cret} {name}({', '.join(cparams) if cparams else 'void'})\n{{\n"
            f"{decl}    {full_asm}\n{body_ret}}}\n"
        )

        # ---- Mojo binding ----
        fname = snake(name[len(chunk) + 1:])
        params = []
        call_args = []
        mseen = set()
        for rn, nm, ptr in cargs:  # already deduped/named on the C side
            if nm in mseen:
                continue
            mseen.add(nm)
            if ptr:
                params.append(f"{nm}: UnsafePointer[UInt8, MutUntrackedOrigin]")
            else:
                params.append(f"{nm}: Int32")
            call_args.append(nm)
        for rn, text in ret_outs:
            params.append(f"out_r{rn}: UnsafePointer[Int32, MutUntrackedOrigin]")
            call_args.append(f"out_r{rn}")
        mret = "Int32" if retreg is not None else "None"
        doc = (r["one_line"] or "").strip().rstrip(".")
        pr = f"(PRM {r['page_ref']})" if r["page_ref"] else ""
        mojo_lines.append(f"def {fname}({', '.join(params)}) -> {mret}:")
        mojo_lines.append(f'    """{doc} {pr}"""')
        if retreg is not None:
            mojo_lines.append(
                f'    return external_call["{name}", Int32]({", ".join(call_args)})'
            )
        else:
            mojo_lines.append(
                f'    _ = external_call["{name}", Int32]({", ".join(call_args)})'
            )
        mojo_lines.append("")
        stats["emitted"] += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=r"F:\RISCOSDEV\db\riscos_prm.sqlite")
    ap.add_argument("--chunks", default="OS,Wimp")
    ap.add_argument("--out", default=r"F:\RISCOSDEV\mojo-riscos\riscos")
    ap.add_argument("--shim-dir", default=r"F:\RISCOSDEV\compiler\rostrt")
    a = ap.parse_args()

    con = sqlite3.connect(a.db)
    con.row_factory = sqlite3.Row
    os.makedirs(a.out, exist_ok=True)

    for chunk in [c.strip() for c in a.chunks.split(",")]:
        rows = con.execute(
            "SELECT name, one_line, on_entry, on_exit, entry_regs, exit_regs, "
            "page_ref, number FROM swi WHERE documented=1 AND number IS NOT NULL "
            "AND name LIKE ? ORDER BY name",
            (chunk + "_%",),
        ).fetchall()
        mojo = [
            f'"""RISC OS {chunk} SWI bindings — generated from the PRM.',
            "",
            "Bindings call C shims named exactly like their SWIs, generated",
            "into rostrt/swis_" + snake(chunk) + ".c (see gen_riscos_pkg.py).",
            '"""',
            "",
            "from std.ffi import external_call",
            "",
        ]
        shim = [
            f"/* RISC OS {chunk} SWI shims — generated by gen_riscos_pkg.py.",
            " * One C function per SWI, named exactly like the SWI, so the",
            " * Mojo bindings' external_call names resolve here. */",
            "",
        ]
        stats = {"emitted": 0, "reason": 0, "toomany": 0, "magic": 0, "hand": 0}
        classify(rows, chunk, shim, mojo, stats)

        mpath = os.path.join(a.out, f"{snake(chunk)}.mojo")
        appendix = os.path.join(a.out, f"appendix_{snake(chunk)}.mojo")
        if os.path.exists(appendix):
            mojo.append(open(appendix, encoding="utf-8").read())
        open(mpath, "w", encoding="utf-8", newline="\n").write("\n".join(mojo))

        spath = os.path.join(a.shim_dir, f"swis_{snake(chunk)}.c")
        open(spath, "w", encoding="utf-8", newline="\n").write("\n".join(shim))
        print(
            f"{chunk}: {stats['emitted']} bindings+shims "
            f"(skipped: {stats['reason']} reason-mux, {stats['toomany']} >R3, "
            f"{stats['magic']} magic, {stats['hand']} hand)"
        )


if __name__ == "__main__":
    main()
