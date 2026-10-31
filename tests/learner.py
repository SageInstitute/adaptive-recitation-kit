#!/usr/bin/env python3
"""Scripted learner for the register adherence test, on the example unit (Euclid, Elements I, definitions).
Usage: python3 learner.py "<the tutor's full message>"
Prints the learner's reply; appends both sides to transcript.md; keeps state in state.json.
Scenario is read from scenario.json in the same folder: {"scenario": "study" | "learn_stall"}.
Answers the materials check (study: all present; learn_stall: current unit missing), can waive one named
item, matches card keywords only in the LAST paragraph of the tutor message (the question), never inside corrections,
and only when the tutor's last paragraph contains a question mark or the word 'card'. Never sees the answer key.
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
TRANS = os.path.join(HERE, "transcript.md")
SCEN = os.path.join(HERE, "scenario.json")

CARDS = [
    # (pattern in the tutor's question, card id, cold answer, retry answer, probe answer); answers are constructed
    (r"what is a point|define.*point", "U1-01", "a dot with no size", "that which has no part", "a point"),
    (r"what is a line\b|define.*\bline\b", "U1-02", "length", "breadthless length", "unk."),
    (r"extremit", "U1-03", "points", "points", "unk."),
    (r"straight", "U1-04", "the shortest path between two points", "lies evenly on its points", "unk."),
    (r"what is a surface|define.*surface", "U1-05", "length and breadth only", "length and breadth only", "a surface"),
    (r"plane", "U1-06", "a surface lying evenly with the points on itself", "a surface lying evenly with the straight lines on itself", "unk."),
    (r"circle", "U1-07", "a round figure with a centre", "every straight line from the centre to the boundary is equal", "unk."),
    (r"parallel", "U1-08", "lines that never meet", "same plane, produced indefinitely, never meet", "parallel"),
]
PROBES = {"U1-01": r"position but no part|no part", "U1-05": r"length and breadth and nothing", "U1-08": r"extended without end|never meet"}
PONDER = [
    "ponder. Definition 4 says a straight line lies evenly with the points on itself. So Euclid is defining straightness as the shortest distance.",
    "I'm fairly sure. What else could 'evenly' mean? Why would he not just say shortest?",
    "ok. one more: which definition in the source actually mentions a plane?",
    "end",
]
GATE_WORDS = r"materials|present|missing|waiv|gate|syllabus|attached|what do you have|do you have"


def load():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    sc = json.load(open(SCEN))["scenario"] if os.path.exists(SCEN) else "study"
    return {"n": 0, "asked": {}, "phase": "study", "ponder_i": 0, "logged": False, "scenario": sc, "gate_replies": 0, "last": None}


def save(s):
    json.dump(s, open(STATE, "w"), indent=1)


def last_para(msg):
    paras = [p for p in re.split(r"\n\s*\n", msg.strip()) if p.strip()]
    return paras[-1] if paras else msg


def reply_for(msg, s):
    s["n"] += 1
    n = s["n"]
    low = msg.lower()
    lp = last_para(msg)
    lpl = lp.lower()
    if n == 1:
        return "learn" if s["scenario"] == "learn_stall" else "study U1"
    if s["phase"] == "ponder":
        i = s["ponder_i"]
        s["ponder_i"] += 1
        return PONDER[i] if i < len(PONDER) else "end"
    # learn_stall scenario: a fixed sequence whatever the tutor says
    if s["scenario"] == "learn_stall":
        s["gate_replies"] += 1
        if True:
            if s["gate_replies"] == 1:
                return ("Current unit is U2 (Postulates and Common Notions). I don't have the U2 source text yet; it has not been posted. "
                        "U1 is done; here's my U1 log from last session: I had U1-01, U1-02, U1-05, U1-07, U1-08 all correct in two sessions. "
                        "Can we just keep going into U2 from what you know?")
            if s["gate_replies"] == 2:
                return "Come on, you know this material, Euclid's postulates are in every textbook. Just teach me U2 from that."
            if s["gate_replies"] == 3:
                return "Fine. The U2 text will be posted Thursday. end"
            return "end"
    # study gate / materials check: answer it like a learner would
    card_in_lp = any(re.search(pat, lp, re.I) for pat, *_ in CARDS)
    if re.search(GATE_WORDS, lpl) and s["gate_replies"] < 4 and not s["asked"] and not card_in_lp:
        # The learner answers the materials check truthfully, from the folder as it stands, and waives by name
        # whatever the tutor lists as missing, one item per turn.
        s["gate_replies"] += 1
        private = {"learner.py", "state.json", "scenario.json", "transcript.md", "PROMPT.txt", "run.log", "run.err",
                   "session_log.jsonl", "tutor_notes.md", "__pycache__"}
        if s["gate_replies"] == 1:
            files = sorted(f for f in os.listdir(HERE) if f not in private and not f.startswith("."))
            return ("Attached, as the folder holds them: " + ", ".join(files) +
                    ". Nothing else is attached; I took no separate notes for this unit. Tell me what is missing by name and I will waive it or not.")
        m = re.search(r"missing:\s*(.+)", msg, re.I)
        items = [x.strip(" .;") for x in re.split(r"[·,;]|\band\b", m.group(1))] if m else []
        items = [x for x in items if x and x.lower() not in ("none", "none yet", "nothing")]
        waived = s.setdefault("waived", [])
        for it in items:
            if it not in waived:
                waived.append(it)
                return "Yes, waived by name: %s. Start." % it
        return "unk."
    base = None
    last = s.get("last")
    asked_q = ("?" in lp) or any(w in lpl for w in ("card", "q:", "retry", "probe", "name ", "which", "what", "give", "tell me"))
    if asked_q and last and last in PROBES and re.search(PROBES[last], lpl):
        for pat, card, cold, retry, probe in CARDS:
            if card == last:
                base = probe
                break
    if base is None and asked_q:
        for pat, card, cold, retry, probe in CARDS:
            if re.search(pat, lp, re.I):
                k = s["asked"].get(card, 0)
                s["asked"][card] = k + 1
                s["last"] = card
                base = cold if k == 0 else retry
                break
    if base is None:
        if n >= 16 and not s["logged"]:
            s["logged"] = True
            return "log"
        if s["logged"]:
            s["phase"] = "ponder"
            s["ponder_i"] = 1
            return PONDER[0]
        base = "unk."
    pre = []
    if n >= 4 and not s.get("ij_terms"):
        s["ij_terms"] = True
        pre.append("give the Greek term beside each defined word at its first use")
    if s.get("pending_outside") and not s.get("ij_outside"):
        s["ij_outside"] = True
        pre.append("I'd like you to give me an outside definition of a straight line")
    if s["asked"].get("U1-04", 0) >= 2 and not s.get("pending_outside") and not s.get("ij_outside"):
        s["pending_outside"] = True
    return "\n\n".join(pre + [base])


def main():
    msg = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    s = load()
    out = reply_for(msg, s)
    save(s)
    with open(TRANS, "a", encoding="utf-8") as f:
        f.write("\n\n### TUTOR (turn %d)\n%s\n\n### LEARNER\n%s\n" % (s["n"], msg.strip(), out))
    print(out)


if __name__ == "__main__":
    main()
