# What may I decide alone, and when must I ask the owner?

<!--
WHAT: The line between "make the routine call and keep going" and "stop, ask,
      wait" — plus how to ask well when asking is right.
WHY:  both failure modes are expensive. An agent that asks about everything turns
      a one-prompt task into a conversation; an agent that never asks silently
      picks the wrong interpretation and delivers the wrong feature.
STATUS: STUB — outline only.
-->

## To fill in

- **Decide alone** when the choice is reversible, conventional, or invisible in
  the result: naming, file placement, which existing helper to reuse, ordering of
  independent work. State the assumption in the report; do not stop for it.
- **Ask** when two readings of the request lead to materially different work, or
  when the action is hard to reverse or outward-facing (pushing, deploying,
  deleting data, anything that leaves the machine).
- **Ask once, in a batch.** Enumerate everything unknown up front rather than
  drip-feeding questions across the task. Do the work that does not depend on the
  answer first.
- **How to ask**: give the options with a recommendation, not an open question.
  "A or B, I'd pick A because X" beats "how should I do this?".
- **Never block on a question you can answer from the code**, the repo history,
  or a two-minute experiment.
- **Where the question goes** in autonomous runs (the pipeline's ask-the-owner
  path) versus an interactive session.

## Kaizen-specific

- The Warden contract has a first-class `AskOwner` — a project's agent can block
  on a question that reaches the owner through the Hub. Prefer it over guessing.
