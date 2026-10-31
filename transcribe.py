#!/usr/bin/env python3
"""transcribe.py - the session log written by machinery, from the session's own transcript.

The register asks the tutor to log every event itself. This takes that job away: it reads the
Claude Code transcript of a session and walks it as a state machine over the register's fixed
turn shapes, writing session_log.jsonl in the section 4 schema. The model tutors in the form;
the form is transcribed. A turn that fits no shape is not guessed at: it is skipped and named in
a `#` note line, so a lapse is visible rather than silent.

Shapes read (REGISTER section 1):
  tutor   "Card U1-04 ..." in the last paragraph           opens a card (stage cold, retry, or review if the paragraph says so)
  tutor   "Probe ..." in the last paragraph                 opens a probe on the last card
  tutor   first line begins correct | partly | no          closes the open card as an attempt; error tag and grain from the first lines
  tutor   "[integrate]" etc. anywhere                       the kind of the learner question just answered (one word from the signal list)
  tutor   present: / missing: / waived:                     the materials check, into the meta line
  tutor   "parked"                                          the open card is parked
  learner  log | end | study | learn | ponder                session commands; a mode word after the first is a mode signal
  learner  anything while a card is open                    an attempt if a verdict follows, else a signal
  learner  "... sure" / "... unsure" as the last word        confidence

    python3 transcribe.py TRANSCRIPT.jsonl LOG.jsonl [--manifest manifest.md] [--dry]
    python3 transcribe.py --find CWD --since EPOCH          print the newest transcript for that folder, or nothing

Python 3.8+, standard library only. Writes each event once: a transcript already in the log (by
its session id on the meta line) is not written again; a re-run is harmless.
"""
import argparse, datetime as dt, json, os, re, sys

VERDICTS = {"correct": "correct", "partly": "partial", "partial": "partial", "no": "wrong", "wrong": "wrong"}
ERRORS = ["no_answer", "intrusion", "misframe", "omission", "extra_unsupported", "wording", "format"]
SIGNALS = ["instruction", "reconcile", "source_request", "meaning_request", "example_request", "outside_definition",
           "integrate", "insight", "confusion", "locate", "parked", "pace_fast", "pace_slow", "waiver", "override",
           "override_request", "stall", "gate_hold", "mode"]
GRAINS = ("concept", "component", "process")
MODES = ("study", "learn", "ponder")
CARD = re.compile(r"\bCard\s+([A-Z]+\d+-\d+)\b")
ITEM = re.compile(r"\b([A-Z]+\d+-\d+)\b")
BRACKET = re.compile(r"\[(" + "|".join(SIGNALS) + r")\]")
SYSREM = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


def strip_md(line):
    return re.sub(r"^[\s>*#_`]+|[*_`]+$", "", line).strip()


def turns_of(path):
    """(role, text, timestamp, sessionId) per turn; consecutive assistant entries are one turn."""
    out, sid = [], None
    for raw in open(path, encoding="utf-8"):
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        role = e.get("type")
        if role not in ("user", "assistant"):
            continue
        sid = sid or e.get("sessionId")
        c = (e.get("message") or {}).get("content")
        if isinstance(c, str):
            text = c
        elif isinstance(c, list):
            if any(b.get("type") == "tool_result" for b in c if isinstance(b, dict)):
                continue
            text = "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
        else:
            continue
        text = SYSREM.sub("", text).strip()
        if not text:
            continue
        if role == "user" and ("You are harnessed to one directive" in text or text.startswith("This session is being continued")):
            continue
        if out and out[-1][0] == role == "assistant":
            out[-1] = (role, out[-1][1] + "\n\n" + text, out[-1][2], sid)
        else:
            out.append((role, text, e.get("timestamp", ""), sid))
    return out, sid


def local_date(ts):
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date().isoformat()
    except Exception:
        return dt.date.today().isoformat()


def local_time(ts):
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%H.%M")
    except Exception:
        return "?"


def manifest_fields(path):
    d = {}
    if path and os.path.isfile(path):
        for line in open(path):
            m = re.match(r"- (\w+): (.*)", line.strip())
            if m:
                d[m.group(1)] = m.group(2)
    return d


def existing(log):
    """(lines, session ids seen, transcript ids seen, items attempted before)"""
    lines, sessions, transcripts, items = set(), set(), set(), set()
    if os.path.isfile(log):
        for raw in open(log, encoding="utf-8"):
            line = raw.rstrip("\n")
            lines.add(line)
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("session"):
                sessions.add(e["session"])
            if e.get("type") == "meta" and e.get("transcript"):
                transcripts.add(e["transcript"])
            if e.get("type") == "attempt" and e.get("stage") != "probe" and e.get("item"):
                items.add(e["item"])
    return lines, sessions, transcripts, items


def transcribe(turns, sid, manifest, session, seen_items):
    """Walk the turns; return (events, notes). Events are dicts; notes are '# ...' strings."""
    ev, notes = [], []
    mode = manifest.get("mode") or None
    materials = None
    open_card, last_card, stage_next = None, None, None
    pending_learner = None          # (text, ts) of the learner turn awaiting the tutor's reading
    date = local_date(turns[0][2]) if turns else dt.date.today().isoformat()
    attempted = set(seen_items)
    mode_said = False               # the first mode word opens the session; a later one is a switch
    after_command = False           # the tutor's answer to log or end is not a turn to read

    def meta():
        m = {"type": "meta", "session": session, "date": date, "register": "1.6", "mode": mode or "study",
             "course": manifest.get("course", ""), "unit": manifest.get("units", ""), "transcript": sid or ""}
        if manifest.get("exam") and manifest["exam"] != "none":
            m["exam"] = manifest["exam"]
        if materials:
            m["materials"] = materials
        return m

    for role, text, ts, _ in turns:
        if role == "user":
            low = text.strip().lower()
            first = low.split()[0] if low.split() else ""
            if low in ("log", "end"):
                pending_learner, after_command = None, True
                continue
            if first in MODES and len(low.split()) <= 3:
                if mode_said:
                    ev.append({"type": "signal", "session": session, "kind": "mode", "note": first})
                mode, mode_said = first, True
                pending_learner = None
                continue
            pending_learner = (text, ts)
            continue

        # tutor turn
        if after_command:
            after_command = False
            continue
        lines = [strip_md(l) for l in text.splitlines() if l.strip()]
        head = lines[0].lower() if lines else ""
        paras = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
        last = paras[-1] if paras else text
        read = False

        # the materials check, once
        if materials is None and any(l.lower().startswith("present:") for l in lines):
            materials = {"present": [], "missing": [], "waived": []}
            for l in lines:
                for key in materials:
                    if l.lower().startswith(key + ":"):
                        body = l.split(":", 1)[1].strip()
                        materials[key] = [] if body.lower() in ("none", "none yet", "") else [x.strip() for x in re.split(r"\s·\s|;", body) if x.strip()]
            read = True

        # a verdict closes the open card
        m = re.match(r"^(correct|partly|partial|no|wrong)\b", head)
        if m and open_card and pending_learner:
            answer, ats = pending_learner
            verdict = VERDICTS[m.group(1)]
            if re.match(r"^\s*unk\b", answer, re.I) or not answer.strip():
                verdict = "none"
            errs = [e for e in ERRORS if re.search(r"\b" + e + r"\b", " ".join(lines[:3]).lower())]
            if verdict == "none" and "no_answer" not in errs:
                errs.append("no_answer")
            a = {"type": "attempt", "session": session, "item": open_card, "stage": stage_next or ("retry" if open_card in attempted else "cold"),
                 "verdict": verdict, "answer": answer.strip()[:300]}
            if a["stage"] == "cold":
                a["presentation"] = "question_first"
            if errs and verdict != "correct":
                a["error"] = errs
            g = [g for g in GRAINS if re.search(r"\b" + g + r"\b", " ".join(lines[:3]).lower())]
            if g and mode == "learn":
                a["grain"] = g[0]
            lw = answer.strip().split()[-1].strip(".,!").lower() if answer.strip() else ""
            if lw in ("sure", "unsure") and a["stage"] != "probe":
                a["confidence"] = 1 if lw == "sure" else 0
            ev.append(a)
            if a["stage"] != "probe":
                attempted.add(open_card)
            last_card, open_card, stage_next, pending_learner = open_card, None, None, None
            read = True
        elif pending_learner and open_card:
            # no verdict: the learner's turn was a question or a request, not an attempt
            kb = BRACKET.search(text)
            kind = kb.group(1) if kb else "question"
            if "confusion" in head or "re-pos" in head or "answers a card" in head:
                kind = "confusion"
            ev.append({"type": "signal", "session": session, "kind": kind, "open_item": open_card, "note": pending_learner[0].strip()[:300]})
            pending_learner = None
            read = True
        elif pending_learner and not open_card:
            kb = BRACKET.search(text)
            ev.append({"type": "signal", "session": session, "kind": kb.group(1) if kb else "question", "note": pending_learner[0].strip()[:300]})
            pending_learner = None
            read = True

        if "parked" in text.lower() and open_card:
            ev.append({"type": "signal", "session": session, "kind": "parked", "open_item": open_card, "note": "parked by the tutor"})
            open_card, stage_next = None, None
            read = True

        # a card or probe opened in the last paragraph
        c = CARD.search(last)
        if c:
            if open_card and open_card != c.group(1):
                ev.append({"type": "signal", "session": session, "kind": "parked", "open_item": open_card, "note": "a new card was posed before a verdict"})
            open_card = c.group(1)
            stage_next = "review" if re.search(r"\breview\b", last, re.I) else None
            read = True
        elif re.search(r"\bprobe\b", last, re.I) and last_card and not open_card:
            open_card, stage_next = last_card, "probe"
            read = True

        if not read:
            notes.append(f"# unread tutor turn {local_time(ts)}: {lines[0][:70] if lines else ''}")

    if pending_learner:
        notes.append(f"# learner turn with no tutor reading after it {local_time(pending_learner[1])}: {pending_learner[0].strip()[:70]}")
    return [meta()] + ev, notes


def cmd_find(cwd, since):
    root = os.path.expanduser("~/.claude/projects")
    best = None
    for d in os.listdir(root) if os.path.isdir(root) else []:
        dd = os.path.join(root, d)
        for n in os.listdir(dd):
            p = os.path.join(dd, n)
            if not n.endswith(".jsonl") or os.path.getmtime(p) < since:
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    for raw in f:
                        e = json.loads(raw)
                        if e.get("cwd"):
                            if os.path.realpath(e["cwd"]) == os.path.realpath(cwd) and (best is None or os.path.getmtime(p) > os.path.getmtime(best)):
                                best = p
                            break
            except Exception:
                continue
    if best:
        print(best)
    return 0 if best else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="write the session log from the session's transcript")
    ap.add_argument("transcript", nargs="?")
    ap.add_argument("log", nargs="?")
    ap.add_argument("--manifest")
    ap.add_argument("--dry", action="store_true", help="print the lines; write nothing")
    ap.add_argument("--find", metavar="CWD")
    ap.add_argument("--since", type=float, default=0.0)
    a = ap.parse_args(argv)
    if a.find:
        return cmd_find(a.find, a.since)
    if not (a.transcript and a.log):
        ap.error("TRANSCRIPT and LOG are needed")
    turns, sid = turns_of(a.transcript)
    if not turns:
        print("no turns in the transcript", file=sys.stderr)
        return 1
    lines, sessions, transcripts, items = existing(a.log)
    if sid and sid in transcripts:
        print(f"already in the log: transcript {sid[:8]}")
        return 0
    session = "s%d" % (len(sessions) + 1)
    manifest = manifest_fields(a.manifest or os.path.join(os.path.dirname(os.path.abspath(a.log)), "manifest.md"))
    events, notes = transcribe(turns, sid, manifest, session, items)
    out = [json.dumps(e, ensure_ascii=False) for e in events]
    new = [l for l in out if l not in lines] + [n for n in notes if n not in lines]
    if a.dry:
        print("\n".join(new))
    else:
        with open(a.log, "a", encoding="utf-8") as f:
            for l in new:
                f.write(l + "\n")
    n_att = sum(1 for e in events if e["type"] == "attempt")
    n_sig = sum(1 for e in events if e["type"] == "signal")
    print(f"{session}: {n_att} attempts, {n_sig} signals, {len(notes)} turns not read" + (" (dry)" if a.dry else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
