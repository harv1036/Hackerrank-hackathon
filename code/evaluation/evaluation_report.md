# Evaluation report

Scored with `python3 code/evaluation/main.py` (scorer) on the 32 public samples (`dataset/sample_output.csv`).
The samples were used while developing the solver, so these numbers are *development* scores, not a held-out
estimate. The 518 evaluation cases have no public answers.

## 1. Results on the 32 public samples

| Metric | Score |
|---|---|
| Case-level deletion-status accuracy | 100.0% |
| Step precision (all 6 fields) | 99.5% |
| Step recall | 94.3% |
| Step F1 | 96.8% |
| Core step F1 (ignoring evidence_id) | 96.8% |
| Field-level accuracy (matched steps) | 100.0% |
| Sequence accuracy (exact ordered steps) | 68.8% |
| Exact-case accuracy (sequence + every evidence_id) | 68.8% |

Per field: `phase` 100.0%, `system_id` 100.0%, `resource_id` 100.0%, `operation` 100.0%, `state` 100.0%, `evidence_id` 100.0%

The samples come from two generators. Cases 03, 09 and 11-32 (24 cases) use random resource ids and are
the same kind as the 518 evaluation cases; cases 01-02, 04-08 and 10 (8 cases) are hand-built stories
whose account ids (`account_001`, ...) never occur in the evaluation requests.

| Subset | Cases | Status acc. | Step F1 | Sequence acc. |
|---|---|---|---|---|
| Evaluation-like (random ids) | 24 | 100.0% | 98.8% | 87.5% |
| Hand-built stories | 8 | 100.0% | 78.9% | 12.5% |

## 2. Final run on the 518 requests

`output.csv`: 518 rows, status split COMPLETE 154, INCOMPLETE 354, NOT_APPLICABLE 10.
Format checks (518 unique sorted case ids, valid JSON arrays, consecutive `step_no`, phase order
FINDING→REMEDIATION→VERIFICATION, empty `evidence_id` on remediation and on every verification of an INCOMPLETE
case, `[]` for NOT_APPLICABLE) all pass. One deliberate exception: in 2 COMPLETE cases a retained audit record appears
only in a bundle exported after `audit_at`, which may not be cited, so its verification step carries an empty `evidence_id`.

## 3. Additional evaluation cases (created for this project)

`python3 code/evaluation/custom_cases.py` builds tiny synthetic evidence sets and runs the real solver.

| # | Scenario | Result |
|---|---|---|
| custom_1 | Request e-mail is a historical address; the stale search entry still carries it | pass |
| custom_2 | Primary says gone, failover replica still holds the entry (replication drift) | pass |
| custom_3 | A later export (after audit_at) re-lists the entry; it must not change the answer | pass |
| custom_4 | Security audit record inside its retention period is verified present, never deleted | pass |
| custom_5 | Tombstone still exposes the e-mail: redact (keep the stable reference), do not delete | pass |
| custom_6 | Recreation paths are shut before records are deleted | pass |
| custom_7 | Access request is NOT_APPLICABLE, a French deletion request is not | pass |


## 4. Failures found and improvements made

| Failure seen on the samples | Cause | Fix |
|---|---|---|
| Resources missing for log evidence | A request/response pair spans two log lines (trace id) in six different syntaxes | Trace-aware parser, one observation per completed call |
| Case 26 wrongly INCOMPLETE | A stale bundle said PRESENT, a later export returned 404 in another store | One shared `service` store per system, so the latest readback supersedes; SQL replicas stay separate |
| False finding from a March log | Evidence captured before the request describes an earlier life of the account | Ignore observations captured before `requested_at` |
| Every citation pointed to a bundle exported after `audit_at` | Latest-file rule ignored the capture time | Only files captured and observations recorded at or before `audit_at` can decide or be cited |
| Deleted accounts / anonymised profiles not found | They carry no identifier | Attribute them from the deletion worker's activity rows: fixed stage offsets before `reported_at`, result class follows the reported status, cross-case elimination, and case-order pairing when several cases share one report time |
| Verification of INCOMPLETE cases cited evidence | Proposed future checks are not backed by evidence | Empty `evidence_id` for all steps after the findings |
| Odd `system_id` values (`backup_restore_service`...) | Snapshot `data_store` names the container, not the resource | Owning system derived from the resource-id prefix |
| Case 07 missed (attachment only in a PDF) | PDFs and screenshots are not text | Facts of the 11 screenshots and 4 PDFs extracted once (`code/audit/data/extracted_facts.json`) and linked through the data-catalog `LINK_RESOURCE_IDENTITY` rows |
| Non-deletion requests | `request_text` can be an access, correction or marketing request | Multilingual rule classifier (+ optional Gemini fallback) → NOT_APPLICABLE with `[]` |

Remaining known gaps on the samples: the hand-built stories (account ids that exist only in the sample answers and
in nothing the solver can read) and two retained audit records (cases 18 and 20) that appear in no participant file.
Accounts shared between cases with identical report times are attributed by case order, which is a heuristic.
