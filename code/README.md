# Ghost Data Still Exists - deletion-audit agent

Built in seven parts; each commit runs on its own.

* **Part 1 (this commit) - evidence ingestion.** `python3 code/main.py` loads every SQL snapshot, JSON bundle /
  export / ledger, log (six syntaxes) and the handover text into normalised observations and prints what it found.
