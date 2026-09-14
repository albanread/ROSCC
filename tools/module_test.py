#!/usr/bin/env python3
"""End-to-end test for `roscc link --module`: build a module whose body is
Mojo, then make RISC OS 5 load it, run it and kill it.

Two halves, and both matter:

  * Static — read the image the way RISC OS will: the 13-word header, the
    offsets it holds, the strings and the command table they point at.
    A module with a bad header is refused with no diagnosis worth having,
    so check it here where the failure can be explained.

  * Live — boot RISC OS 5.30 in the instrumented RPCEmu, *RMLoad the file,
    *Help it, run its command and *RMKill it, and read the console back.
    The command prints a number Mojo computed (sum of fib(1..20) = 17710),
    so the test can tell "the module loaded" from "the Mojo code ran".

Usage:
    python tools/module_test.py [--build] [--module PATH]

--build runs tools/build-module.sh first. The machine is cold booted every
run (about ten seconds): restoring a snapshot brings the guest portal back
without the handshake the host needs to drive it, so --snapshot is off by
default.
"""
import argparse
import os
import shutil
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
COMPILER = os.path.dirname(HERE)
EMU_TOOLS = r"F:\RISCOSDEV\rpcemu\src\tools"
EMU_CWD = r"F:\RISCOSDEV\rpcemu\win32\RPCEmu"
HOSTFS = os.path.join(EMU_CWD, "hostfs")
DEFAULT_MODULE = os.path.join(COMPILER, "tests", "module", "build", "mojomod,ffa")
# HostFS names its disc "HostFS"; the ",ffa" host suffix is the filetype, so
# the RISC OS leafname has no comma in it.
GUEST_PATH = "HostFS::HostFS.$.mojomod"
CALLER_PATH = "HostFS::HostFS.$.swicall"

# Chunk base the module publishes: user-application range (PRM 1-27), a
# multiple of 64, zero top byte.
SWI_CHUNK = 0xC7000
SNAPSHOT = os.path.join(EMU_CWD, "module_test.snap")

# sum(fib(1..20)) == fib(22) - 1. If this number reaches the console the
# module's Mojo body really executed; a header alone could not produce it.
FIB_SUM = 17710

HDR = ["start", "init", "final", "service", "title", "help", "commands",
       "swi_chunk", "swi_handler", "swi_names", "swi_decode", "messages",
       "flags"]

try:  # the console here is cp1252; the text is not
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

fails = []


def check(ok, what, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f" — {detail}" if detail else ""))
    if not ok:
        fails.append(what)
    return ok


def cstring(d, off):
    end = d.index(b"\0", off)
    return d[off:end].decode("latin1")


def check_image(path):
    """Read the module the way the OS does, and complain the way it won't."""
    print(f"\n== image: {path}")
    d = open(path, "rb").read()
    check(len(d) >= 52, "image holds a full 13-word header", f"{len(d)} bytes")
    h = struct.unpack_from("<13I", d, 0)
    for name, v in zip(HDR, h):
        print(f"       &{HDR.index(name) * 4:02X} {name:<12} {v:#x}")

    # Every offset except the SWI chunk number must land inside the image.
    for i, name in enumerate(HDR):
        if i == 7 or h[i] == 0:
            continue
        check(h[i] < len(d), f"header offset {name} is inside the image",
              f"{h[i]:#x} vs {len(d):#x}")
    for i in (0, 1, 2, 3, 8):
        if h[i]:
            check(h[i] % 4 == 0, f"code offset {HDR[i]} is word aligned")

    check(h[4] != 0, "module has a title (RISC OS cannot name it otherwise)")
    check(cstring(d, h[4]) == "MojoMod", "title is MojoMod", cstring(d, h[4]))
    check(h[12] != 0, "module has a flags word")
    flags = struct.unpack_from("<I", d, h[12])[0]
    check(flags & 1 == 1, "flags bit 0 set: 32-bit compatible",
          f"{flags:#x} — RISC OS 5 refuses the module without it")
    check(h[1] != 0 and h[2] != 0, "init and final entries present")

    # Command table: string, align, code offset, info word, syntax, help.
    o, cmds = h[6], []
    check(o != 0, "module has a command table")
    while o and d[o] != 0:
        name = cstring(d, o)
        o = (o + len(name) + 1 + 3) & ~3
        code, info, _syntax, helpo = struct.unpack_from("<4I", d, o)
        o += 16
        cmds.append((name, code, info, cstring(d, helpo)))
    for name, code, info, htext in cmds:
        print(f"       *{name}: code +{code:#x}, min {info & 0xff}, "
              f"max {(info >> 16) & 0xff}")
        check(0 < code < len(d), f"*{name} code offset is inside the image")
        check(code % 4 == 0, f"*{name} code offset is word aligned")
        check(bool(htext), f"*{name} has help text")
    check(any(c[0] == "MojoMod" for c in cmds), "*MojoMod is in the table")

    # SWI fields. The kernel ignores all three unless the chunk base passes
    # its checks (PRM 1-223), so a module with a bad chunk loads happily and
    # simply has no SWIs — worth catching here rather than in the emulator.
    chunk, handler, names = h[7], h[8], h[9]
    check(chunk != 0 and handler != 0, "module offers SWIs",
          f"chunk &{chunk:X}, handler +{handler:#x}")
    check(chunk % 64 == 0, "SWI chunk base is a multiple of 64")
    check(chunk >> 24 == 0, "SWI chunk base has a zero top byte")
    check((chunk >> 18) & 3 == 3, "SWI chunk is in the user-application range",
          "bits 19:18 = 11, PRM 1-27")
    check(0 < handler < len(d) and handler % 4 == 0,
          "SWI handler offset is word aligned and inside the image")

    # Decoding table: group prefix, then one name per SWI, then a zero byte.
    check(names != 0, "module has a SWI decoding table")
    o, swi_names = names, []
    prefix = cstring(d, o)
    o += len(prefix) + 1
    while d[o] != 0:
        n = cstring(d, o)
        swi_names.append(n)
        o += len(n) + 1
    check(prefix == "MojoMod", "decoding table's group prefix is MojoMod", prefix)
    for i, n in enumerate(swi_names):
        print(f"       &{chunk + i:X}  {prefix}_{n}")
    check(swi_names == ["Add", "Counter"],
          "decoding table names Add and Counter in chunk order",
          ", ".join(swi_names))
    return d


def run_on_riscos(module_path, snapshot=None, keep_snapshot=False):
    """Load the module in RISC OS 5.30 and drive it from the host.

    Commands go through the guest portal (the RPCAgent module), not the
    keyboard: it runs them from a transient callback and reports back what
    happened, so a failed command gives its RISC OS error rather than
    silence — OS_CLI returns errors to its caller instead of printing them.
    """
    sys.path.insert(0, EMU_TOOLS)
    from rpc_client import Machine  # noqa: E402

    shutil.copy(module_path, os.path.join(HOSTFS, "mojomod,ffa"))
    caller = os.path.join(os.path.dirname(module_path), "swicall,ff8")
    have_caller = os.path.exists(caller)
    if have_caller:
        shutil.copy(caller, os.path.join(HOSTFS, "swicall,ff8"))
    print()
    print(f"== copied to hostfs as mojomod,ffa (RISC OS sees {GUEST_PATH})"
          + ("  + swicall,ff8" if have_caller else ""))

    m = Machine()
    try:
        if snapshot and os.path.exists(snapshot):
            m.call("snapshot.load", path=snapshot, timeout=60)
            print("== restored from snapshot")
        else:
            print("== cold boot", end="", flush=True)
            t0 = time.time()
            for _ in range(180):
                if m.call("portal.status", timeout=30).get("present"):
                    break
                time.sleep(1)
                print(".", end="", flush=True)
            else:
                raise RuntimeError("guest portal never appeared")
            time.sleep(4)  # let the boot sequence finish talking
            print(f" up in {time.time() - t0:.0f}s")
            if keep_snapshot:
                m.call("snapshot.save", path=SNAPSHOT, timeout=120)

        def cmd(command, quote=10, timeout=30):
            """Run one * command; return (console text, portal status)."""
            before = m.call("vdu.read", timeout=30)
            prev = before.get("text", "") if isinstance(before, dict) else ""
            done = m.call("portal.status", timeout=30).get("commands", 0)
            m.call("portal.run", command=command, timeout=60)
            # The portal takes one command at a time and runs it from a
            # callback, so wait for its completed count to move rather than
            # guessing at a delay.
            # Two ways a command can be over. Normally the portal's completed
            # count moves and it reports the RISC OS error, if any. But a
            # command that starts an application never comes back to the
            # portal's callback, so that never happens for *Run — there,
            # settled console output is the only end-of-command signal.
            deadline = time.time() + timeout
            st, whole = {}, prev
            last_change = time.time()
            while time.time() < deadline:
                st = m.call("portal.status", timeout=30)
                busy = st.get("pending") or st.get("armed") or st.get("running")
                if not busy and st.get("commands", 0) > done:
                    time.sleep(0.6)  # output trails the portal's report
                    break
                now = m.call("vdu.read", timeout=30).get("text", "")
                if now != whole:
                    whole, last_change = now, time.time()
                elif len(whole) > len(prev) and time.time() - last_change > 2.0:
                    st["settled"] = True
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError(f"*{command} did not finish in {timeout}s")
            # Take the console delta ourselves: what this command printed is
            # whatever the capture grew by while it ran.
            v = m.call("vdu.read", timeout=30)
            whole = v.get("text", "") if isinstance(v, dict) else str(v)
            text = whole[len(prev):] if whole.startswith(prev) else whole
            print()
            print(f"  *{command}")
            if st.get("failed"):
                print(f"    ! RISC OS error {st.get('error_number', 0):#x}: "
                      f"{st.get('error', '')}")
            lines = [l for l in text.splitlines() if l.strip()]
            for line in lines[:quote]:
                print(f"    | {line}")
            if len(lines) > quote:
                print(f"    | ... {len(lines) - quote} more line(s)")
            return text, st

        load, st = cmd(f"RMLoad {GUEST_PATH}")
        check(not st.get("failed"), "*RMLoad accepted the module",
              st.get("error", ""))
        check("initialised" in load,
              "init ran, and its message came from Mojo")

        mods, _ = cmd("Modules", quote=0)
        check("MojoMod" in mods, "*Modules lists MojoMod in the module chain")

        helptext, _ = cmd("Help MojoMod")
        check("MojoMod" in helptext and "1.00" in helptext,
              "*Help MojoMod prints the module's help string")

        out, st = cmd("MojoMod")
        check(not st.get("failed"), "*MojoMod returned without error",
              st.get("error", ""))
        check("Mojo code running inside a RISC OS module" in out,
              "the command veneer reached the Mojo body")
        check(str(FIB_SUM) in out,
              f"Mojo computed fib(1..20) = {FIB_SUM} inside the module",
              "the number is proof the body ran, not just the header")

        def run_app(command, quote=10, timeout=60):
            """Start an application and read back what it printed.

            Typed at the prompt rather than sent through the portal: starting
            an application never returns to the portal's callback, so the
            portal would report the command as still in flight for ever and
            refuse every command after it. Settled console output is the
            end-of-run signal instead.
            """
            prev = m.call("vdu.read", timeout=30).get("text", "")
            m.call("type", text=command + "\r", timeout=120)
            whole, last_change, t0 = prev, time.time(), time.time()
            while time.time() - t0 < timeout:
                now = m.call("vdu.read", timeout=30).get("text", "")
                if now != whole:
                    whole, last_change = now, time.time()
                elif len(whole) > len(prev) and time.time() - last_change > 2.5:
                    break
                time.sleep(0.25)
            text = whole[len(prev):] if whole.startswith(prev) else whole
            print()
            print(f"  *{command}   (typed)")
            lines = [l for l in text.splitlines() if l.strip()][1:]  # drop echo
            for line in lines[:quote]:
                print(f"    | {line}")
            if len(lines) > quote:
                print(f"    | ... {len(lines) - quote} more line(s)")
            return text

        if have_caller:
            # The outside half: a normal Absolute image, built by the same
            # toolchain, calling the module the way any program would.
            call = run_app(f"Run {CALLER_PATH}")
            check(bool(call.strip()), "the caller application ran")
            check(f"= &{SWI_CHUNK:X}" in call,
                  "OS_SWINumberFromString resolved MojoMod_Add through the "
                  "module's decoding table", f"expected &{SWI_CHUNK:X}")
            check("MojoMod_Add(20, 22) = 42" in call,
                  "the SWI dispatched into Mojo and returned R0 = 42")
            check("1 command(s), 2 SWI(s) so far" in call,
                  "MojoMod_Counter read state written by a different entry "
                  "point back out of module workspace",
                  "the *MojoMod run earlier, plus Add and Counter themselves")
            check("unimplemented SWI refused" in call,
                  "the dispatcher's bounds check refused a SWI in range but "
                  "not implemented")
            # The static-base model, reported by the module itself: a C .bss
            # counter and a C .data value. Both are writable statics, which a
            # module could not have at all until the linker gave them their
            # own address space.
            check(".bss counter = 1" in call,
                  "a C static in .bss worked: the area was claimed and zeroed, "
                  "and r9 addressed it")
            check(".data seed = &5A5A" in call,
                  "a C static in .data worked: the linker carried its initial "
                  "value and initialisation copied it in")

        kill, st = cmd("RMKill MojoMod")
        check(not st.get("failed"), "*RMKill accepted the module",
              st.get("error", ""))
        check("finalised" in kill, "finalisation ran on the way out")
        check("1 command(s) and 2 SWI(s)" in kill,
              "the workspace counters survived every call until the kill",
              "one *MojoMod, and two SWIs — the refused one never reached "
              "a handler, so it is not counted")

        gone, _ = cmd("Modules", quote=0)
        check("MojoMod" not in gone, "MojoMod has left the module chain")
    finally:
        try:
            m.call("quit", timeout=10)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true",
                    help="run tools/build-module.sh first")
    ap.add_argument("--module", default=DEFAULT_MODULE)
    ap.add_argument("--snapshot", default=None,
                    help="booted-machine snapshot to start from. Off by "
                         "default: restoring one leaves the host half of the "
                         "guest portal without its handshake, so portal.run "
                         "refuses. Cold boot takes about a minute.")
    ap.add_argument("--static-only", action="store_true",
                    help="check the image, do not start the emulator")
    a = ap.parse_args()

    if a.build:
        import subprocess
        subprocess.run(["bash", os.path.join(HERE, "build-module.sh")],
                       check=True, cwd=COMPILER)

    if not os.path.exists(a.module):
        sys.exit(f"module_test: {a.module} not found — run with --build")

    check_image(a.module)
    if not a.static_only:
        run_on_riscos(a.module, a.snapshot)

    print()
    if fails:
        print(f"FAILED: {len(fails)} check(s)")
        for f in fails:
            print(f"  - {f}")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
