"""How much of the OS can the shim generator reach, and what stops it?

The objective is that no user program issues a SWI, which only holds if the
library covers everything a program might want. That makes the skipped
categories the whole story, so this counts them: per prefix, how many SWIs
the generator binds today and what the rest are blocked on.

Applies exactly the rules in gen_riscos_pkg.py, so the numbers here and the
generator's own output cannot drift apart.
"""
import argparse
import collections
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from gen_riscos_pkg import HAND_OVERRIDE, reg_num          # noqa: E402


def bucket(row):
    """Which category this SWI falls into, by the generator's own rules."""
    if row["name"] in HAND_OVERRIDE:
        return "hand"

    entry = json.loads(row["entry_regs"] or "[]")
    on_entry = (row["on_entry"] or "").lower()

    if "reason" in on_entry or "reason code" in on_entry:
        return "reason-mux"
    if any(reg_num(e[0]) > 3 for e in entry) or len(entry) > 4:
        return "beyond-r3"
    if "‘task’" in on_entry or "× 100" in on_entry or "'task'" in on_entry:
        return "magic"

    return "bindable"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(HERE, "..", "..", "db",
                                                 "riscos_prm.sqlite"))
    ap.add_argument("--min", type=int, default=1,
                    help="only show prefixes with at least this many SWIs")
    a = ap.parse_args()

    con = sqlite3.connect(a.db)
    con.row_factory = sqlite3.Row

    documented = con.execute(
        "SELECT name, on_entry, entry_regs, exit_regs, number FROM swi "
        "WHERE documented=1 AND number IS NOT NULL ORDER BY name").fetchall()

    undocumented = con.execute(
        "SELECT count(*) FROM swi WHERE documented=0 OR number IS NULL"
    ).fetchone()[0]

    per_prefix = collections.defaultdict(collections.Counter)
    totals = collections.Counter()

    for row in documented:
        prefix = row["name"].split("_", 1)[0]
        kind = bucket(row)
        per_prefix[prefix][kind] += 1
        totals[kind] += 1

    print("Documented SWIs with a number: %d" % len(documented))
    print("Index-only rows (number, no registers): %d" % undocumented)
    print()

    order = ["bindable", "reason-mux", "beyond-r3", "magic", "hand"]
    print("%-16s %7s %7s %7s %7s %7s %7s"
          % ("prefix", "total", "ok", "reason", ">R3", "magic", "hand"))

    rows = sorted(per_prefix.items(), key=lambda kv: -sum(kv[1].values()))
    shown = 0
    for prefix, counts in rows:
        total = sum(counts.values())
        if total < a.min:
            continue
        shown += total
        print("%-16s %7d %7d %7d %7d %7d %7d"
              % (prefix, total, counts["bindable"], counts["reason-mux"],
                 counts["beyond-r3"], counts["magic"], counts["hand"]))

    print("%-16s %7d %7d %7d %7d %7d %7d"
          % ("TOTAL", len(documented), totals["bindable"], totals["reason-mux"],
             totals["beyond-r3"], totals["magic"], totals["hand"]))

    reach = totals["bindable"] + totals["hand"]
    print()
    print("Reachable by the generator today: %d of %d (%.0f%%)"
          % (reach, len(documented), 100.0 * reach / len(documented)))
    print("Blocked: %d reason-multiplexed, %d use registers above R3, %d magic"
          % (totals["reason-mux"], totals["beyond-r3"], totals["magic"]))

    # The reason-multiplexed families are the big prize: each is many calls
    # hiding behind one SWI number, and they are the ones user code reaches
    # for most often.
    print()
    print("Largest reason-multiplexed SWIs (each is really many calls):")
    for row in documented:
        if bucket(row) != "reason-mux":
            continue
        text = row["on_entry"] or ""
        if len(text) > 1200:
            print("   %-28s %6d characters of reason codes"
                  % (row["name"], len(text)))

    return 0


if __name__ == "__main__":
    sys.exit(main())
