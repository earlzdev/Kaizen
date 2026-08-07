# =============================================================================
# Unit tests — brain/access.py (_decide: the pure access-list decision)
# =============================================================================
# WHAT: the specificity ladder (exact 3 > module-or-tool 2 > blanket 1) and the
#       tie-breaks-toward-deny rule, over hand-built AccessRule rows.
# WHY: this gate is the only thing standing between an agent and every tool;
#       _decide was explicitly factored out to be testable without a DB.
# HOW: AccessRule ORM objects are constructed directly — no session, no engine.
# =============================================================================

from brain.access import AccessControl
from brain.db.models import AccessRule


def rule(module=None, tool=None, allowed=False):
    return AccessRule(agent_id=1, module=module, tool=tool, allowed=allowed)


def decide(rules, module, tool):
    return AccessControl._decide(rules, module, tool)


def test_no_rules_allows_by_default():
    assert decide([], "mentor", "search_knowledge") is True


def test_blanket_deny_blocks_everything():
    rules = [rule(allowed=False)]
    assert decide(rules, "mentor", "search_knowledge") is False
    assert decide(rules, None, "remember_fact") is False


def test_module_deny_blocks_only_that_module():
    rules = [rule(module="mentor", allowed=False)]
    assert decide(rules, "mentor", "search_knowledge") is False
    assert decide(rules, "tracker", "list_tasks") is True


def test_exact_allow_overrides_module_deny():
    rules = [
        rule(module="mentor", allowed=False),
        rule(module="mentor", tool="search_knowledge", allowed=True),
    ]
    assert decide(rules, "mentor", "search_knowledge") is True
    assert decide(rules, "mentor", "add_goal") is False


def test_module_deny_overrides_blanket_allow():
    rules = [rule(allowed=True), rule(module="tools", allowed=False)]
    assert decide(rules, "tools", "weather") is False
    assert decide(rules, "mentor", "add_goal") is True


def test_equal_specificity_tie_breaks_toward_deny():
    rules = [
        rule(module="mentor", tool="search_knowledge", allowed=True),
        rule(module="mentor", tool="search_knowledge", allowed=False),
    ]
    # Same score, disagreeing — deny must win regardless of rule order.
    assert decide(rules, "mentor", "search_knowledge") is False
    assert decide(list(reversed(rules)), "mentor", "search_knowledge") is False


def test_rule_for_other_tool_does_not_apply():
    rules = [rule(module="mentor", tool="add_goal", allowed=False)]
    assert decide(rules, "mentor", "search_knowledge") is True
