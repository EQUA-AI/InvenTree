"""Native synthetic fixtures shared by S5 controls, never an authority adapter."""

from uuid import uuid4

from django.contrib.auth import get_user_model

from assets.locations import save_location, transfer_machines
from assets.models import AssetMachine, Client, ClientScopeGrant


class NativePlacementFixture:
    """Use real source commands and unusable test-only authentication material."""

    def setUp(self):
        """Create a native placement without implicitly enrolling a projection."""
        super().setUp()
        self.owner = Client.objects.create(name='Synthetic S5 owner', code='s5-owner')
        self.actor = get_user_model().objects.create_superuser(
            username='synthetic-s5-operator', password=None
        )
        ClientScopeGrant.objects.create(user=self.actor, client=self.owner)
        self.destination = save_location(
            self.actor,
            {
                'client': self.owner.pk,
                'name': 'Synthetic S5 location',
                'code': 's5-location',
                'timezone': 'UTC',
                'reason': 'Synthetic fixture',
            },
        )
        self.machine = AssetMachine.objects.create(
            name='Synthetic S5 source', client=self.owner
        )

    def tearDown(self):
        """Stop this synthetic capture before Django's destructive test-only flush."""
        from integration_state.models import SourceNamespace

        SourceNamespace.objects.filter(environment='synthetic-s5').update(active=False)
        super().tearDown()

    def move(self, *, destination=..., version=None, key=None):
        """Keep native validation, version, receipt, history and scope execution."""
        self.machine.refresh_from_db()
        destination = self.destination if destination is ... else destination
        return transfer_machines(
            self.actor,
            {
                'machines': [
                    {
                        'machine_id': self.machine.pk,
                        'expected_placement_version': self.machine.placement_version
                        if version is None
                        else version,
                    }
                ],
                'destination_location_id': None
                if destination is None
                else destination.pk,
                'reason': 'Synthetic S5 move',
                'idempotency_key': uuid4() if key is None else key,
            },
        )
