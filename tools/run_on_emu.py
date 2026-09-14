#!/usr/bin/env python3
"""Drive the agent-controlled RPCEmu to run a RISC OS program and collect
its console output. Usage:

    run_on_emu.py <file-on-hostfs> [args]

Spawns rpcemu-headless --rpc, boots (or restores a snapshot if present),
runs the program with */ and prints the captured console text.
"""
import json
import os
import subprocess
import sys
import time

EMU = r"F:\RISCOSDEV\rpcemu\win32\RPCEmu\rpcemu-headless.exe"
CWD = r"F:\RISCOSDEV\rpcemu\win32\RPCEmu"
SNAP = os.path.join(CWD, "boot.snap")


class Rpc:
    def __init__(self):
        self.p = subprocess.Popen(
            [EMU, "--rpc"], cwd=CWD,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        )
        self.id = 0

    def call(self, method, **params):
        self.id += 1
        req = {"jsonrpc": "2.0", "id": self.id, "method": method}
        if params:
            req["params"] = params
        self.p.stdin.write(json.dumps(req) + "\n")
        self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("emulator closed the channel")
            obj = json.loads(line)
            if obj.get("id") == self.id:
                if "error" in obj:
                    raise RuntimeError(f"{method}: {obj['error']}")
                return obj.get("result")

    def close(self):
        try:
            self.call("quit")
        except Exception:
            pass
        self.p.wait(timeout=10)


def main():
    target = sys.argv[1].replace("\\", ".")
    rpc = Rpc()
    try:
        # Boot: prefer a saved snapshot (0.02s) over a cold boot (~9s).
        if os.path.exists(SNAP):
            rpc.call("snapshot.load", path=SNAP)
            print("[booted from snapshot]")
        else:
            t0 = time.time()
            # Cold boot: wait until the desktop is up (idle).
            for _ in range(120):
                st = rpc.call("status")
                if st.get("idle") or st.get("state") == "running":
                    time.sleep(5)
                    break
                time.sleep(1)
            print(f"[cold boot {time.time()-t0:.0f}s]")
            rpc.call("snapshot.save", path=SNAP)

        vdu_before = rpc.call("vdu.read")
        since = vdu_before.get("cursor", 0) if isinstance(vdu_before, dict) else 0

        r = rpc.call("cli", command=f"/{target}")
        print("=== console ===")
        vdu = rpc.call("vdu.read", since=since)
        text = vdu.get("text", "") if isinstance(vdu, dict) else str(vdu)
        print(text)
        print("=== result ===")
        print(json.dumps(r, indent=2)[:500])
    finally:
        rpc.close()


if __name__ == "__main__":
    main()
