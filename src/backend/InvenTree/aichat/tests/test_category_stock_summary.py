"""Category aggregation includes descendants and retains the stock threshold."""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from ai.core.tools.inventree.read.stock import category_stock_summary
from part.models import Part, PartCategory
from stock.models import StockItem


class CategoryStockSummaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = SimpleNamespace(pk=1)
        cls.root = PartCategory.objects.create(name='Category stock fixture')
        cls.child = PartCategory.objects.create(name='Child fixture', parent=cls.root)
        cls.other = PartCategory.objects.create(name='Other fixture')
        for name, category, quantities in [
            ('Split across bins', cls.child, [1200, 900]),
            ('Root stock', cls.root, [2101]),
            ('Exactly threshold', cls.child, [2000]),
            ('Outside category', cls.other, [9999]),
        ]:
            part = Part.objects.create(name=name, category=category)
            for quantity in quantities:
                StockItem.objects.create(part=part, quantity=quantity)

    def test_subtree_threshold_sums_before_filtering_and_excludes_other_categories(self):
        with (
            patch('ai.core.tools.inventree.read.database._current_user', return_value=self.user),
            patch('users.permissions.check_user_role', return_value=True) as permission,
        ):
            result = category_stock_summary(self.root.pk, 2000)
        self.assertTrue(result['resolved'])
        self.assertEqual(result['part_count'], 2)
        self.assertEqual({p['name'] for p in result['parts']}, {'Split across bins', 'Root stock'})
        self.assertEqual(result['quantity_greater_than'], '2000')
        self.assertFalse(result['truncated'])
        self.assertEqual([call.args[1:] for call in permission.call_args_list], [('part', 'view'), ('stock', 'view')])

    def test_stock_permission_is_required_even_with_part_permission(self):
        with (
            patch('ai.core.tools.inventree.read.database._current_user', return_value=self.user),
            patch('users.permissions.check_user_role', side_effect=lambda _user, role, _permission: role == 'part'),
        ):
            result = category_stock_summary(self.root.pk, 2000)
        self.assertFalse(result['resolved'])
        self.assertNotIn('parts', result)

    def test_detail_cap_does_not_truncate_the_count(self):
        parts = [
            Part.objects.create(name=f'Bulk stock {index}', category=self.child)
            for index in range(201)
        ]
        StockItem.objects.bulk_create([
            StockItem(part=part, quantity=3000) for part in parts
        ])
        with (
            patch('ai.core.tools.inventree.read.database._current_user', return_value=self.user),
            patch('users.permissions.check_user_role', return_value=True),
        ):
            result = category_stock_summary(self.root.pk, 2000)
        self.assertEqual(result['part_count'], 203)
        self.assertEqual(len(result['parts']), 200)
        self.assertTrue(result['truncated'])

    def test_missing_principal_and_nonfinite_threshold_fail_closed(self):
        with patch('ai.core.tools.inventree.read.database._current_user', return_value=None):
            self.assertFalse(category_stock_summary(self.root.pk, 2000)['resolved'])
        for minimum in [float('nan'), float('inf'), -1]:
            self.assertFalse(category_stock_summary(self.root.pk, minimum)['resolved'])
