# Session learning loop (all sessions)

At the end of a session, capture ONE durable lesson to the knowledge base **only
if** you established a generalizable, code-anchored, OBSERVED fact about the work — product
code, build/config, logging/telemetry, or an investigation method (e.g. "trace X
in source Y, not the logs").

Rules:
- WRITE only when it (a) generalizes beyond this one task AND (b) you can cite a
  real `file:line` / command / log line. Otherwise write nothing.
- Never write: one-off feature Q&A, speculation, or self-changes to the agent kit
  itself (those belong in a separate kit ledger, not the product KB). When
  unsure, don't write.
- The capture tool verifies the `--source` file exists (or rejects it), so cite
  only real anchors. A resolvable anchor proves the source exists, not that your
  conclusion is correct — capture OBSERVED facts, never inferences.
- New captures are recorded `validated: false` (advisory) until `kb-curate`
  re-verifies them; recall shows un-curated lessons as `[unverified]`.
- Stay silent when nothing durable was learned — no empty/`--none` captures.

Command:

```
python "<path to skills>/learning-capture/capture_learning.py" \
  --kb-root __KB_ROOT__ \
  --lesson "<one-line, generalizable, verified fact>" \
  --source "<repo-relative file>:line" --source-root <reviewed/edited repo root> \
  --layer <your-layer>
```
