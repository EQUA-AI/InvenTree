"""
AIMMS Tests Package

Unit and integration tests for the AIMMS backend.

These run under pytest against ``ai.core.tests.settings`` - a minimal Django
configuration with an in-memory user model, a signed-cookie session engine and
only the apps the AI boundary touches. Run them with::

    pytest src/backend/InvenTree/ai/core/tests

Django's own test runner discovers them too, because they sit inside the
InvenTree tree and match ``test*.py``. It imports them with InvenTree's settings
already configured, so the ``os.environ.setdefault`` at the top of each module
is a no-op and the tests then run against the wrong world entirely - a database
session engine where they built a cookie session, the real user model where they
registered fakes. The failures that produces say nothing about the code.

So skip the package outright unless its own settings are in force. A clear skip
is honest; three inscrutable failures are not.
"""

import os
import unittest

# Logic in __init__ (RUF067) on purpose: unittest's loader catches SkipTest when
# it imports a package during discovery, and the package is the only place that
# covers every module below it in one statement.
#
# Unset is the normal pytest case - each module below sets the variable itself
# on import. Only an *already chosen* foreign settings module is disqualifying,
# because by then the setdefault in those modules can no longer take effect.
if (  # noqa: RUF067
    _configured := os.environ.get("DJANGO_SETTINGS_MODULE")
) and _configured != "ai.core.tests.settings":
    raise unittest.SkipTest(
        f"ai.core.tests needs DJANGO_SETTINGS_MODULE=ai.core.tests.settings, "
        f"but {_configured} is already configured. Run them with pytest."
    )
