# =============================================================================
# Shared tool services — tools/shared/
# =============================================================================
# WHAT: Low-level services used by MORE THAN ONE tool. Currently just the
#       headless browser (browser.py), used by both `read_page` and
#       `traffic_score`.
#
# WHY here and not duplicated in each tool dir: the plugin layout is "one dir per
#       tool", but a genuinely shared dependency (Chromium/Playwright) should be
#       constructed and reasoned about in one place, not copied. The loader skips
#       this package — it holds services, not tools.
# =============================================================================
