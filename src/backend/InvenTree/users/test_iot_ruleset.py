"""Least-privilege ruleset registration for internal IoT polling state."""

from django.test import SimpleTestCase

from users.ruleset import RuleSetEnum, get_ruleset_ignore, get_ruleset_models


class IoTRuleSetTests(SimpleTestCase):
    """Polling cursors are administrative state, not operator-editable equipment."""

    def test_checkpoint_requires_only_the_admin_ruleset(self):
        """Register checkpoints without making them public or work-order CRUD."""
        label = 'assets_ingestioncheckpoint'
        owners = [
            ruleset
            for ruleset, models in get_ruleset_models().items()
            if label in models
        ]
        self.assertEqual(owners, [RuleSetEnum.ADMIN])
        self.assertNotIn(label, get_ruleset_ignore())
