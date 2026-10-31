# Adaptive brief

**Next block: defaults (16 data points, below the 4 needed to adapt); 8 card(s) due now.**

Generated 2026-10-09 by analyze.py v1.6. Data: 19 attempts on 8 items (16 cards, 3 probes), 3 learner signals.
Exam: 2026-10-15 (6 days).

Register in force (from overlay):
- verdict: correct / partly / no, then the specific error, then the corrected statement, then stop
- tone: terse, no padding, no closing line that needs a follow-up

## Gate met (study)
- mode: study
- present: dates ("Exam: 2026-10-15"), source.md ("# Unit 1 source: Euclid"), bank.md ("# Unit 1 bank"), session_log.jsonl ("# Constructed example")
- missing: none
- waived: none
- study gate: met

## Settings for the next block
- **Mode:** RECITE (default; not enough data to change it)
- **Order [R2, act]:** Short-lag success is easy and says little about retention. Set the next retry at 7+ cards or the block end; a short-lag correct does not count toward retirement (two correct sessions). Evidence: short-lag retries (<=6 cards) 88% over 8.
- **Order [R3, act]:** Interleave: alternate 1 new : 1 retry (retries 3+ cards after the miss). Avoid a wall of new cards or a wall of retries. Evidence: longest run of the same stage = 5 cards in a row.
- **Feedback [R5, act]:** Do not repeat the same explanation. Change representation: add the 'why' line, an outside definition, a contrast pair or a worked example, then re-test 3+ cards later. Evidence: 2+ non-correct attempts: straight-line, circle.
- **Presentation [R9, tentative]:** Open new topics with the scenario format, then fade to explain. Evidence: scenario 100% vs explain 56%.
- **Order [R15, act]:** Re-ask each at 7+ cards in contrast format. Open the next block with one line: say unsure when unsure; a wrong sure costs more than a blank. Evidence: 4 card(s) answered sure and wrong: U1-04, U1-06, U1-07, U1-08.
- **Presentation [R8]:** Persist as overlay settings and obey from the first card of the next session: give the Greek term beside each defined word at its first use

## Snapshot
| measure | value | n |
|---|---|---|
| cold (first-attempt) accuracy | 56% | 8 |
| retry accuracy, short lag (<=6 cards) | 88% | 8 |
| retry accuracy, long lag | n/a | 0 |
| probe (transfer) accuracy | 100% | 3 |
| longest same-stage run | 5 cards | |
| review accuracy, 7+ days | 83% | 3 |
| probe accuracy, cue-free stems | 100% | 3 |
| calibration: sure-and-wrong / unsure-and-right | 4 / 0 | 9 |
| error mix | omission 4, misframe 1, wording 1, intrusion 1 | |
| learner signals | outside_definition 1, integrate 1, instruction 1 | |

## Concepts, weakest first (mastery 0-1, confidence 0-1)
| concept | framework | mastery | confidence | attempts | last |
|---|---|---|---|---|---|
| plane | definitions | 0.53 | 0.47 | 2 | correct |
| straight-line | definitions | 0.55 | 0.54 | 3 | correct |
| circle | figures | 0.59 | 0.54 | 3 | partial |
| line-extremities | definitions | 0.67 | 0.33 | 1 | correct |
| line | definitions | 0.70 | 0.54 | 3 | correct |
| parallel | figures | 0.70 | 0.54 | 3 | correct |
| point | definitions | 0.73 | 0.47 | 2 | correct |
| surface | definitions | 0.73 | 0.47 | 2 | correct |

## Confusion pairs
- definitions answered with straight-line (1)

## Learner-originated cards to add (one per question)
- [outside_definition] straight-line: asked whether the shortest-path idea is Euclid's; it is not in Book I's definitions (Archimedes' axiom is the usual source)
- [integrate] parallel: asked whether two lines that never meet in different planes are parallel; the definition requires the same plane

## Queue
- U1-01 (point) box 2: end-of-session pass  <- due now
- U1-06 (plane) box 2: end-of-session pass  <- due now
- U1-04 (straight-line) box 2: end-of-session pass  <- due now
- U1-07 (circle) box 2: end-of-session pass  <- due now
- U1-03 (line-extremities) box 2: end-of-session pass  <- due now
- U1-02 (line) box 3: final pass: retired, last attempt 2026-10-08  <- due now
- U1-05 (surface) box 2: final pass: retired, last attempt 2026-10-08  <- due now
- U1-08 (parallel) box 2: final pass: retired, last attempt 2026-10-08  <- due now

## Confidence and what would change this
- Sample: 16 cards. Rules fire as 'tentative' from 4 data points and 'act' from 8. Treat tentative rules as experiments: apply them for one block, log, re-run.
- To test the mode rule, log `presentation` on every new card (question_first vs source_first) and compare cold accuracy next run.
