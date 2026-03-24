# Email Assistant Batch Report

Run time: 2026-03-24  
Model: `gpt-4o-mini`  
Input set: `examples/*.json` (6 files)  
Output dir: `outputs/`

## Overall Result

- Total cases: 6
- Successful runs: 6
- Runtime stability: no schema/parsing crash
- Output schema consistency: all outputs contain `summary`, `key_points`, `action_items`, `open_questions`
- Hallucination control: generally good (facts mostly grounded in input)

## Case-by-Case Review

### 1) `01_single_email.json`
- Status: pass
- Strength: correctly extracted contract review task, owner, and explicit deadline
- Risk: low

### 2) `02_deadline_clear_thread.json`
- Status: pass
- Strength: correctly identified core action and due date
- Risk: low

### 3) `03_meeting_schedule_thread.json`
- Status: mostly pass
- Strength: captured meeting objective, open question, and SRE task
- Issue:
  - deadline for "Prepare timeline and logs" appears as `2026-03-18T12:00:00Z`, but source text says "Friday 12 PM" and timestamp context may not map exactly to this ISO date.
- Risk: medium (relative-time to absolute-date conversion can drift)

### 4) `04_fyi_thread.json`
- Status: pass
- Strength: correctly returned no action items for FYI thread
- Risk: low

### 5) `05_owner_unknown_thread.json`
- Status: mostly pass
- Strength: correctly set owner to `unknown` and raised open question
- Issue:
  - deadline inferred as `2026-03-26` from "next week"; this may be over-interpretation.
- Risk: medium (ambiguous temporal phrase normalization)

### 6) `06_long_ambiguous_thread.json`
- Status: pass
- Strength: captured ambiguity and missing ownership/deadline as `unknown`
- Risk: low to medium (complex threads may still miss minor facts)

## Quality Summary

- Summary quality: good, concise and business-friendly
- Key points coverage: good in most cases
- Action extraction:
  - strong for explicit assignments
  - acceptable for implicit tasks (uses `unknown` when unclear)
- Open questions extraction: useful and generally accurate

## Main Failure Patterns Observed

1. Relative time interpretation (`Friday`, `next week`) may be converted into potentially inaccurate absolute dates.
2. In implicit assignment contexts, owner inference can be conservative (good for precision) but may lower recall.

## Recommended Next Iteration

1. Prompt hardening for temporal ambiguity:
   - If date expression is relative and not explicitly anchored, keep original text or set `unknown` instead of forcing ISO conversion.
2. Optional schema extension:
   - add `evidence` field per action item (short quote/source message index).
3. Add lightweight deterministic post-processing:
   - validate suspicious deadline normalization and fallback to `unknown` when confidence is low.
4. Build a simple regression script:
   - run all examples and compare outputs against expected checkpoints (owner/deadline presence and schema validity).
