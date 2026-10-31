#!/usr/bin/env python3
"""Audit recitation banks for: probes without a locator; stems that contain another card's answer;
source lines that contain another card's answer. Usage: python3 audit_bank.py BANK.md [BANK.md ...]
Prints a compact report; never prints answer text longer than 60 chars."""
import re, sys

CARD = re.compile(r"^\*\*([A-Z]+\d+-\d+)\*\*.*?$", re.M)
LOC = re.compile(r"\[\d{1,3}:\d{2}\]|slide\s*\d+|p\.\s*\d+|page\s*\d+|\bdoi\b|https?://", re.I)
STOP = set("the a an of to in and or is are was were be it that this with for on as by at from his her its they them he she you your what which who how why when where not no yes do does did can could would should will one two three".split())


def norm(t):
    t = re.sub(r"[^a-z0-9 ]", " ", t.lower())
    return " ".join(x for x in t.split() if x not in STOP and len(x) > 2)


def keyphrase(ans):
    """distinctive 4-word windows from an answer, used to detect leaks elsewhere"""
    w = norm(ans).split()
    return {" ".join(w[i:i + 4]) for i in range(max(0, len(w) - 3))} if len(w) >= 4 else ({" ".join(w)} if len(w) >= 3 else set())


def parse(path):
    txt = open(path, encoding="utf-8").read()
    cards = []
    pos = [(m.start(), m.group(1)) for m in CARD.finditer(txt)]
    for i, (st, cid) in enumerate(pos):
        en = pos[i + 1][0] if i + 1 < len(pos) else len(txt)
        block = txt[st:en]
        def field(name):
            m = re.search(r"^- %s:\s*(.*?)$" % name, block, re.M)
            return m.group(1).strip() if m else ""
        cards.append({"id": cid, "q": field("Q"), "a": field("A"), "src": field("Source"),
                      "probe": field("Probe"), "trap": field("Trap"), "tier": (re.search(r"tier (\d)", block) or [None, "?"])[1]})
    return cards


def audit(path):
    cards = parse(path)
    out = {"file": path.split("/")[-1], "n": len(cards), "probe_no_locator": [], "stem_leaks": [], "source_leaks": []}
    for c in cards:
        if c["probe"] and not LOC.search(c["probe"]):
            out["probe_no_locator"].append((c["id"], c["tier"], c["probe"][:60]))
    for c in cards:
        kp = keyphrase(c["a"])
        if not kp:
            continue
        for d in cards:
            if d["id"] == c["id"]:
                continue
            nq, ns = norm(d["q"]), norm(d["src"])
            if any(k in nq for k in kp):
                out["stem_leaks"].append((d["id"], c["id"]))
            if any(k in ns for k in kp) and c["id"] != d["id"]:
                out["source_leaks"].append((d["id"], c["id"]))
    return out


if __name__ == "__main__":
    for p in sys.argv[1:]:
        r = audit(p)
        print("== %s  (%d cards)" % (r["file"], r["n"]))
        print("  probes without locator: %d" % len(r["probe_no_locator"]))
        for cid, tier, pr in r["probe_no_locator"]:
            print("    %s t%s  %s" % (cid, tier, pr))
        print("  stems containing another card's answer: %d" % len(r["stem_leaks"]))
        for d, c in r["stem_leaks"]:
            print("    %s stem contains answer of %s" % (d, c))
        print("  source lines containing another card's answer: %d" % len(r["source_leaks"]))
        for d, c in r["source_leaks"]:
            print("    %s source contains answer of %s" % (d, c))
