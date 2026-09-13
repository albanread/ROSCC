#!/usr/bin/env python3
"""extract.py — build the SharedCLibrary binding spec from the DDE headers.

Reads the CLib headers from the DDE export (AcornC.C++/Export/APCS-32/Lib/CLib/h)
and classifies every function prototype by how an AAPCS caller (clang, Mojo)
reaches the APCS-32 SharedCLibrary entry:

  identical  scalar/pointer args and return — the two calling standards lay
             these out identically, so the binding is a direct branch
  variadic   "...": stack-layout sensitive — implement locally (musl), do
             not call through
  float      double/float by value in or out: FPA-era conventions on the
             CLib side — implement locally (soft-float libm), do not call
             through
  structret  struct by value in or out (div_t and friends) — implement locally
  verify     64-bit integer args (long long): AAPCS even-aligns the register
             pair; verify against the DDE-compiled callee before trusting a
             direct branch (torture-harness case)

Output: clib.json  [{name, header, proto, class}, ...] plus a report.

The spec is language-neutral: the veneer generator (ARM objects for rostrt)
and the Mojo declaration generator both read it.

Usage: extract.py <dde-clib-h-dir> [out.json]
"""

import json
import re
import sys
from pathlib import Path

# Headers whose declarations are functions worth binding. Skipped: macro-only
# or type-only headers (assert, errno, float, iso646, limits, setjmp-as-macro,
# stdarg, stdbool, stddef, stdint, tgmath, varargs) and complex/fenv (v1
# scope; nobody in NetSurf's dependency tree calls them).
WANTED = [
    "ctype", "inttypes", "kernel", "locale", "math", "setjmp", "signal",
    "stdio", "stdlib", "string", "swis", "time", "wchar", "wctype",
]

# The DDE's TCPIPLibs export: the BSD socket API over the Internet module
# plus the unix-ish file layer (dirent, unistd). Bound by the same rules —
# every prototype there is scalar/pointer, i.e. direct-branch class.
TCP_WANTED = [
    "dirent", "err", "ifaddrs", "inetlib", "netdb", "resolv", "riscos",
    "socklib", "unixlib", "unistd",
]

# Returns of these typedefs are struct-by-value (div_t and friends).
STRUCT_RET_TYPES = ("div_t", "ldiv_t", "lldiv_t")

# Never call through, whatever the prototype says: the jmp_buf layout is
# private to the compiler that built the callee.
KNOWN_LOCAL = {"setjmp", "longjmp"}

SKIP_DECL = ("typedef", "extern \"C\"", "#")

TYPE_WORDS = re.compile(
    r"\b(void|char|short|int|long|unsigned|signed|float|double|struct|"
    r"union|enum|const|volatile|register|restrict|__restrict|_Bool|size_t|"
    r"FILE|fpos_t|time_t|clock_t|va_list|div_t|ldiv_t|lldiv_t|wint_t|"
    r"_kernel_...)\b"
)


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    return text


def flatten(text: str) -> str:
    text = text.replace("\\\n", " ")
    # Drop preprocessor lines entirely.
    text = "\n".join(
        ln for ln in text.splitlines() if not ln.lstrip().startswith("#")
    )
    # `extern "C" {` linkage blocks wrap whole header bodies; remove the
    # openers and let stray closers be ignored at depth 0 (headers have no
    # function bodies, so a depth-0 '}' is always a linkage close).
    return re.sub(r'extern\s+"C"\s*\{', " ", text)


def split_params(params: str):
    parts, depth, cur = [], 0, ""
    for ch in params:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def classify_param(p: str):
    if p == "...":
        return "variadic"
    if "va_list" in p:
        # AAPCS va_list is a struct; APCS a plain pointer — do not call
        # through, implement locally.
        return "variadic"
    if "double" in p or "float" in p or re.search(r"\bcomplex\b", p):
        return "float"
    if ("struct" in p and "*" not in p) or p.strip() in STRUCT_RET_TYPES:
        return "structret"
    if "long long" in p or "__int64" in p or "int64" in p:
        return "verify"
    return "identical"


def classify(ret: str, params: list[str]) -> tuple[str, str]:
    reasons = []
    rc = classify_param(ret)
    if rc != "identical":
        reasons.append(f"returns {ret.strip()}")
    for p in params:
        c = classify_param(p)
        if c != "identical":
            reasons.append(f"arg `{p}`")
    if any(r for r in reasons):
        if "..." in params or any("va_list" in p for p in params):
            return "variadic", "; ".join(reasons) or "..."
        # float beats structret beats verify for the report class
        for cls in ("float", "structret", "verify"):
            hit = [
                r for r in reasons
                if cls == "float" and ("double" in r or "float" in r)
                or cls == "structret" and ("struct" in r or any(
                    t in r for t in STRUCT_RET_TYPES))
                or cls == "verify" and ("long long" in r or "int64" in r)
            ]
            if hit:
                return cls, "; ".join(reasons)
    return "identical", ""


def extract_header(path: Path):
    text = flatten(strip_comments(path.read_text(errors="replace")))
    # Statements: split on ';' at brace depth 0.
    out = []
    chunk, depth = "", 0
    for ch in text:
        if ch == "{":
            depth += 1
        elif ch == "}" and depth > 0:  # stray linkage closers stay depth 0
            depth -= 1
        if ch == ";" and depth == 0:
            out.append(chunk)
            chunk = ""
        else:
            chunk += ch
    decls = []
    for stmt in out:
        s = " ".join(stmt.split()).strip()
        if not s or any(s.startswith(k) for k in SKIP_DECL):
            continue
        if s == "}":  # linkage-block closer left over after flatten()
            continue
        if s.startswith("extern "):
            s = s[len("extern "):]
        if "(" not in s or ")" not in s:
            continue
        # Name = identifier immediately before the first '(' at depth 0.
        m = re.match(r"^(.*?[*\s])(\w+)\s*\((.*)$", s, flags=re.S)
        if not m:
            continue
        lead, name, rest = m.group(1).strip(), m.group(2), m.group(3)
        if not lead or lead.startswith(("if", "for", "while", "switch", "return")):
            continue
        # Balanced close paren of the parameter list.
        depth, close = 0, None
        for i, ch in enumerate(rest):
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    close = i
                    break
                depth -= 1
        if close is None:
            continue
        params_src = rest[:close]
        if "*" in lead and name in lead:  # e.g. `FILE *fopen(...)` handled above
            pass
        # Skip anything that declares data, not a function: the text after
        # the parens must be empty.
        tail = rest[close + 1:].strip()
        if tail:
            continue
        ret = re.sub(r"^(extern|register)\s+", "", lead)
        params = split_params(params_src)
        if params == ["void"]:
            params = []
        bad = any(("(" in p and ")" not in p) for p in params)
        if bad:
            continue
        if name in KNOWN_LOCAL:
            cls, reason = "local", "compiler-private state (jmp_buf)"
        else:
            cls, reason = classify(ret, params)
        decls.append(
            {
                "name": name,
                "header": path.name,
                "ret": ret,
                "params": params,
                "class": cls,
                "reason": reason,
            }
        )
    return decls


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    hdir = Path(sys.argv[1])
    tcpdir = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    out_path = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("clib.json")

    all_decls, seen = [], {}
    sources = [(hdir, WANTED, "")]
    if tcpdir is not None:
        sources.append((tcpdir, TCP_WANTED, "tcpip/"))
    for d, wanted, prefix in sources:
        for hname in wanted:
            p = d / hname
            if not p.exists():
                print(f"warning: {p} missing", file=sys.stderr)
                continue
            for dl in extract_header(p):
                key = dl["name"]
                if key in seen:  # first declaration wins; #ifdef variants dedup
                    continue
                seen[key] = True
                dl["header"] = prefix + hname
                all_decls.append(dl)

    counts = {}
    for d in all_decls:
        counts[d["class"]] = counts.get(d["class"], 0) + 1

    out_path.write_text(json.dumps(all_decls, indent=1) + "\n")
    print(f"{len(all_decls)} functions -> {out_path}")
    for cls in ("identical", "verify", "structret", "float", "variadic", "local"):
        n = counts.get(cls, 0)
        pct = 100.0 * n / len(all_decls) if all_decls else 0
        print(f"  {cls:10s} {n:4d}  ({pct:.1f}%)")
    print("\nNon-identical functions:")
    for d in all_decls:
        if d["class"] != "identical":
            print(f"  {d['class']:9s} {d['name']:24s} {d['reason']}")


if __name__ == "__main__":
    main()
