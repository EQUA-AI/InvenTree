"""VERIFY-H01: post-delete Owner cleanup tolerates a missing Owner row.

Deleting a user or group whose Owner row was never created (the offline
management-command context suppresses ``create_owner``) must not crash the
post_delete callback, must leave unrelated Owner rows untouched, and must
still really delete the Owner row when one exists. Real ORM rows and real
signals only — signals are never disabled and no permission is weakened.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from users.models import Owner


class OwnerCleanupTests(TestCase):
    """The delete_owner callback is robust for user and group, both states."""

    def _owner_row_count(self, obj) -> int:
        """Count Owner rows for one user/group exactly as get_owner resolves."""
        model = obj.__class__
        return Owner.objects.filter(
            owner_type=ContentType.objects.get_for_model(model), owner_id=obj.pk
        ).count()

    def _ensure_missing_owner(self, obj) -> None:
        """Force the precondition: no Owner row for this principal.

        The production helper normally creates one on save; the offline
        command context suppresses it. Removing the row (real ORM) makes the
        precondition hold in either context without touching any signal.
        """
        existing = Owner.get_owner(obj)
        if existing is not None:
            existing.delete()
        self.assertIsNone(Owner.get_owner(obj))

    # ---- missing Owner row: no crash, unrelated owners untouched ----------

    def test_user_without_owner_row_deletes_cleanly_and_keeps_unrelated_owner(
        self,
    ) -> None:
        """A user with no Owner row deletes without an AttributeError."""
        user_model = get_user_model()
        suffix = 'ownerless-user'
        survivor = user_model.objects.create_user(username=f'survivor-{suffix}')
        survivor_owner = Owner.create(obj=survivor)
        self.assertIsNotNone(survivor_owner)
        assert survivor_owner is not None

        doomed = user_model.objects.create_user(username=f'doomed-{suffix}')
        self._ensure_missing_owner(doomed)
        doomed_pk = doomed.pk

        doomed.delete()

        self.assertFalse(
            Owner.objects.filter(
                owner_type=ContentType.objects.get_for_model(user_model),
                owner_id=doomed_pk,
            ).exists()
        )
        self.assertIsNotNone(Owner.get_owner(survivor))
        self.assertEqual(Owner.get_owner(survivor).pk, survivor_owner.pk)

    def test_group_without_owner_row_deletes_cleanly_and_keeps_unrelated_owner(
        self,
    ) -> None:
        """A group with no Owner row deletes without an AttributeError."""
        suffix = 'ownerless-group'
        survivor = Group.objects.create(name=f'survivor-{suffix}')
        survivor_owner = Owner.create(obj=survivor)
        self.assertIsNotNone(survivor_owner)
        assert survivor_owner is not None

        doomed = Group.objects.create(name=f'doomed-{suffix}')
        self._ensure_missing_owner(doomed)
        doomed_pk = doomed.pk

        doomed.delete()

        self.assertFalse(
            Owner.objects.filter(
                owner_type=ContentType.objects.get_for_model(Group), owner_id=doomed_pk
            ).exists()
        )
        self.assertIsNotNone(Owner.get_owner(survivor))
        self.assertEqual(Owner.get_owner(survivor).pk, survivor_owner.pk)

    # ---- existing Owner row: really deleted with the principal ------------

    def test_existing_owner_row_is_deleted_with_the_user(self) -> None:
        """A provisioned Owner row is removed when its user is deleted."""
        user_model = get_user_model()
        doomed = user_model.objects.create_user(username='provisioned-user')
        owner = Owner.create(obj=doomed)
        self.assertIsNotNone(owner)
        assert owner is not None
        self.assertTrue(Owner.objects.filter(pk=owner.pk).exists())

        doomed.delete()

        self.assertFalse(Owner.objects.filter(pk=owner.pk).exists())

    def test_existing_owner_row_is_deleted_with_the_group(self) -> None:
        """A provisioned Owner row is removed when its group is deleted."""
        doomed = Group.objects.create(name='provisioned-group')
        owner = Owner.create(obj=doomed)
        self.assertIsNotNone(owner)
        assert owner is not None
        self.assertTrue(Owner.objects.filter(pk=owner.pk).exists())

        doomed.delete()

        self.assertFalse(Owner.objects.filter(pk=owner.pk).exists())
