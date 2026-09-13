#!/usr/bin/env python3
"""dbg.py — drive a QEMU GDB stub directly: breakpoint, step, log.

The farm's emulator runs with `-gdb tcp::PORT` (relaunch a machine with
the flag added to its launch.json argv).  This little RSP client exists
because batch lldb fights the QEMU stub; here a step-and-log loop is
five lines.  Usage:

    dbg.py <port> <break-addr> [max-steps]

Sets a breakpoint, continues, then single-steps recording every pc;
prints the tail of the trace and the registers whenever the walk leaves
application space for the RISC OS handler (an Internal Error has
happened by then — the last module-side pcs are the fault's origin).
"""

import socket
import sys
import time


class Stub:
    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.s.sendall(b"+")
        self.buf = b""

    def _recv(self):
        while True:
            # skip protocol acks that arrive ahead of a packet
            while self.buf[:1] in (b"+", b"-"):
                self.buf = self.buf[1:]
            if b"#" in self.buf and len(self.buf.split(b"#", 1)[1]) >= 2 \
                    and b"$" in self.buf.split(b"#", 1)[0]:
                break
            d = self.s.recv(4096)
            if not d:
                raise EOFError("stub closed")
            self.buf += d
        i = self.buf.index(b"#")
        pkt, self.buf = self.buf[self.buf.index(b"$") + 1:i], self.buf[i + 3:]
        self.s.sendall(b"+")
        return pkt

    def cmd(self, data):
        cs = sum(data.encode()) & 0xFF
        self.s.sendall(f"${data}#{cs:02x}".encode())
        # swallow acks, then the reply
        while True:
            r = self._recv()
            return r

    def cont(self):
        self.s.sendall(b"$c#63")
        return self._recv()

    def step(self):
        self.s.sendall(b"$s#73")
        return self._recv()

    def regs(self):
        g = self.cmd("g").decode()
        vals = []
        for i in range(17):  # r0-r15 + cpsr
            h = g[i * 8:(i + 1) * 8]
            vals.append(int.from_bytes(bytes.fromhex(h), "little"))
        return vals

    def mem(self, addr, ln):
        r = self.cmd(f"m{addr:x},{ln:x}").decode()
        if r.startswith("E"):
            return None
        return bytes.fromhex(r)


def main():
    port, brk = int(sys.argv[1]), int(sys.argv[2], 0)
    max_steps = int(sys.argv[3]) if len(sys.argv) > 3 else 60000

    st = Stub(port)
    print("stub:", st.cmd("qSupported:packet-size=1024")[:40])
    print("bp:", st.cmd(f"Z0,{brk:x},1"))
    watches = [int(w, 0) for w in sys.argv[4:]]
    for w in watches:
        print(f"watch {w:#x}:", st.cmd(f"Z2,{w:x},4"))
    print("continue ->", st.cont())

    trace = []
    for i in range(max_steps):
        r = st.regs()
        pc = r[15]
        trace.append(pc)
        if pc not in (0xFFFF0008,) and 0xFFFF0000 <= pc < 0xFFFF0020 and pc != brk:
            # stop replies at watchpoints carry the faulting pc directly
            pass
        if pc in (0xFFFF000C, 0xFFFF0010):  # prefetch/data abort only:
            # 0xFFFF0008 is the SWI vector — every call passes through.
            print(f"\nstep {i}: pc hit abort vector {pc:#x}")
            break
        if pc == 0:
            print(f"\nstep {i}: pc = 0")
            break
        st.step()

    # compress: runs of consecutive same-region pcs
    def region(p):
        if p < 0x8000:
            return "low"
        if p < 0x100000:
            return "app"
        if p < 0xF0000000:
            return "ws"
        return "rom"

    tail = trace[-60:]
    print(f"\n{len(trace)} steps; last 60 pcs:")
    prev = None
    for p in tail:
        mark = "" if region(p) == prev else f"   <-- {region(p)}"
        print(f"  {p:#010x}{mark}")
        prev = region(p)

    print("\nregisters at stop:")
    names = [f"r{i}" for i in range(13)] + ["sp", "lr", "pc"]
    r = st.regs()
    for n, v in list(zip(names, r))[:16]:
        print(f"  {n:3s} {v:#010x}")
    st.cmd("D")  # detach


if __name__ == "__main__":
    main()
