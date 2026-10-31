# Adaptive Recitation Kit

A portable study protocol for any chat model, in one text file. Three modes: **study** (strict, sourced, deadline-aware; the model owns plan, order, grouping, pace and presentation and adapts them to the log; gated on dates, syllabus and the unit's materials, or a named waiver), **learn** (paced to a live course; errors tagged by grain; never runs a unit whose materials are missing), **ponder** (Socratic dialogue with sources open; three ledgers kept apart: learner, literature, model; anti-sycophancy clauses, of which only the model-assumption bar is waivable by the learner). Inside them the model uses four operations: recite, teach, analyze, plan. Model-agnostic, course-agnostic, no dependencies.

```
adaptive-recitation-kit/
  REGISTER.md            the protocol: paste block, the three modes with their gates, loop, log schema, by-hand analysis, rules, anti-sycophancy clauses, porting, reader-output rule, guardrails, evidence
  analyze.py             optional analyzer (Python 3.8+, standard library only): log -> profile.json + brief.md
  transcribe.py          optional transcriber: a Claude Code session transcript -> session_log.jsonl, written by machinery from the register's turn shapes
  overlay.example.json   learner preferences and thresholds (copy to overlay.json and edit)
  example/               a constructed two-session example on public-domain material (Euclid, Elements I, definitions, Heath 1908):
                         source.md, bank.md, session_log.jsonl, and the profile.json and brief.md the analyzer wrote from them
  tests/                 the scripted learner and tutor prompts for adherence testing, on the example unit; bank audit scripts
```

## Quick start (any model, no code)
1. Attach `REGISTER.md` and paste its section 1 as the first message.
2. Attach the unit's materials: the dates, the syllabus, the slide text, the transcript or recording, your notes, any assigned reading, and the card bank (REGISTER section 2 says what each mode needs). Attach `overlay.json` if you have one. From the second session on, also attach `session_log.jsonl` and the last `brief.md`.
3. Type `study`, `learn` or `ponder`, plus the unit (for example `study U1`).
4. At the end type `log` (the model also prints it on `end`); save what it prints. The log is the only state.

## With code
```
python3 analyze.py selftest
python3 transcribe.py TRANSCRIPT.jsonl session_log.jsonl --manifest manifest.md   # the log from the session's transcript; each event once
python3 analyze.py analyze example/session_log.jsonl --overlay overlay.example.json --out /tmp/out --today 2026-10-09
python3 analyze.py add session_log.jsonl item=U1-01 concept=point verdict=partly error=omission
```

## What the example shows
Two sessions a week apart on eight cards. Cold accuracy 56%, short-lag retries 88%, probes 100%, review at seven days 83%, four cards answered sure and wrong. The brief sets interleaving and longer retry lags (R2, R3), a change of representation for the two concepts missed twice (R5), contrast-format re-asks for the sure-and-wrong cards (R15), and persists the learner's one standing instruction (R8). The example is constructed to show the schema and the analyzer; it records no learner.

## Adherence testing
`tests/learner.py` is a scripted learner that answers the tutor from a fixed table, waives missing items by name, and never sees the answer key; `tests/PROMPT_study.txt` and `tests/PROMPT_learn.txt` drive a tutor against it inside a folder holding the kit and the example unit. Run a model against them and grade the transcript against REGISTER sections 1, 3 and 7. `tests/audit_bank.py` checks a bank for probes without locators and stems or source lines that leak another card's answer.

## Guardrails (short form)
Course material only. Corrections are sourced or silent. No adaptation tied to a lapse. The log is the state. Small samples are called small. The gates are real. Study support only; not for graded work.

## License
MIT, Sage Institute. See `LICENSE`.
