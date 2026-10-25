# Ghost Data Still Exists - deletion-audit agent

Built in seven parts; each commit runs on its own.

* **Part 1 - evidence ingestion.** Every evidence file becomes a normalised observation (`audit/evidence.py`).
* **Part 2 (this commit) - core solver.** Identity resolution (current + historical identifiers), state at
  `audit_at`, policy mapping, ordered FINDING / REMEDIATION / VERIFICATION steps. `python3 code/main.py` writes
  `output.csv` for all requests.
