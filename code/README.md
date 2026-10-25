# Ghost Data Still Exists - deletion-audit agent

Built in seven parts; each commit runs on its own.

* **Part 1 - evidence ingestion.** Every evidence file becomes a normalised observation (`audit/evidence.py`).
* **Part 2 (this commit) - core solver.** Identity resolution (current + historical identifiers), state at
  `audit_at`, policy mapping, ordered FINDING / REMEDIATION / VERIFICATION steps. `python3 code/main.py` writes
  `output.csv` for all requests.
* **Part 3 (this commit) - scorer.** `python3 code/evaluation/main.py [--verbose]` scores the 32 public samples:
  status, step precision / recall / F1, field and sequence accuracy.
* **Part 4 (this commit) - keyless resources.** `audit/attribute.py` attributes deleted accounts and anonymised
  analytics profiles from the deletion worker's activity rows (stage offsets, result class, cross-case elimination).
* **Part 5 (this commit) - screenshots and PDFs.** Facts of the 11 screenshots and 4 PDFs
  (`audit/data/extracted_facts.json`) plus the data catalog's `LINK_RESOURCE_IDENTITY` links.
