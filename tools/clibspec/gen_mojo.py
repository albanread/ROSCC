#!/usr/bin/env python3
"""gen_mojo.py — the C library surface as a Mojo package.

The portability rule this package enforces: Mojo code talks to the C
library ABI (the ROM's SharedCLibrary, through the roclib veneers), not
to SWIs — a future RISC OS rebuilds the binding, not the programs.

Reads the binding spec (clib.json, from extract.py) and emits `clib.mojo`
in the style of gen_riscos_pkg.py's Mojo half: every `identical`-class
function becomes an `external_call` wrapper named exactly like its C
symbol, which links directly against the veneer slots in roclib.

v1 scope: the `identical` class only — scalar/pointer prototypes whose
AAPCS and APCS-32 layouts coincide.  Float, variadic and struct-by-value
functions live in rostrt's local implementations and are deliberately
NOT bound here (the classes exist so this choice is visible).

Usage:
  gen_mojo.py <clib.json> <out-clib.mojo>
"""

import json
import sys
from pathlib import Path

PTR = "UnsafePointer[UInt8, MutUntrackedOrigin]"

# Header macros, not functions: their slots exist in the table but the
# module never veneers them (calling one is a branch to zero — found by
# the torture suite).  tolower/toupper/isblank are real functions.
MACRO_ONLY = {
    "isalnum", "isalpha", "iscntrl", "isdigit", "isgraph", "islower",
    "isprint", "ispunct", "isspace", "isupper", "isxdigit",
}

# C parameter/return spellings -> Mojo.  Anything unmapped defers the
# function to the appendix (fn pointers, exotic types).
def map_type(c: str):
    c = c.replace("restrict", "").replace("volatile", "").replace("const", "")
    c = " ".join(c.split())
    if "(" in c:
        return None  # function pointer — appendix territory
    if "*" in c:
        return PTR
    t = c.split()[-1] if c else ""
    return {
        "int": "Int32",
        "unsigned": "UInt32",
        "unsigned int": "UInt32",
        "uint": "UInt32",
        "long": "Int32",           # ILP32
        "unsigned long": "UInt32",
        "ulong": "UInt32",
        "size_t": "UInt32",
        "ssize_t": "Int32",
        "short": "Int32",
        "unsigned short": "UInt32",
        "char": "Int32",           # char by value, widened
        "signed char": "Int32",
        "unsigned char": "UInt32",
        "void": "None",
        "_Bool": "Int32",
        "time_t": "Int32",
        "clock_t": "Int32",
        "fpos_t": "Int32",
        "wint_t": "UInt32",
        "va_list": None,
        "jmp_buf": None,
        "FILE": None,              # FILE by value shouldn't exist
        "div_t": None,
        "in_addr_t": "UInt32",
        "socklen_t": "UInt32",
        "ssize": "Int32",
        "pid_t": "Int32",
        "off_t": "Int32",
        "ssize": "Int32",
        "mode_t": "UInt32",
        "dev_t": "UInt32",
        "ino_t": "UInt32",
        "uid_t": "UInt32",
        "gid_t": "UInt32",
        "ssize_t": "Int32",
    }.get(t if " " not in c else c, None) if t else "None"


def param_names(params):
    """The spec's params are type-only or `type name`; synthesise names."""
    out = []
    for i, p in enumerate(params):
        p = p.replace("restrict", "").strip()
        toks = p.replace("*", " * ").split()
        name = None
        for t in reversed(toks):
            if t.isidentifier() and t not in (
                "char", "int", "long", "unsigned", "signed", "short",
                "void", "const", "struct", "union", "enum", "float",
                "double", "size_t", "time_t", "FILE", "va_list"):
                name = t
                break
        out.append(name or f"arg{i}")
    return out


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    spec = json.loads(Path(sys.argv[1]).read_text())
    out = Path(sys.argv[2])

    bound, deferred = [], []
    for f in spec:
        if f["class"] != "identical" or f["name"] in MACRO_ONLY:
            continue
        ret = map_type(f["ret"])
        ptypes = [map_type(p) for p in f["params"]]
        if ret is None or any(p is None for p in ptypes):
            deferred.append(f"{f['name']}  ({f['ret']}({', '.join(f['params'])}))")
            continue
        names = param_names(f["params"])
        params = ", ".join(f"{n}: {t}" for n, t in zip(names, ptypes))
        args = ", ".join(names)
        fname = f["name"]
        lines = []
        lines.append(f"def {fname}({params}) -> {ret if ret != 'None' else 'Int32'}:")
        lines.append(f'    """{fname} — {f["header"]}, via the SharedCLibrary."""')
        # A void C function still answers with r0; return it — a Mojo
        # def must return its declared type.
        lines.append(
            f'    return external_call["{fname}", {ret if ret != "None" else "Int32"}]({args})')
        bound.append("\n".join(lines))

    body = []
    body.append('"""The C library surface — generated from clib.json by')
    body.append('    tools/clibspec/gen_mojo.py.  Do not edit; regenerate.')
    body.append('')
    body.append('Every wrapper calls the ROM\'s SharedCLibrary through the')
    body.append('roclib veneers (registered by roclib_init/roclib_run in')
    body.append('rostrt).  Mojo code uses these instead of SWIs so it stays')
    body.append('portable across RISC OS versions: the binding is rebuilt,')
    body.append('not the programs.')
    body.append('"""')
    body.append("")
    body.append("from std.ffi import external_call")
    body.append("")
    body.append("# " + "=" * 72)
    body.append("# The portable surface: scalar/pointer prototypes whose")
    body.append("# AAPCS and APCS-32 layouts coincide (clib.json class")
    body.append("# `identical`).  Float, variadic and struct-by-value C")
    body.append("# functions are implemented locally in rostrt and are not")
    body.append("# bound here.")
    body.append("# " + "=" * 72)
    body.append("")
    body.append("\n\n".join(bound))
    body.append("")
    if deferred:
        body.append("")
        body.append("# Deferred to the appendix (function-pointer or exotic")
        body.append("# parameters — bind with `thin abi(\"C\")` types):")
        for d in deferred:
            body.append(f"#   {d}")

    out.write_text("\n".join(body) + "\n")
    print(f"{len(bound)} functions bound, {len(deferred)} deferred -> {out}")


if __name__ == "__main__":
    main()
