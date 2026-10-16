"""Display identities the tests configure, the way an installation would.

Seat IDs are role-based (replika-companion, kindroid-companion...); names
and pronouns come from these settings. Importing this module sets them for
any test module that does not configure its own.
"""

import os

CONFIGURED = {
    "TINYTABLE_OPERATOR_NAME": "Sam",
    "TINYTABLE_OPERATOR_PRONOUNS": "she/her",
    "TINYTABLE_REPLIKA_COMPANION_NAME": "Juniper",
    "TINYTABLE_REPLIKA_COMPANION_PRONOUNS": "she/her",
    "TINYTABLE_KINDROID_COMPANION_NAME": "Wren",
    "TINYTABLE_KINDROID_COMPANION_PRONOUNS": "she/her",
    "TINYTABLE_KINDROID_COMPANION_2_NAME": "Robin",
    "TINYTABLE_LOCAL_MODEL_NAME": "Ember",
}
for key, value in CONFIGURED.items():
    os.environ.setdefault(key, value)
