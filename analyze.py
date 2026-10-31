#!/usr/bin/env python3
"""
analyze.py - Adaptive Recitation Kit analyzer (v1.6)

Python 3.8+, standard library only. Reads an event log (JSON Lines), estimates
retention / recall / retrieval trends, and writes:
  profile.json   machine-readable metrics, rules fired, queue
  brief.md       plain-language brief a tutor (any model) can act on

Commands
  python3 analyze.py analyze LOG.jsonl [--out DIR] [--overlay FILE] [--today YYYY-MM-DD] [--exam YYYY-MM-DD] [--terms FILE]
    --terms FILE: {"L5-01": {"must": ["..."], "must_not": ["..."]}, ...}; each logged "answer" is audited against its card
  python3 analyze.py add LOG.jsonl key=value [key=value ...]
  python3 analyze.py selftest

LOG FORMAT: one JSON object per line; lines starting with # are ignored.
  {"type":"meta","session":"s1","date":"2026-10-10","course":"...","unit":"...","exam":"2026-10-14"}
  {"type":"attempt","session":"s1","item":"L5-01","concept":"coarticulation","framework":"speech-signal",
   "stage":"cold|retry|probe|review","format":"short|mc|scenario|list|paired|explain|contrast",
   "presentation":"question_first|source_first","verdict":"correct|partial|wrong|none",
   "error":["no_answer","intrusion","misframe","omission","extra_unsupported","wording","format"],
   "intruder":"<framework or concept the answer belonged to>","support":"none|slide|hint|outside_def|explanation",
   "feedback":"quote|quote+slide|outside_def|mechanism|contrast","latency_s":null,"confidence":null,"note":"..."}
  {"type":"signal","session":"s1","kind":"instruction|reconcile|source_request|meaning_request|example_request|
   outside_definition|integrate|insight|confusion|locate|parked|pace_fast|pace_slow|waiver|override|override_request|
   stall|gate_hold|mode","concept":"...","note":"..."}
  meta may also carry "mode":"study|learn|ponder", "prev_unit":"...", and
   "materials":{"present":[...],"missing":[...],"waived":[...]}  (the study gate and the learn stall read these)
  attempt may carry "grain":"concept|component|process" (learn mode), "unit":"...", and "open_item" (the card that was
   open when the reply arrived; if it differs from "item" the analyzer flags it). "note" is allowed on every line.
  lag = number of DISTINCT other items attempted between two attempts of the same item (probes excluded).
  {"type":"claim","session":"s1","holder":"learner|literature|model","concept":"...","text":"...","source":"...","status":"open|agreed|disputed|retracted"}
Only "type" is required. Everything else is optional; missing fields reduce what can be inferred, nothing breaks.
The analyzer never reads clinical or personal content; keep logs to course material only.
"""
import argparse, collections, datetime as dt, json, os, sys, tempfile

VERSION = "1.6"
SCORE = {"correct": 1.0, "partial": 0.5, "wrong": 0.0, "none": 0.0}
ALIASES = {"partly": "partial", "no": "wrong", "miss": "wrong", "missed": "wrong", "blank": "none",
           "unk": "none", "right": "correct", "yes": "correct", "ok": "correct"}
ERRORS = ["no_answer", "intrusion", "misframe", "omission", "extra_unsupported", "wording", "format"]
SIGNALS = ["instruction", "reconcile", "source_request", "meaning_request", "example_request",
           "outside_definition", "integrate", "insight", "confusion", "pace_fast", "pace_slow",
           "waiver", "override", "override_request", "stall", "gate_hold", "mode", "locate", "parked"]
MODES = ("study", "learn", "ponder")
GRAINS = ("concept", "component", "process")
HOLDERS = ("learner", "literature", "model")
CARD_SIGNALS = {"example_request", "reconcile", "integrate", "meaning_request", "outside_definition"}
DEFAULTS = {
    "cold_low": 0.35, "cold_high": 0.65, "short_lag": 6, "retry_ok": 0.75,
    "probe_low": 0.60, "probe_high": 0.80, "format_gap": 0.30, "recency": 0.75,
    "min_n_tentative": 4, "min_n_act": 8, "run_wall": 4,
    "contrast_mastery": 0.60, "contrast_conf": 0.50, "grain_share": 0.60, "known_box": 3,
    # v1.6: review at open, the final pass, calibration, cross-unit mixing, the gate's proof of opening
    "retention_low": 0.60, "retention_high": 0.85, "final_days": 7, "cross_unit_share": 0.25, "gate_proof": "auto",
}
LAG_BANDS = (("same day", 0, 0), ("1 to 2 days", 1, 2), ("3 to 6 days", 3, 6), ("7+ days", 7, 10 ** 6))
CONF_WORDS = {"sure": 1.0, "unsure": 0.0, "1": 1.0, "0": 0.0, "true": 1.0, "false": 0.0}


def conf_of(v):
    """A confidence as the learner gave it: a word or a number, else None."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return 1.0 if float(v) >= 0.5 else 0.0
    return CONF_WORDS.get(str(v).strip().lower())


def proven(entry):
    """A `present` entry proves it was opened when it carries a quoted line (or a `line` field)."""
    if isinstance(entry, dict):
        return bool(str(entry.get("line") or "").strip())
    t = str(entry)
    return any(q in t for q in ('"', "\u201c", "\u201d", "'", "\u2018", "\u2019"))


def entry_name(entry):
    if isinstance(entry, dict):
        return str(entry.get("name") or entry.get("item") or "?")
    return str(entry)


# ----------------------------------------------------------------------------- loading
def load(path):
    events, problems = [], []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                e = json.loads(line)
            except Exception as ex:
                problems.append("line %d: not JSON (%s)" % (i, ex))
                continue
            if not isinstance(e, dict) or "type" not in e:
                problems.append("line %d: missing \"type\"" % i)
                continue
            e["_seq"] = len(events)
            events.append(e)
    return events, problems


def as_list(v):
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [x.strip() for x in str(v).split(",") if x.strip()]


def norm_attempt(e, problems):
    v = str(e.get("verdict", "none")).strip().lower()
    v = ALIASES.get(v, v)
    if v not in SCORE:
        problems.append("seq %d: unknown verdict %r treated as none" % (e.get("_seq", -1), e.get("verdict")))
        v = "none"
    e["verdict"] = v
    e["score"] = SCORE[v]
    errs = [x for x in as_list(e.get("error")) if x in ERRORS or not problems.append("unknown error tag %r kept" % x)]
    if v == "none" and "no_answer" not in errs:
        errs.append("no_answer")
    if v == "correct" and errs:
        problems.append("seq %d: verdict correct with error %s; downgraded to partial (correct never carries an error tag)" % (e.get("_seq", -1), errs))
        v = "partial"
        e["score"] = SCORE[v]
    e["error"] = errs
    e["verdict"] = v
    e["item"] = str(e.get("item", "?"))
    e["concept"] = str(e.get("concept") or e["item"])
    return e


def session_dates(events):
    sd = {}
    for e in events:
        if e["type"] == "meta" and e.get("session") and e.get("date"):
            sd[e["session"]] = e["date"]
    return sd


def event_date(e, sd):
    if e.get("session") in sd:
        return sd[e["session"]]
    t = e.get("t")
    return t[:10] if isinstance(t, str) and len(t) >= 10 else None


def parse_date(s):
    try:
        return dt.date.fromisoformat(s)
    except Exception:
        return None


# ----------------------------------------------------------------------------- core metrics
def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def pct(x):
    return "n/a" if x is None else "%d%%" % round(100 * x)


def build(events, cfg, today=None, exam=None):
    problems = []
    sd = session_dates(events)
    attempts = [norm_attempt(e, problems) for e in events if e["type"] == "attempt"]
    signals = [e for e in events if e["type"] == "signal"]
    metas = [e for e in events if e["type"] == "meta"]
    claims = [e for e in events if e["type"] == "claim"]
    with_mat = [m for m in metas if m.get("materials")]
    last_meta = (with_mat or metas or [{}])[-1]
    mode = str(last_meta.get("mode", "")).lower() or None
    if mode and mode not in MODES:
        problems.append("unknown mode %r in meta; treated as unset" % mode)
        mode = None
    mats_raw = last_meta.get("materials") or {}
    present_raw = mats_raw.get("present") if isinstance(mats_raw.get("present"), list) else as_list(mats_raw.get("present"))
    unproven = [entry_name(x) for x in (present_raw or []) if not proven(x)]
    mats = {k: as_list(mats_raw.get(k)) for k in ("missing", "waived")}
    mats["present"] = [entry_name(x) for x in (present_raw or [])]
    if exam is None:
        for m in reversed(metas):
            if m.get("exam"):
                exam = parse_date(m["exam"])
                break

    # stages: default first non-probe attempt per item = cold, later = retry
    seen = set()
    for a in attempts:
        st = a.get("stage")
        if st not in ("cold", "retry", "probe", "review"):
            st = "retry" if a["item"] in seen else "cold"
        a["stage"] = st
        if st != "probe":
            seen.add(a["item"])

    cards = [a for a in attempts if a["stage"] != "probe"]          # "cards" = non-probe attempts
    # v1.6: lag in days for review attempts, from the line or from the dates of this and the item's previous attempt
    last_day = {}
    for a in attempts:
        d = event_date(a, sd)
        if a["stage"] == "review":
            if a.get("lag_days") is None and d and a["item"] in last_day and parse_date(d) and parse_date(last_day[a["item"]]):
                a["lag_days"] = (parse_date(d) - parse_date(last_day[a["item"]])).days
        if d and a["stage"] != "probe":
            last_day[a["item"]] = d
        a["conf"] = conf_of(a.get("confidence"))
    # lag in cards since the item's previous non-probe attempt
    last_pos = {}
    for pos, a in enumerate(cards):
        if a["item"] in last_pos:
            between = cards[last_pos[a["item"]] + 1: pos]
            a["lag"] = len({b["item"] for b in between if b["item"] != a["item"]})
        else:
            a["lag"] = None
        last_pos[a["item"]] = pos

    # per-item records and Leitner box
    items = collections.OrderedDict()
    for a in attempts:
        it = items.setdefault(a["item"], {"item": a["item"], "concept": a["concept"],
                                          "framework": a.get("framework"), "attempts": [], "box": 1,
                                          "sessions_correct": set(), "last_date": None})
        it["attempts"].append({k: a.get(k) for k in ("stage", "verdict", "score", "lag", "error", "format",
                                                     "presentation", "support", "feedback")})
        if a.get("framework"):
            it["framework"] = a["framework"]
        d = event_date(a, sd)
        if d:
            it["last_date"] = d
        if a["verdict"] == "correct" and a.get("session") is not None:
            it["sessions_correct"].add(a.get("session"))
    for name, it in items.items():
        box, first = 1, True
        for a in [x for x in attempts if x["item"] == name and x["stage"] != "probe"]:
            if first:
                box = 2 if a["score"] == 1.0 else 1
                first = False
            elif a["score"] == 1.0:
                box = min(5, box + 1)
            elif a["score"] == 0.0:
                box = 1
        it["box"] = box
        it["retired"] = len(it["sessions_correct"]) >= 2

    # concept mastery: recency-weighted, smoothed with a Beta(1,1) prior
    by_concept = collections.OrderedDict()
    for a in attempts:
        by_concept.setdefault(a["concept"], []).append(a)
    concepts = []
    for c, lst in by_concept.items():
        n = len(lst)
        w = [cfg["recency"] ** (n - 1 - i) for i in range(n)]
        sw = sum(w)
        s = sum(wi * x["score"] for wi, x in zip(w, lst))
        concepts.append({"concept": c, "framework": next((x.get("framework") for x in reversed(lst) if x.get("framework")), None),
                         "n": n, "mastery": round((1 + s) / (2 + sw), 3), "confidence": round(sw / (sw + 2), 3),
                         "last": lst[-1]["verdict"], "non_correct": sum(1 for x in lst if x["score"] < 1.0),
                         "ever_correct": any(x["score"] == 1.0 for x in lst)})
    concepts.sort(key=lambda r: r["mastery"])

    fw_mastery = collections.defaultdict(list)
    for c in concepts:
        if c["framework"]:
            fw_mastery[c["framework"]].append(c["mastery"])
    fw_mastery = {k: round(mean(v), 3) for k, v in fw_mastery.items()}
    fw_conf = collections.defaultdict(list)
    for c in concepts:
        if c["framework"]:
            fw_conf[c["framework"]].append(c["confidence"])
    fw_conf = {k: round(mean(v), 3) for k, v in fw_conf.items()}

    cold = [a for a in cards if a["stage"] == "cold"]
    retry = [a for a in cards if a["stage"] in ("retry", "review")]
    probes = [a for a in attempts if a["stage"] == "probe"]
    short = [a for a in retry if a["lag"] is not None and a["lag"] <= cfg["short_lag"]]
    long_ = [a for a in retry if a["lag"] is not None and a["lag"] > cfg["short_lag"]]

    def acc(lst):
        return mean(x["score"] for x in lst)

    errs = collections.Counter(e for a in attempts for e in a["error"])
    pairs = collections.Counter()
    for a in attempts:
        if "intrusion" in a["error"] and a.get("intruder"):
            pairs[(a.get("framework") or a["concept"], str(a["intruder"]))] += 1

    by_format = {}
    for a in attempts:
        if a.get("format"):
            by_format.setdefault(a["format"], []).append(a["score"])
    by_format = {k: {"n": len(v), "acc": round(mean(v), 3)} for k, v in by_format.items()}
    by_presentation = {}
    for a in cold:
        if a.get("presentation"):
            by_presentation.setdefault(a["presentation"], []).append(a["score"])
    by_presentation = {k: {"n": len(v), "acc": round(mean(v), 3)} for k, v in by_presentation.items()}
    by_feedback = {}
    for a in retry:
        if a.get("feedback"):
            by_feedback.setdefault(a["feedback"], []).append(a["score"])
    by_feedback = {k: {"n": len(v), "acc": round(mean(v), 3)} for k, v in by_feedback.items()}

    sig_counts = collections.Counter(s.get("kind", "?") for s in signals)
    misaddressed = sum(1 for a in attempts if a.get("open_item") and str(a["open_item"]) != a["item"])
    if misaddressed:
        problems.append("%d attempt(s) carry an open_item different from item: a reply to a card other than the open one "
                        "is a confusion signal, not an attempt (register section 1)" % misaddressed)

    by_grain = collections.Counter()
    for a in attempts:
        g = str(a.get("grain", "")).lower()
        if g in GRAINS and a["score"] < 1.0:
            by_grain[g] += 1

    # v1.6: retention by lag band, from review attempts
    reviews = [a for a in attempts if a["stage"] == "review" and a.get("lag_days") is not None]
    retention = []
    for label, lo, hi in LAG_BANDS:
        band = [a for a in reviews if lo <= int(a["lag_days"]) <= hi]
        retention.append({"band": label, "n": len(band), "acc": acc(band)})
    # v1.6: calibration, from the confidence word
    conf_rows = [a for a in attempts if a.get("conf") is not None and a["stage"] != "probe"]
    sure_wrong = [a for a in conf_rows if a["conf"] >= 0.5 and a["score"] < 1.0]
    unsure_right = [a for a in conf_rows if a["conf"] < 0.5 and a["score"] == 1.0]
    calibration = {"n": len(conf_rows), "sure_wrong": len(sure_wrong), "unsure_right": len(unsure_right),
                   "sure_wrong_items": sorted({a["item"] for a in sure_wrong})}
    # v1.6: cue-free probes apart from the rest
    cue_free = [a for a in probes if str(a.get("cue_free", "")).lower() in ("true", "1", "yes")]
    # v1.6: units, and which earlier unit is known
    units = collections.OrderedDict()
    for a in cards:
        u = str(a.get("unit") or "")
        if u:
            units.setdefault(u, set()).add(a["item"])
    unit_known = {}
    for u, names in units.items():
        top = [items[n] for n in names if n in items]
        unit_known[u] = bool(top) and sum(1 for it in top if it["box"] >= cfg["known_box"] or it["retired"]) >= max(1, (len(top) + 1) // 2)
    last_block_units = {str(a.get("unit") or "") for a in cards[-8:] if a.get("unit")}

    # study gate: every missing item must be waived; v1.6: and every present entry proven, unless the overlay says not
    unwaived = [m for m in mats["missing"] if m not in mats["waived"]]
    # the proof is asked of a log written under register 1.6 or later (the meta line names it), or always when the
    # overlay sets gate_proof true, or never when it sets it false; older logs are read as they were
    gp = cfg.get("gate_proof", "auto")
    enforce_proof = (gp is True) or (gp == "auto" and str(last_meta.get("register") or "") >= "1.6")
    proof_ok = (not enforce_proof) or not unproven
    if unproven and enforce_proof:
        problems.append("materials check: %d present entr%s carr%s no quoted line, so the opening is unproven: %s" % (
            len(unproven), "y" if len(unproven) == 1 else "ies", "ies" if len(unproven) == 1 else "y", ", ".join(unproven)))
    gate = {"mode": mode, "present": mats["present"], "missing": mats["missing"], "waived": mats["waived"],
            "unwaived": unwaived, "unproven": unproven if enforce_proof else [], "study_ok": (not unwaived) and proof_ok}
    # learn stall: current materials missing AND the previous unit is known
    prev_unit = last_meta.get("prev_unit")
    prev_items = [it for it in items.values() if prev_unit and any(
        (x.get("unit") == prev_unit) for x in [a for a in attempts if a["item"] == it["item"]])]
    known = [it for it in prev_items if it["box"] >= cfg["known_box"] or it["retired"]]
    gate["prev_unit"] = prev_unit
    gate["prev_known"] = "%d/%d" % (len(known), len(prev_items)) if prev_items else None
    gate["learn_stall"] = bool(unwaived) and bool(prev_items) and len(known) >= max(1, (len(prev_items) + 1) // 2)

    ledger = collections.OrderedDict()
    for c in claims:
        h = str(c.get("holder", "")).lower()
        if h not in HOLDERS:
            problems.append("seq %d: claim holder %r unknown; kept under 'model'" % (c["_seq"], c.get("holder")))
            h = "model"
        if h == "literature" and not c.get("source"):
            problems.append("seq %d: literature claim without a source" % c["_seq"])
        ledger.setdefault(str(c.get("concept", "?")), {k: [] for k in HOLDERS})[h].append(
            {"text": c.get("text", ""), "source": c.get("source"), "status": c.get("status", "open")})

    # longest run of same stage among cards
    run, best, prev = 0, 0, None
    for a in cards:
        run = run + 1 if a["stage"] == prev else 1
        prev = a["stage"]
        best = max(best, run)
    max_run = best

    stats = {
        "n_attempts": len(attempts), "n_cards": len(cards), "n_items": len(items), "n_signals": len(signals),
        "cold": {"n": len(cold), "acc": acc(cold), "last4": acc(cold[-4:]) if cold else None},
        "retry_short": {"n": len(short), "acc": acc(short)}, "retry_long": {"n": len(long_), "acc": acc(long_)},
        "probe": {"n": len(probes), "acc": acc(probes)},
        "errors": dict(errs), "intrusion_pairs": [{"target": k[0], "intruder": k[1], "count": v} for k, v in pairs.items()],
        "by_format": by_format, "by_presentation": by_presentation, "by_feedback": by_feedback,
        "signals": dict(sig_counts), "max_same_stage_run": max_run, "framework_mastery": fw_mastery,
        "framework_confidence": fw_conf, "by_grain": dict(by_grain), "mode": mode, "misaddressed": misaddressed,
        "retention": retention, "calibration": calibration,
        "probe_cue_free": {"n": len(cue_free), "acc": acc(cue_free)},
        "units": {u: {"n": len(v), "known": unit_known[u]} for u, v in units.items()}, "last_block_units": sorted(last_block_units),
    }
    return {"attempts": attempts, "signals": signals, "items": items, "concepts": concepts, "stats": stats,
            "problems": problems, "exam": exam, "sd": sd, "gate": gate, "ledger": ledger}


# ----------------------------------------------------------------------------- rules
def strength(n, cfg):
    if n >= cfg["min_n_act"]:
        return "act"
    if n >= cfg["min_n_tentative"]:
        return "tentative"
    return None


def evaluate_rules(R, cfg, off=()):
    S = R["stats"]
    out = []

    def add(rid, lever, strength_, evidence, action, n):
        if rid in off or strength_ is None:
            return
        out.append({"id": rid, "lever": lever, "strength": strength_, "n": n, "evidence": evidence, "action": action})

    # R1 speed / mode for NEW material
    n, a = S["cold"]["n"], S["cold"]["acc"]
    if a is not None:
        if a < cfg["cold_low"]:
            add("R1", "speed+presentation", strength(n, cfg),
                "cold (first-attempt) accuracy %s over %d new cards" % (pct(a), n),
                "NEW material in TEACH mode: put the source (slide/diagram) up, have the learner explain it back in "
                "their own words, then ask the recall card. 2-3 new cards per block. Keep RETRIES in RECITE mode (cold).", n)
        elif a >= cfg["cold_high"]:
            add("R1", "speed", strength(n, cfg),
                "cold accuracy %s over %d new cards" % (pct(a), n),
                "NEW material in RECITE mode (test first). Raise pace: bigger blocks, skip the probe after a clean "
                "correct, move to scenario / contrast formats.", n)
        if n >= 6 and S["cold"]["last4"] is not None and S["cold"]["last4"] - a >= 0.25:
            add("R1b", "speed", strength(n, cfg), "last-4 cold accuracy %s vs overall %s" % (pct(S["cold"]["last4"]), pct(a)),
                "Improving: re-test the mode after the next block before locking it in.", n)
    # R2 spacing / lag
    n, a = S["retry_short"]["n"], S["retry_short"]["acc"]
    if a is not None and a >= cfg["retry_ok"]:
        add("R2", "order+spacing", strength(n, cfg),
            "short-lag retries (<=%d cards) %s over %d" % (cfg["short_lag"], pct(a), n),
            "Short-lag success is easy and says little about retention. Set the next retry at 7+ cards or the block end; "
            "a short-lag correct does not count toward retirement (two correct sessions).", n)
    # R3 interleave
    if S["max_same_stage_run"] >= cfg["run_wall"]:
        add("R3", "order", strength(S["n_cards"], cfg) or ("tentative" if S["n_cards"] >= 2 else None),
            "longest run of the same stage = %d cards in a row" % S["max_same_stage_run"],
            "Interleave: alternate 1 new : 1 retry (retries 3+ cards after the miss). Avoid a wall of new cards or a wall of retries.",
            S["n_cards"])
    # R4 interference between frameworks
    sig_text = " ".join((str(s.get("concept", "")) + " " + str(s.get("note", ""))).lower()
                        for s in R["signals"] if s.get("kind") in ("integrate", "reconcile", "confusion"))
    for p in S["intrusion_pairs"]:
        corroborated = p["intruder"].lower() in sig_text or p["target"].lower() in sig_text
        st = "act" if p["count"] >= 2 else ("tentative" if corroborated else None)
        fm, fc = S["framework_mastery"], S["framework_confidence"]

        def ready(f):
            return fm.get(f, 0) >= cfg["contrast_mastery"] and fc.get(f, 0) >= cfg["contrast_conf"]

        def mc(f):
            return "%s %.2f/%.2f" % (f, fm.get(f, 0), fc.get(f, 0)) if f in fm else "%s n/a" % f
        gate = "ready now" if ready(p["target"]) and ready(p["intruder"]) else "not yet"
        add("R4", "order+presentation", st,
            "answers for %s drew on %s (%d time%s%s)" % (p["target"], p["intruder"], p["count"], "" if p["count"] == 1 else "s",
                                                           ", plus a learner question linking them" if corroborated else ""),
            "Name the framework in every stem for these two ('In the %s framework ...'). Queue ONE contrast card "
            "('%s or %s?') once both frameworks reach mastery >= %.2f with confidence >= %.2f (now %s, %s: %s). "
            "Never show the two diagrams as one." %
            (p["target"], p["target"], p["intruder"], cfg["contrast_mastery"], cfg["contrast_conf"], mc(p["target"]), mc(p["intruder"]), gate),
            p["count"])
    # R5 change representation
    stuck = [c for c in R["concepts"] if c["non_correct"] >= 2]
    if stuck:
        add("R5", "feedback", "act" if any(c["n"] >= 3 for c in stuck) else "tentative",
            "2+ non-correct attempts: " + ", ".join(c["concept"] for c in stuck),
            "Do not repeat the same explanation. Change representation: add the 'why' line, an outside definition, a contrast "
            "pair or a worked example, then re-test 3+ cards later.", len(stuck))
    # R6 unsupported additions
    n = S["errors"].get("extra_unsupported", 0)
    if n >= 2:
        add("R6", "feedback", "tentative" if n < 4 else "act", "%d answers added claims not in the source" % n,
            "Keep a one-line 'not in the source' flag on additions (sourced-or-silent), and ask 'where was that said?' "
            "before accepting an embellishment as correct.", n)
    # R7 learner-originated cards
    asked = [s for s in R["signals"] if s.get("kind") in CARD_SIGNALS]
    if asked:
        add("R7", "order+presentation", "act", "%d learner questions that mark integration gaps" % len(asked),
            "Answer first, then turn each question into a card scheduled 3+ cards later (see list). Questions are the "
            "learner telling you where the map has a hole.", len(asked))
    # R8 learner instructions
    # One R8 line per standing instruction, so no tutoring rule is buried in a run-on.
    # Notes starting "Build:" are notes about building the kit; they go under R8b and are not tutoring rules.
    ins = [s for s in R["signals"] if s.get("kind") == "instruction"]
    tutor_ins = [s for s in ins if not str(s.get("note", "")).lower().startswith("build:")]
    build_ins = [s for s in ins if str(s.get("note", "")).lower().startswith("build:")]
    for s in tutor_ins:
        add("R8", "presentation", "act", "standing instruction from the learner",
            "Persist as overlay settings and obey from the first card of the next session: " +
            str(s.get("note", "")), 1)
    if build_ins:
        add("R8b", "build", "note", "%d build note(s) (not tutoring rules)" % len(build_ins),
            "Do not act on these while tutoring; they are for building the kit: " +
            " | ".join(str(s.get("note", "")) for s in build_ins), len(build_ins))
    # R9 format
    fm = {k: v for k, v in S["by_format"].items() if v["n"] >= 3}
    if len(fm) >= 2:
        hi, lo = max(fm.items(), key=lambda kv: kv[1]["acc"]), min(fm.items(), key=lambda kv: kv[1]["acc"])
        if hi[1]["acc"] - lo[1]["acc"] >= cfg["format_gap"]:
            add("R9", "presentation", "tentative", "%s %s vs %s %s" % (hi[0], pct(hi[1]["acc"]), lo[0], pct(lo[1]["acc"])),
                "Open new topics with the %s format, then fade to %s." % (hi[0], lo[0]), hi[1]["n"] + lo[1]["n"])
    # R10 probes
    n, a = S["probe"]["n"], S["probe"]["acc"]
    if n >= cfg["min_n_tentative"] and a is not None:
        if a < cfg["probe_low"]:
            add("R10", "feedback", "tentative", "probe (transfer) accuracy %s over %d" % (pct(a), n),
                "Keep one probe after each clean correct on tier-1 cards: transfer is where the gaps show.", n)
        elif a >= cfg["probe_high"]:
            add("R10", "speed", "tentative", "probe accuracy %s over %d" % (pct(a), n),
                "Probe only tier-1 cards; skip probes elsewhere to raise pace.", n)
    # R11 learn mode: errors concentrate in one grain
    bg = S["by_grain"]
    tot = sum(bg.values())
    if tot >= cfg["min_n_tentative"]:
        g, c = max(bg.items(), key=lambda kv: kv[1])
        if c / tot >= cfg["grain_share"]:
            regroup = {"concept": "open each block with the 'what is it for' line, then the parts",
                       "component": "run term and structure cards as a set before any scenario",
                       "process": "run sequence and scenario cards together; ask for the order out loud"}[g]
            add("R11", "order+grouping", strength(tot, cfg), "%d of %d tagged errors at the %s grain" % (c, tot, g),
                "Regroup the next block by grain: " + regroup + ".", tot)
    # R12 gates
    G = R["gate"]
    if G["mode"] == "study" and G["unwaived"]:
        add("R12", "gate", "act", "study gate not met: missing without waiver: " + ", ".join(G["unwaived"]),
            "Do not start the block. Print the materials check and wait for a named waiver on each missing item.", len(G["unwaived"]))
    if G["mode"] == "study" and G.get("unproven"):
        add("R12b", "gate", "act", "present entries with no quoted line: " + ", ".join(G["unproven"]),
            "The gate is unproven. Print the materials check again with the first line of each file as read, or waive by name.", len(G["unproven"]))
    # R13 retention at a delay (v1.6)
    far = next((b for b in S["retention"] if b["band"] == "7+ days"), None)
    if far and far["acc"] is not None and far["n"] >= cfg["min_n_tentative"]:
        if far["acc"] < cfg["retention_low"]:
            add("R13", "spacing", strength(far["n"], cfg), "review accuracy at 7+ days %s over %d" % (pct(far["acc"]), far["n"]),
                "The day boxes are too long for this material: boxes 3 to 5 due in 1, 2, 4 days; review set five cards at open.", far["n"])
        elif far["acc"] >= cfg["retention_high"]:
            add("R13", "spacing", strength(far["n"], cfg), "review accuracy at 7+ days %s over %d" % (pct(far["acc"]), far["n"]),
                "Retention holds at a week: boxes 3 to 5 due in 2, 5, 10 days; review set three cards at open.", far["n"])
    # R14 across units (v1.6)
    known_units = [u for u, v in S["units"].items() if v["known"]]
    if len(S["units"]) >= 2 and known_units:
        missing_from_block = [u for u in known_units if u not in S["last_block_units"]]
        if missing_from_block:
            add("R14", "order", "act", "earlier unit(s) known and absent from the last block: " + ", ".join(missing_from_block),
                "Draw about %d%% of the next block from %s, mixed in; name the framework in any stem the log has shown confusable across units."
                % (round(100 * cfg["cross_unit_share"]), ", ".join(missing_from_block)), len(missing_from_block))
    # R15 calibration (v1.6)
    C = S["calibration"]
    if C["sure_wrong"]:
        add("R15", "order+presentation", "act", "%d card(s) answered sure and wrong: %s" % (C["sure_wrong"], ", ".join(C["sure_wrong_items"])),
            "Re-ask each at 7+ cards in contrast format." + (" Open the next block with one line: say unsure when unsure; a wrong sure costs more than a blank." if C["sure_wrong"] >= 2 else ""),
            C["sure_wrong"])
    if G["mode"] == "learn" and G["learn_stall"]:
        add("R12", "gate", "act", "learn stall: missing %s; previous unit %s known %s" % (", ".join(G["unwaived"]), G["prev_unit"], G["prev_known"]),
            "Refuse to continue. List the materials needed by name and wait. Accept an override only with a reason restatable as valid in two lines; log it.", len(G["unwaived"]))
    return out


# ----------------------------------------------------------------------------- queue
INTERVALS = {3: 1, 4: 3, 5: 7}


def build_queue(R, today, exam):
    q = []
    for name, it in R["items"].items():
        if it["retired"] or all(a["stage"] == "probe" for a in it["attempts"]):
            continue
        last = parse_date(it["last_date"]) if it["last_date"] else today
        last = last or today
        box = it["box"]
        mastery = next((c["mastery"] for c in R["concepts"] if c["concept"] == it["concept"]), None)
        if box == 1:
            due, when = last, "in session: re-ask 3+ cards after the last miss"
        elif box == 2:
            due, when = last, "end-of-session pass"
        else:
            due = last + dt.timedelta(days=INTERVALS[box])
            when = "on or after %s" % due.isoformat()
        if exam and box >= 3:
            final = exam - dt.timedelta(days=1)
            if due > final >= last:
                due, when = final, "day before the exam (%s)" % final.isoformat()
        q.append({"item": name, "concept": it["concept"], "box": box, "due": due.isoformat(), "when": when,
                  "mastery": mastery, "due_now": due <= today})
    q.sort(key=lambda r: (r["due"], r["mastery"] if r["mastery"] is not None else 1))
    # v1.6: the final pass. Within final_days of the exam, retired cards come back, oldest last attempt first.
    if exam is not None and 0 <= (exam - today).days <= INTERVALS_FINAL_DAYS():
        days_left = (exam - today).days
        retired = [it for it in R["items"].values() if it["retired"]]
        retired.sort(key=lambda it: it["last_date"] or "")
        for it in retired[: 3 + 2 * days_left]:
            q.append({"item": it["item"], "concept": it["concept"], "box": it["box"], "due": today.isoformat(),
                      "when": "final pass: retired, last attempt %s" % (it["last_date"] or "unknown"),
                      "mastery": next((c["mastery"] for c in R["concepts"] if c["concept"] == it["concept"]), None),
                      "due_now": True, "final": True})
    return q


def INTERVALS_FINAL_DAYS():
    return DEFAULTS["final_days"]


# ----------------------------------------------------------------------------- brief
def mode_line(rules):
    for r in rules:
        if r["id"] == "R1":
            if "TEACH" in r["action"]:
                return "TEACH for new material, RECITE for retries", r
            return "RECITE (test-first) for new material", r
    return "RECITE (default; not enough data to change it)", None


def render_brief(R, rules, queue, overlay, cfg, today):
    S = R["stats"]
    mode, rr = mode_line(rules)
    L = []
    L.append("# Adaptive brief")
    lead = None
    for r in rules:
        if r["id"] in ("R12", "R12b"):
            lead = "Block does not start: " + r["evidence"] + "."
            break
    if lead is None and rr:
        lead = "Next block: %s (%s, %s)." % (mode, rr["evidence"], rr["strength"])
    if lead is None:
        due = [q for q in queue if q["due_now"]]
        if S["n_cards"] < 4:
            lead = "Next block: defaults (%d data points, below the 4 needed to adapt); %d card(s) due now." % (S["n_cards"], len(due))
        else:
            lead = "Next block: defaults (cold accuracy between the thresholds over %d cards, so the mode stands); %d card(s) due now." % (S["n_cards"], len(due))
    L.append("")
    L.append("**" + lead + "**")
    meta = [m for m in R.get("metas", [])]
    L.append("")
    L.append("Generated %s by analyze.py v%s. Data: %d attempts on %d items (%d cards, %d probes), %d learner signals." %
             (today.isoformat(), VERSION, S["n_attempts"], S["n_items"], S["n_cards"], S["probe"]["n"], S["n_signals"]))
    if R["exam"]:
        L.append("Exam: %s (%d days)." % (R["exam"].isoformat(), (R["exam"] - today).days))
    reg = overlay.get("register") if overlay else None
    if reg:
        L.append("")
        L.append("Register in force (from overlay):")
        for k, v in reg.items():
            L.append("- %s: %s" % (k, v))
    G = R["gate"]
    if G["mode"] or G["missing"] or G["present"]:
        L.append("")
        if G["mode"] == "study":
            L.append("## Gate %s (study)" % ("met" if G["study_ok"] else "NOT met"))
        elif G["mode"] == "learn":
            L.append("## Learn: %s" % ("stall" if G["learn_stall"] else "materials check"))
        else:
            L.append("## Mode: %s" % (G["mode"] or "unset"))
        L.append("- mode: %s" % (G["mode"] or "unset"))
        L.append("- present: %s" % (", ".join(G["present"]) or "none listed"))
        L.append("- missing: %s" % (", ".join(G["missing"]) or "none"))
        L.append("- waived: %s" % (", ".join(G["waived"]) or "none"))
        if G["mode"] == "study":
            holds = S["signals"].get("gate_hold", 0)
            L.append("- study gate: %s%s" % ("met" if G["study_ok"] else "NOT met (unwaived: %s)" % ", ".join(G["unwaived"]),
                                              "; gate_hold logged %d time(s)" % holds if holds else ""))
        if G["mode"] == "learn":
            L.append("- learn stall: %s%s" % ("YES" if G["learn_stall"] else "no",
                     " (previous unit %s known %s)" % (G["prev_unit"], G["prev_known"]) if G["prev_unit"] else ""))
    L.append("")
    L.append("## Settings for the next block")
    L.append("- **Mode:** %s%s" % (mode, " (%s; %s)" % (rr["evidence"], rr["strength"]) if rr else ""))
    for r in rules:
        if r["id"] in ("R12",):
            L.append("- **GATE [R12]:** %s Evidence: %s." % (r["action"], r["evidence"]))
    for r in rules:
        if r["id"] in ("R12b",):
            L.append("- **GATE [R12b]:** %s Evidence: %s." % (r["action"], r["evidence"]))
    for r in rules:
        if r["id"] in ("R2", "R3", "R4", "R5", "R6", "R9", "R10", "R11", "R13", "R14", "R15"):
            L.append("- **%s [%s, %s]:** %s Evidence: %s." % (r["lever"].split("+")[0].title(), r["id"], r["strength"], r["action"], r["evidence"]))
    for r in rules:
        if r["id"] == "R8":
            L.append("- **Presentation [R8]:** %s" % r["action"])
    for r in rules:
        if r["id"] == "R8b":
            L.append("- **Build notes [R8b, not tutoring rules]:** %s" % r["action"])
    L.append("")
    L.append("## Snapshot")
    L.append("| measure | value | n |")
    L.append("|---|---|---|")
    L.append("| cold (first-attempt) accuracy | %s | %d |" % (pct(S["cold"]["acc"]), S["cold"]["n"]))
    L.append("| retry accuracy, short lag (<=%d cards) | %s | %d |" % (cfg["short_lag"], pct(S["retry_short"]["acc"]), S["retry_short"]["n"]))
    L.append("| retry accuracy, long lag | %s | %d |" % (pct(S["retry_long"]["acc"]), S["retry_long"]["n"]))
    L.append("| probe (transfer) accuracy | %s | %d |" % (pct(S["probe"]["acc"]), S["probe"]["n"]))
    L.append("| longest same-stage run | %d cards | |" % S["max_same_stage_run"])
    for b in S["retention"]:
        if b["n"]:
            L.append("| review accuracy, %s | %s | %d |" % (b["band"], pct(b["acc"]), b["n"]))
    if S["probe_cue_free"]["n"]:
        L.append("| probe accuracy, cue-free stems | %s | %d |" % (pct(S["probe_cue_free"]["acc"]), S["probe_cue_free"]["n"]))
    if S["calibration"]["n"]:
        L.append("| calibration: sure-and-wrong / unsure-and-right | %d / %d | %d |" % (S["calibration"]["sure_wrong"], S["calibration"]["unsure_right"], S["calibration"]["n"]))
    if S["errors"]:
        L.append("| error mix | %s | |" % ", ".join("%s %d" % kv for kv in sorted(S["errors"].items(), key=lambda kv: -kv[1])))
    if S["by_grain"]:
        L.append("| errors by grain (learn) | %s | |" % ", ".join("%s %d" % kv for kv in sorted(S["by_grain"].items(), key=lambda kv: -kv[1])))
    if S["signals"]:
        L.append("| learner signals | %s | |" % ", ".join("%s %d" % kv for kv in sorted(S["signals"].items(), key=lambda kv: -kv[1])))
    L.append("")
    L.append("## Concepts, weakest first (mastery 0-1, confidence 0-1)")
    L.append("| concept | framework | mastery | confidence | attempts | last |")
    L.append("|---|---|---|---|---|---|")
    for c in R["concepts"]:
        L.append("| %s | %s | %.2f | %.2f | %d | %s |" % (c["concept"], c["framework"] or "", c["mastery"], c["confidence"], c["n"], c["last"]))
    if S["intrusion_pairs"]:
        L.append("")
        L.append("## Confusion pairs")
        for p in S["intrusion_pairs"]:
            L.append("- %s answered with %s (%d)" % (p["target"], p["intruder"], p["count"]))
    asked = [s for s in R["signals"] if s.get("kind") in CARD_SIGNALS]
    if asked:
        L.append("")
        L.append("## Learner-originated cards to add (one per question)")
        for s in asked:
            L.append("- [%s] %s: %s" % (s.get("kind"), s.get("concept", ""), s.get("note", "")))
    if R["ledger"]:
        L.append("")
        L.append("## Ledger (ponder): three understandings, kept apart")
        L.append("| concept | learner says | literature says (source) | model's present view |")
        L.append("|---|---|---|---|")
        def cell(lst):
            if not lst:
                return ""
            return " / ".join(("[%s] " % x["status"] if x["status"] != "open" else "") + x["text"] +
                              (" (%s)" % x["source"] if x.get("source") else "") for x in lst)
        for c, h in R["ledger"].items():
            L.append("| %s | %s | %s | %s |" % (c, cell(h["learner"]), cell(h["literature"]), cell(h["model"])))
        L.append("")
        L.append("Disputed first when re-opening. A column changes only when its holder's position changes.")
    audit = R.get("audit") or []
    if audit:
        L.append("")
        L.append("## Verdict audit (the terms would have scored these differently; verdicts are not changed)")
        for a in audit:
            L.append("- %s: logged %s; terms say %s (%s)" % (a["item"], a["verdict"], a["terms_say"], a["why"]))
    L.append("")
    L.append("## Queue")
    if not queue:
        L.append("- nothing queued")
    for r in queue:
        L.append("- %s (%s) box %d: %s%s" % (r["item"], r["concept"], r["box"], r["when"], "  <- due now" if r["due_now"] else ""))
    L.append("")
    L.append("## Confidence and what would change this")
    n = S["n_cards"]
    L.append("- Sample: %d cards. Rules fire as 'tentative' from %d data points and 'act' from %d. Treat tentative rules as "
             "experiments: apply them for one block, log, re-run." % (n, cfg["min_n_tentative"], cfg["min_n_act"]))
    L.append("- To test the mode rule, log `presentation` on every new card (question_first vs source_first) and compare cold accuracy next run.")
    if not S["calibration"]["n"]:
        L.append("- No confidence words in this log: the learner may end an answer with sure or unsure (register section 1).")
    if not any(b["n"] for b in S["retention"]):
        L.append("- No review attempts in this log: from the second session on, open with three to five earlier cards logged `stage: review` (register section 1).")
    if R["problems"]:
        L.append("")
        L.append("## Log problems")
        for p in R["problems"][:10]:
            L.append("- " + p)
    return "\n".join(L) + "\n"


# ----------------------------------------------------------------------------- commands
def audit_verdicts(attempts, terms):
    """Each logged answer against its card's must / must_not terms. Lists disagreements; changes nothing."""
    out = []
    for a in attempts:
        t = terms.get(a["item"]) if isinstance(terms, dict) else None
        ans = str(a.get("answer") or "")
        if not t or not ans.strip() or a["stage"] == "probe":
            continue
        low = ans.lower()
        must = [m for m in as_list(t.get("must")) if m]
        must_not = [m for m in as_list(t.get("must_not")) if m]
        missing = [m for m in must if m.lower() not in low]
        present_bad = [m for m in must_not if m.lower() in low]
        if must and not missing and not present_bad:
            say = "correct"
        elif present_bad or (must and len(missing) == len(must)):
            say = "wrong"
        else:
            say = "partial"
        if say != a["verdict"]:
            why = []
            if missing: why.append("missing: " + ", ".join(missing))
            if present_bad: why.append("carries: " + ", ".join(present_bad))
            if not why: why.append("every must term present, none of the must_not")
            out.append({"item": a["item"], "verdict": a["verdict"], "terms_say": say, "why": "; ".join(why)})
    return out


def cmd_analyze(args):
    cfg = dict(DEFAULTS)
    overlay = {}
    if args.overlay:
        with open(args.overlay, encoding="utf-8") as f:
            overlay = json.load(f)
        cfg.update(overlay.get("thresholds", {}))
    events, problems = load(args.log)
    today = parse_date(args.today) if args.today else None
    if today is None:
        sd = session_dates(events)
        today = parse_date(max(sd.values())) if sd else dt.date.today()
    exam = parse_date(args.exam) if args.exam else None
    R = build(events, cfg, today, exam)
    R["problems"] = problems + R["problems"]
    if getattr(args, "terms", None):
        with open(args.terms, encoding="utf-8") as f:
            R["audit"] = audit_verdicts(R["attempts"], json.load(f))
    rules = evaluate_rules(R, cfg, overlay.get("rules_off", []))
    queue = build_queue(R, today, R["exam"])
    brief = render_brief(R, rules, queue, overlay, cfg, today)
    out = args.out or os.path.dirname(os.path.abspath(args.log))
    os.makedirs(out, exist_ok=True)
    profile = {"version": VERSION, "generated": today.isoformat(), "stats": R["stats"], "concepts": R["concepts"],
               "rules": rules, "queue": queue, "gate": R["gate"], "ledger": R["ledger"], "overlay_applied": bool(overlay), "problems": R["problems"],
               "audit": R.get("audit", [])}
    with open(os.path.join(out, "profile.json"), "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)
    with open(os.path.join(out, "brief.md"), "w", encoding="utf-8") as f:
        f.write(brief)
    sys.stdout.write(brief)
    return 0


def cmd_add(args):
    obj = {}
    for kv in args.fields:
        if "=" not in kv:
            sys.exit("add: expected key=value, got %r" % kv)
        k, v = kv.split("=", 1)
        if k in ("error",):
            v = as_list(v)
        elif k in ("latency_s", "confidence"):
            v = float(v)
        obj[k] = v
    if "type" not in obj:
        obj["type"] = "attempt" if "verdict" in obj else ("signal" if "kind" in obj else "meta")
    if "session" not in obj and os.path.exists(args.log):
        events, _ = load(args.log)
        for e in reversed(events):
            if e.get("session"):
                obj["session"] = e["session"]
                break
    obj.setdefault("t", dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat())
    with open(args.log, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    print(json.dumps(obj, ensure_ascii=False))
    return 0


def cmd_selftest(_):
    def A(item, concept, verdict, stage=None, **kw):
        d = {"type": "attempt", "session": "s1", "item": item, "concept": concept, "verdict": verdict}
        if stage:
            d["stage"] = stage
        d.update(kw)
        return d

    cfg = dict(DEFAULTS)
    # 1. four cold misses, then four retries at lag 3 (the shape of a first session)
    log = [{"type": "meta", "session": "s1", "date": "2026-10-10", "exam": "2026-10-14"}]
    for i, v in enumerate(["none", "wrong", "none", "wrong"]):
        log.append(A("c%d" % i, "k%d" % i, v, "cold", framework="F%d" % (i % 2),
                     **({"error": ["intrusion"], "intruder": "F0"} if (i == 3) else {})))
    for i, v in enumerate(["partial", "correct", "correct", "correct"]):
        log.append(A("c%d" % i, "k%d" % i, v, "retry"))
    log.append({"type": "signal", "session": "s1", "kind": "integrate", "concept": "F0 and F1", "note": "asked how they relate"})
    R = build(log, cfg, dt.date(2026, 10, 10), None)
    S = R["stats"]
    assert S["cold"]["n"] == 4 and S["cold"]["acc"] == 0.0, S["cold"]
    assert S["retry_short"]["n"] == 4 and abs(S["retry_short"]["acc"] - 0.875) < 1e-9, S["retry_short"]
    lags = [a["lag"] for a in R["attempts"] if a["stage"] == "retry"]
    assert lags == [3, 3, 3, 3], lags
    # lag counts distinct other items: c0, c0, c0 echoes between c1 attempts give lag 1, not 3
    le = build([A("c1", "k1", "wrong", "cold"), A("c0", "k0", "wrong"), A("c0", "k0", "wrong"), A("c0", "k0", "wrong"),
                A("c1", "k1", "correct", "retry")], cfg)
    assert [x["lag"] for x in le["attempts"] if x["item"] == "c1"] == [None, 1], le["attempts"]
    mi = build([A("c1", "k1", "wrong", "cold", open_item="c2")], cfg)
    assert mi["stats"]["misaddressed"] == 1 and any("open_item" in p_ for p_ in mi["problems"])
    rules = {r["id"]: r for r in evaluate_rules(R, cfg)}
    assert rules["R1"]["strength"] == "tentative" and "TEACH" in rules["R1"]["action"], rules.get("R1")
    assert "R2" in rules and "R3" in rules and "R7" in rules
    assert rules["R4"]["strength"] == "tentative"          # one intrusion, corroborated by the learner's question
    q = {r["item"]: r for r in build_queue(R, dt.date(2026, 10, 10), dt.date(2026, 10, 14))}
    assert q["c0"]["box"] == 1 and q["c1"]["box"] == 2, q
    assert "not yet" in rules["R4"]["action"], rules["R4"]["action"]      # contrast card gated on mastery + confidence
    assert build_queue(build([A("p", "p", "correct", "probe")], cfg), dt.date(2026, 10, 10), None) == []
    # 2. too little data: no rule fires
    R2 = build([A("x", "x", "none", "cold"), A("y", "y", "wrong", "cold")], cfg)
    assert evaluate_rules(R2, cfg) == [] or all(r["id"] not in ("R1", "R2") for r in evaluate_rules(R2, cfg))
    # 3. strong cold performance -> test-first and faster
    log3 = [A("a%d" % i, "a%d" % i, "correct", "cold") for i in range(5)]
    r3 = {r["id"]: r for r in evaluate_rules(build(log3, cfg), cfg)}
    assert "TEACH" not in r3["R1"]["action"] and "pace" in r3["R1"]["action"], r3["R1"]
    # 4. add/load round trip
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "log.jsonl")
        ns = argparse.Namespace(log=p, fields=["item=z1", "concept=zed", "verdict=partly", "error=omission,wording"])
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            cmd_add(ns)
        ev, pr = load(p)
        a = norm_attempt(ev[0], [])
        assert a["verdict"] == "partial" and a["error"] == ["omission", "wording"], a
        with contextlib.redirect_stdout(io.StringIO()):
            assert cmd_analyze(argparse.Namespace(log=p, out=td, overlay=None, today="2026-10-10", exam=None)) == 0
        assert os.path.exists(os.path.join(td, "brief.md")) and os.path.exists(os.path.join(td, "profile.json"))
    # 5. gates and ledger
    g1 = build([{"type": "meta", "session": "s1", "date": "2026-10-10", "mode": "study",
                 "materials": {"present": ["dates"], "missing": ["L6 recording"], "waived": []}}], cfg)
    assert any(r["id"] == "R12" for r in evaluate_rules(g1, cfg))
    g2 = build([{"type": "meta", "session": "s1", "date": "2026-10-10", "mode": "study",
                 "materials": {"present": ["dates"], "missing": ["L6 recording"], "waived": ["L6 recording"]}}], cfg)
    assert not any(r["id"] == "R12" for r in evaluate_rules(g2, cfg))
    lg = [{"type": "meta", "session": "s1", "date": "2026-10-10", "mode": "learn", "prev_unit": "U1",
           "materials": {"missing": ["U2 slides"]}}]
    lg += [A("u%d" % i, "u%d" % i, "correct", "cold", unit="U1") for i in range(3)]
    lg += [A("u%d" % i, "u%d" % i, "correct", "retry", unit="U1") for i in range(3)]
    g3 = build(lg, cfg)
    assert g3["gate"]["learn_stall"], g3["gate"]
    assert any(r["id"] == "R12" and "stall" in r["evidence"] for r in evaluate_rules(g3, cfg))
    gr = [A("g%d" % i, "g%d" % i, "wrong", "cold", grain="process") for i in range(4)] + [A("g9", "g9", "wrong", "cold", grain="concept")]
    assert any(r["id"] == "R11" and "process" in r["evidence"] for r in evaluate_rules(build(gr, cfg), cfg))
    pl = [{"type": "claim", "session": "s1", "holder": "learner", "concept": "X", "text": "a"},
          {"type": "claim", "session": "s1", "holder": "literature", "concept": "X", "text": "b", "source": "slide 1"},
          {"type": "claim", "session": "s1", "holder": "model", "concept": "X", "text": "c", "status": "disputed"}]
    Rp = build(pl, cfg)
    assert set(Rp["ledger"]["X"]) == set(HOLDERS) and len(Rp["ledger"]["X"]["literature"]) == 1
    assert "## Ledger" in render_brief(Rp, [], [], {}, cfg, dt.date(2026, 10, 10))
    dg = build([A("d", "d", "correct", "cold", error=["extra_unsupported"])], cfg)
    assert dg["attempts"][0]["verdict"] == "partial" and dg["attempts"][0]["score"] == 0.5
    ms = build([{"type": "meta", "session": "s1", "mode": "study", "materials": {"missing": ["x"]}},
                {"type": "meta", "session": "s1", "mode": "ponder"}], cfg)
    assert ms["gate"]["mode"] == "study" and not ms["gate"]["study_ok"], ms["gate"]
    # v1.6: review at a delay, calibration, the final pass, the gate's proof, the verdict audit
    ev6 = [{"type": "meta", "session": "s1", "date": "2026-10-01", "mode": "study", "register": "1.6", "exam": "2026-10-20",
            "materials": {"present": ["dates (\"Midterm 2026-10-20\")", "slides"], "missing": [], "waived": []}},
           A("L5-01", "coart", "correct", confidence="sure", answer="two sounds overlapping"),
           A("L5-02", "trace", "partial", confidence="sure", answer="rhythm"),
           A("L5-03", "ganong", "correct", confidence="unsure"),
           {"type": "meta", "session": "s2", "date": "2026-10-10", "mode": "study", "register": "1.6", "exam": "2026-10-20",
            "materials": {"present": ["dates (\"Midterm 2026-10-20\")", "slides"], "missing": [], "waived": []}},
           A("L5-01", "coart", "correct", stage="review"), A("L5-02", "trace", "wrong", stage="review"),
           A("L5-03", "ganong", "correct", stage="review"), A("L5-04", "mcgurk", "correct", stage="review", lag_days=9)]
    for i, e in enumerate(ev6):
        e["session"] = "s2" if i >= 4 else "s1"; e["_seq"] = i
    g6 = build([dict(e) for e in ev6], cfg, dt.date(2026, 10, 15), None)
    far = next(b for b in g6["stats"]["retention"] if b["band"] == "7+ days")
    assert far["n"] == 4 and abs(far["acc"] - 0.75) < 1e-9, far
    assert g6["stats"]["calibration"]["sure_wrong"] == 1 and g6["stats"]["calibration"]["unsure_right"] == 1
    r6 = evaluate_rules(g6, cfg)
    assert any(r["id"] == "R15" for r in r6), "R15 on the sure-and-wrong card"
    assert any(r["id"] == "R12b" for r in r6) and not g6["gate"]["study_ok"], "the last meta's unquoted 'slides' holds the gate under 1.6"
    assert not any(r["id"] == "R12" for r in r6), "nothing is missing, so R12 is silent"
    q6 = build_queue(g6, dt.date(2026, 10, 15), dt.date(2026, 10, 20))
    assert any(x.get("final") for x in q6), "the final pass lists retired cards within final_days"
    au = audit_verdicts(g6["attempts"], {"L5-01": {"must": ["sounds"]}, "L5-02": {"must": ["word", "phoneme", "feature"], "must_not": ["prosody"]}})
    assert not any(a["item"] == "L5-01" for a in au) and any(a["item"] == "L5-02" and a["terms_say"] == "wrong" for a in au), au
    # an old log, no register field: the gate is not asked for proof
    old = build([dict(e, register=None) for e in ev6[:4]], cfg, dt.date(2026, 10, 15), None)
    assert old["gate"]["study_ok"], "a log without a register version is read as before"
    print("selftest: all checks passed")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Adaptive Recitation Kit analyzer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("log")
    a.add_argument("--out")
    a.add_argument("--overlay")
    a.add_argument("--today")
    a.add_argument("--exam")
    a.add_argument("--terms", help="JSON of must / must_not terms per card id, for the verdict audit")
    a.set_defaults(fn=cmd_analyze)
    d = sub.add_parser("add")
    d.add_argument("log")
    d.add_argument("fields", nargs="+")
    d.set_defaults(fn=cmd_add)
    s = sub.add_parser("selftest")
    s.set_defaults(fn=cmd_selftest)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
