# NetSage AI — Human Review Helper Prompt

This is a small helper template, not sent to the AI model — it's the
structure a human reviewer fills in after reading an AI diagnosis produced
by `diagnose_prompt.md`. It keeps every review consistent so the
Responsible AI log and dashboard can be built from the same fields.

It is NOT used to ask the AI to grade itself. A human always fills this in.

---

## Review Record Template

```
case_id:            <e.g. C007>
ai_root_cause:       <copied from the AI's JSON "root_cause">
ai_confidence:        <copied from the AI's JSON "confidence">
reviewer_decision:   <Accepted | Edited | Rejected>
reviewer_notes:      <why accepted/edited/rejected -- be specific, cite evidence>
corrected_root_cause: <only if Edited or Rejected -- what the real root cause is>
time_to_review_sec:  <optional, for measuring reviewer effort>
reviewed_by:         <reviewer name/initials>
reviewed_at:         <ISO 8601 timestamp>
```

## Decision Guidance

- **Accepted** — the AI's root_cause, evidence, and fix_steps are correct
  and could be handed to the student as-is.
- **Edited** — the AI was on the right track (right general area / OSI
  layer) but got a detail wrong (e.g., named the wrong VLAN, wrong
  command, incomplete fix). Record the corrected version.
- **Rejected** — the AI's root_cause is wrong, unsupported by the
  evidence, or invents evidence that wasn't provided. Record what the
  actual root cause is.

Any case marked **Edited** or **Rejected** must be added to
`responsible_ai_log/corrections_log.csv` (at least 5 required per the
project spec) with a short explanation of *why* the AI was wrong — e.g.
"confused gateway_mismatch with wrong_subnet_mask because both showed a
/24 vs /26 difference in the same evidence block."
