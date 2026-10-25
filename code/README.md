# Ghost Data Still Exists - deletion-audit agent

For each request in `dataset/deletion_requests.csv` the agent decides whether deletion was requested, rebuilds
the person's identifiers, reconstructs the state of every related record at `audit_at` from the evidence files,
applies `deletion_rules.csv`, and writes `case_id,deletion_status,steps_json` to `output.csv`.

## Setup and run

Python 3.9+ and the standard library only (no packages to install).

```bash
# from the repository root (the folder that contains dataset/ and code/)
python3 code/main.py                       # writes ./output.csv  (518 rows, sorted by case_id)
python3 code/evaluation/main.py            # scores the 32 public samples (add --verbose for the diffs)
python3 code/evaluation/custom_cases.py    # extra hand-made evaluation cases
python3 code/evaluation/report.py          # regenerates evaluation/evaluation_report.md and usage_report.md
```

Options: `python3 code/main.py --dataset <dir> --requests <csv> --out <csv>`. The run is deterministic and takes a
few seconds.

### Optional: Gemini (free tier)

Nothing needs a key. With `export GEMINI_API_KEY=...` (read from the environment only, never written to disk or
logs) two hooks switch on:

* `python3 code/main.py` asks `gemini-3.1-flash-lite` (default) to classify a request only when the rule classifier is unsure.
* `python3 code/extract.py` re-reads the 11 screenshots and 4 PDFs with the same model (vision) and rewrites
  `code/audit/data/extracted_facts.json`. **Back that file up first**: a live trial of this step read several resource
  ids and states wrongly (see `evaluation/evaluation_report.md` section 5), so the shipped hand-checked file is the one used.

Model names can be overridden with `GEMINI_CLASSIFY_MODEL` and `GEMINI_VISION_MODEL`. Answers are cached in
`code/cache/llm_cache.json`; call and token counts go to `code/evaluation/usage_run.json`.

## How it works

1. **Evidence → observations** (`audit/evidence.py`, `audit/logparse.py`). SQL snapshots are executed in SQLite
   (primary / failover / replica / reporting tables are separate stores, `snapshot_scope` gives record times),
   JSON bundles, exports and ledgers, six log syntaxes (trace-aware: a lookup's result is on a different line than
   its key) and the handover text are all turned into one record type: *at time T, in store S, resource R was in
   state Q and exposed these identifiers.* Screenshots and PDFs come from `audit/data/extracted_facts.json`.
2. **Identity** (`audit/identity.py`). The request e-mail may be historical; current snapshot + `user_audit_log`
   give every e-mail, phone and e-mail hash the person has had at `audit_at`. The data catalog's
   `LINK_RESOURCE_IDENTITY` rows link screenshot resources to people.
3. **State at `audit_at`** (`audit/solve.py`). Only evidence captured and recorded at or before `audit_at` counts,
   and anything captured before `requested_at` is ignored. The latest reading wins per store; one non-compliant
   store (a drifting replica) makes the resource non-compliant. Workflow statuses and activity results are never
   used as proof.
4. **Keyless resources** (`audit/attribute.py`). Deleted accounts and already-anonymised profiles carry no
   identifier. They are attributed through the deletion worker's activity rows (fixed stage offsets before
   `reported_at`, result class follows the reported status), with elimination across all cases.
5. **Policy** (`POLICY` in `audit/solve.py`): each category maps to its compliant state, finding, remediation and
   verification operation. Retained categories (security audit log, legal hold) are only verified present.
6. **Steps.** INCOMPLETE = findings (cite evidence) + remediation (recreation paths first: disable data flows,
   block restores, then delete / redact / anonymise) + verification (no evidence id). COMPLETE = evidence-backed
   verifications only. Non-deletion requests (`audit/classify.py`, multilingual rules + optional LLM) give
   NOT_APPLICABLE with `[]`.

Prompts live in `code/prompts/`. No organizer-only file, label or case-specific shortcut is used.

## Known limits

* Accounts of cases that share one report time are paired by case order when evidence cannot tell them apart; a failed deletion-worker account event that sits within 3 seconds of an opposite-result account event is treated as a neighbouring workflow and not attributed.
* If one historical e-mail belongs to several users, the user whose e-mail history most recently owned it at `requested_at` is chosen; if that is still tied, no identity is resolved (never chosen by sorted `user_id`).
* Two retained audit records in the public samples appear in no participant file and are not produced.
* Screenshot/PDF facts were extracted once and checked by eye; `extract.py` reproduces them with Gemini.
