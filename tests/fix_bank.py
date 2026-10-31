#!/usr/bin/env python3
"""Add locators to probe lines by matching the probe answer to the cleaned transcript; add order/hold lines for
stems and source lines that contain a sibling card's answer. Writes BANK.fixed.md and prints a report line.
Usage: python3 fix_bank.py BANK.md TRANSCRIPT.md OUT.md"""
import re, sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from audit_bank import parse, norm, keyphrase, LOC

HI, MID = 0.55, 0.38
TL = re.compile(r"^\[(\d{1,3}:\d{2})\]\s*(.*)$")


def load_transcript(path):
    rows = []
    for line in open(path, encoding="utf-8"):
        m = TL.match(line.strip())
        if m:
            rows.append((m.group(1), set(norm(m.group(2)).split())))
    return rows


def best_match(ans, rows):
    w = set(norm(ans).split())
    if len(w) < 3:
        return None, 0.0
    best, bs = None, 0.0
    for t, ws in rows:
        if not ws:
            continue
        ov = len(w & ws) / len(w)
        if ov > bs:
            best, bs = t, ov
    return best, bs


def main(bank, transcript, out):
    txt = open(bank, encoding="utf-8").read()
    rows = load_transcript(transcript)
    cards = parse(bank)
    byid = {c["id"]: c for c in cards}
    stats = {"probes": 0, "hi": 0, "mid": 0, "low": [], "after": 0, "hold": 0}
    # 1. probe locators
    for c in cards:
        if not c["probe"] or LOC.search(c["probe"]):
            continue
        stats["probes"] += 1
        ans = c["probe"].split("→", 1)[1] if "→" in c["probe"] else c["probe"]
        t, sc = best_match(ans, rows)
        if len(set(norm(ans).split())) < 6:
            sc = min(sc, HI - 0.01)   # short answers: chunk match is a hint, not a locator
        old = "- Probe: " + c["probe"]
        if sc >= HI:
            new = old + " [%s]" % t
            stats["hi"] += 1
        elif sc >= MID:
            new = old + " [%s, match unverified]" % t
            stats["mid"] += 1
        else:
            new = old + " [no transcript locator found]"
            stats["low"].append(c["id"])
        assert txt.count(old) == 1, (c["id"], txt.count(old))
        txt = txt.replace(old, new)
    # 2. order and hold lines
    leaks_stem, leaks_src = {}, {}
    for c in cards:
        kp = keyphrase(c["a"])
        if not kp:
            continue
        for d in cards:
            if d["id"] == c["id"]:
                continue
            if any(k in norm(d["q"]) for k in kp):
                leaks_stem.setdefault(d["id"], set()).add(c["id"])
            if any(k in norm(d["src"]) for k in kp):
                leaks_src.setdefault(d["id"], set()).add(c["id"])
    for d, ids in leaks_stem.items():
        anchor = "- Q: " + byid[d]["q"]
        assert txt.count(anchor) == 1, d
        txt = txt.replace(anchor, "- After: %s (this stem contains that card's answer; never pose first)\n%s" % (", ".join(sorted(ids)), anchor))
        stats["after"] += 1
    for d, ids in leaks_src.items():
        ids = ids - leaks_stem.get(d, set())
        if not ids:
            continue
        anchor = "- Source: " + byid[d]["src"]
        assert txt.count(anchor) == 1, d
        txt = txt.replace(anchor, anchor + "\n- Holds: answer of %s; until that card is attempted, cite this source by locator only" % ", ".join(sorted(ids)))
        stats["hold"] += 1
    open(out, "w", encoding="utf-8").write(txt)
    print("%s: probes fixed %d (located %d, unverified %d, none %d%s); after-lines %d; hold-lines %d" % (
        bank.split("/")[-2], stats["probes"], stats["hi"], stats["mid"], len(stats["low"]),
        ": " + " ".join(stats["low"]) if stats["low"] else "", stats["after"], stats["hold"]))


if __name__ == "__main__":
    main(*sys.argv[1:4])
