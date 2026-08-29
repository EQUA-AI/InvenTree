"""WP-B4: the serial-coverage audit gating the scope-enforce flip."""

import json
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from assets.models import AssetMachine, Client


class AuditScopeSerialsTests(TestCase):
    """Blank serials fail the gate; duplicates are reported."""

    @classmethod
    def setUpTestData(cls):
        """One client with good, blank, and duplicated serials."""
        cls.client_row = Client.objects.create(name='Pilot Solar', code='pilot')
        other = Client.objects.create(name='Other', code='other')
        AssetMachine.objects.create(
            name='INV-A', serial='SN-001', client=cls.client_row
        )
        AssetMachine.objects.create(
            name='INV-B', serial='SN-002', client=cls.client_row
        )
        AssetMachine.objects.create(
            name='INV-C', serial='SN-002', client=cls.client_row
        )
        cls.blank = AssetMachine.objects.create(
            name='INV-NOSERIAL', serial='', client=cls.client_row
        )
        AssetMachine.objects.create(name='OTHER-1', serial='SN-100', client=other)

    def test_blank_serial_exits_one_and_names_the_machine(self):
        """Exit 1 with the offending machine and duplicate named."""
        out, err = StringIO(), StringIO()
        with self.assertRaises(SystemExit) as caught:
            call_command(
                'audit_scope_serials',
                '--client',
                'pilot',
                '--json',
                stdout=out,
                stderr=err,
            )
        self.assertEqual(caught.exception.code, 1)
        report = json.loads(out.getvalue())
        entry = report['clients']['pilot']
        self.assertEqual(entry['total'], 4)
        self.assertEqual(entry['blank'], 1)
        self.assertEqual(entry['blank_machines'][0]['pk'], self.blank.pk)
        self.assertEqual(entry['duplicates'], ['SN-002'])
        self.assertIn('Fix the data first', err.getvalue())

    def test_clean_client_passes_the_gate(self):
        """Full serial coverage exits 0."""
        out = StringIO()
        call_command('audit_scope_serials', '--client', 'other', stdout=out)
        self.assertIn('serial coverage OK', out.getvalue())

    def test_unfiltered_audit_covers_every_client(self):
        """No --client filter audits the whole registry."""
        out = StringIO()
        with self.assertRaises(SystemExit):
            call_command('audit_scope_serials', '--json', stdout=out)
        report = json.loads(out.getvalue())
        self.assertIn('pilot', report['clients'])
        self.assertIn('other', report['clients'])
