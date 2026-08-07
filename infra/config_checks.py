# =============================================================================
# Config sanity — infra/config_checks.py
# =============================================================================
# WHAT: One question every service asks at boot: is this setting a real value,
#       or is it still whatever `.env.example` had in it?
#
# WHY it is shared and not re-typed per service: `${VAR:?}` and `if not value`
#       both catch UNSET and neither catches PLACEHOLDER, so a host whose `.env`
#       was copied from the template boots happily on `change-me-…` and fails
#       later, somewhere else, as a symptom that names nothing. Generating a
#       `.env` from the template is right for development and wrong for
#       production; this is what lets a service tell the two apart.
#
# HOW:  if is_placeholder(settings.module_event_token): raise SystemExit(...)
# =============================================================================

# Prefixes the repo's own templates use. Kept as a tuple rather than a regex so
# adding one is obvious, and so a real secret that merely CONTAINS one of these
# words is not rejected — the check is anchored at the start.
_PLACEHOLDER_PREFIXES = (
    "change-me",
    "replace-me",
    "your-",
    "todo",
    "xxx",
)


def is_placeholder(value: str | None) -> bool:
    """True when `value` is missing, blank, or still a template value."""
    if value is None:
        return True
    stripped = value.strip().lower()
    if not stripped:
        return True
    return stripped.startswith(_PLACEHOLDER_PREFIXES)


__all__ = ["is_placeholder"]
