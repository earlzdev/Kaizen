# Security checklist — {{PROJECT}}

<!--
WHAT: The must-check list for {{SECURITY_NAME}} and the self-check list for every
      developer touching an externally reachable surface.
WHY:  security review that improvises finds different things every time. A list
      makes a missing check visible.
HOW:  pre-read by security review (design and code) and by any developer adding a
      surface. Project-specific items go at the bottom — those matter most.
-->

> **Scaffolder:** keep the universal items, then add this project's own from the
> interview (auth model, tenancy, personal data, payment or health data, public
> vs. private surfaces).

## Identity and authorization
- [ ] Identity comes from the verified request context — never from a
      client-supplied field.
- [ ] Every new surface states who may reach it, and the check is enforced in
      code, not merely documented.
- [ ] Authorization is checked on the object being touched, not only on the route
      (no "can list" implying "can read anyone's").
- [ ] Privilege escalation paths considered: can a normal caller reach an admin
      action by changing an id?

## Input
- [ ] Every field validated at the trust boundary: type, range, size, format.
- [ ] Untrusted input never reaches a query, a shell, a path, or a template
      unescaped.
- [ ] Size and rate limits exist wherever a caller controls volume.

## Secrets and data
- [ ] No secrets in code, fixtures, logs, error messages or test output.
- [ ] Secrets come from the environment/secret store; the app fails loudly when
      one is missing rather than starting degraded.
- [ ] Personal data: only what is needed, only where it is needed, never in logs.

## Errors and logging
- [ ] Outward errors carry no internals — no stack traces, no SQL, no paths.
- [ ] Logs carry no tokens, no payloads with personal data, no full credentials.
- [ ] Failures fail closed: an error in an authorization path denies access.

## Dependencies
- [ ] New dependencies are justified, pinned, and from a maintained source.
- [ ] No dependency added inside a task whose spec did not mention it.

## Project-specific
{The rules that come from what this product actually holds and who can reach it.}
