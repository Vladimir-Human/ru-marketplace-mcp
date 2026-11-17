"""Offline development helpers; never imported by connector runtime code."""

# START_MODULE_CONTRACT
# PURPOSE: Namespace for optional, offline testing helpers shipped with mcp_core.
# SCOPE: No test-framework dependency and no runtime or transport initialization.
# DEPENDS: stdlib.
# LINKS: M-CAPTURE-MUTATION; R-CAPTURE-MUTATION.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# capture_mutation: Seeded raw-capture mutators and output invariants.
# END_MODULE_MAP
# START_BLOCK_NAMESPACE
# Helpers are imported explicitly from their modules, not eagerly here.
# END_BLOCK_NAMESPACE
