# {{PROJECT}} — decision journal

<!--
WHAT: One line per decision that the NEXT run has to know about. Read first,
      appended to last, by every persona this project consults.
WHY:  a persona has no memory between runs. Without this, every question starts
      from zero and the owner repeats context they gave an hour ago. The
      short-term half of memory (the last N exchanges) lives in the Warden's
      state volume and is deliberately forgettable; THIS half is durable — it
      survives rebuilds, quota resets and container wipes, and the owner can
      read and correct it.
HOW:  append at the bottom. Never rewrite a line: a decision that was reversed
      gets a NEW line saying so, because "we changed our mind in March" is
      itself the thing a later run needs to know.
-->

**The test for whether a line belongs here:** *if the next run does not know
this, will it decide wrongly?* If no, it is not a decision — it is progress, and
progress belongs in the task's own files.

Good: "chose Postgres over SQLite — the fleet writes concurrently and SQLite
locks." Bad: "implemented the login screen."

| date | decision | why |
|---|---|---|
| | | |
