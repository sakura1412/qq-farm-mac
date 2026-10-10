"""Test full-cycle coverage and limits without OCR, screen capture or input."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from farm import AllFriendsBot


class FakeBot(AllFriendsBot):
    def __init__(self, pages, *, config=None, failures=None):
        # Bypass the production constructor, which prepares screen templates.
        self.cfg = {'max_pages': 20, 'max_operations': 1000,
                    'max_friends': 500, 'max_verification_passes': 6,
                    'scroll_amount': -3}
        self.cfg.update(config or {})
        self.report = {'mode': 'all_friends', 'cycle': 0,
                       'complete': False, 'friends': {}}
        self.pages = pages
        self.page = 0
        self.failures = dict(failures or {})
        self.visits = []
        self.attempts = []
        self.snapshots = []
        self.top_calls = 0
        self.rows_calls = 0
        self.scroll_calls = 0

    def top(self):
        self.top_calls += 1
        self.page = 0

    def rows(self):
        self.rows_calls += 1
        return [(key, SimpleNamespace(name=key)) for key in self.pages[self.page]]

    def visit(self, key, row):
        self.attempts.append(key)
        remaining = self.failures.get(key, 0)
        if remaining:
            self.failures[key] = remaining - 1
            return False
        self.visits.append(key)
        self.report['friends'][key] = {'name': row.name, 'harvest_clicks': 0}
        self.top()
        return True

    def scroll_ocr(self, amount):
        self.scroll_calls += 1
        self.page = min(self.page + 1, len(self.pages) - 1)

    def save_report(self):
        self.snapshots.append(deepcopy(self.report))


class CycleTests(unittest.TestCase):
    def test_unique_friends_on_overlapping_pages_are_all_visited_once(self):
        bot = FakeBot([['A', 'B'], ['B', 'C'], ['C', 'D'], ['D']])
        bot.cycle()
        self.assertEqual(bot.visits, ['A', 'B', 'C', 'D'])
        self.assertEqual(bot.report['checked_this_cycle'], 4)
        self.assertTrue(bot.report['complete'])
        self.assertTrue(bot.snapshots[-1]['complete'])

    def test_discovery_then_two_complete_scans_without_new_friends(self):
        bot = FakeBot([['A']])
        bot.cycle()
        self.assertEqual(bot.visits, ['A'])
        # First read discovers A; every full scan takes three identical reads.
        self.assertEqual(bot.rows_calls, 1 + 3 + 3 + 3)
        # Initial start, return from A, and starts of both verification scans.
        self.assertEqual(bot.top_calls, 4)
        self.assertTrue(bot.report['complete'])

    def test_limit_prevents_claiming_coverage_before_verification(self):
        bot = FakeBot([['A']], config={'max_verification_passes': 2})
        with self.assertRaisesRegex(RuntimeError, '覆盖未确认'):
            bot.cycle()
        self.assertFalse(bot.report['complete'])
        self.assertEqual(bot.report['checked_this_cycle'], 1)
        self.assertFalse(bot.snapshots[-1]['complete'])

    def test_repeated_pages_do_not_repeat_visit(self):
        bot = FakeBot([['A', 'B'], ['A', 'B'], ['A', 'B']])
        bot.cycle()
        self.assertEqual(bot.visits, ['A', 'B'])
        self.assertTrue(bot.report['complete'])

    def test_max_pages_saves_incomplete_status(self):
        bot = FakeBot([['A'], ['B'], ['C']], config={'max_pages': 1})
        with self.assertRaisesRegex(RuntimeError, '滚动上限'):
            bot.cycle()
        self.assertFalse(bot.report['complete'])
        self.assertIn('滚动上限', bot.snapshots[-1]['error'])
        self.assertEqual(bot.report['checked_this_cycle'], 1)

    def test_visit_mismatch_can_retry_without_marking_friend_visited_early(self):
        bot = FakeBot([['A', 'B']], failures={'A': 1})
        bot.cycle()
        self.assertEqual(bot.attempts, ['A', 'A', 'B'])
        self.assertEqual(bot.visits, ['A', 'B'])
        self.assertTrue(bot.report['complete'])

    def test_repeated_visit_mismatch_is_bounded_by_max_operations(self):
        bot = FakeBot([['A']], config={'max_operations': 3}, failures={'A': 100})
        with self.assertRaisesRegex(RuntimeError, '操作上限'):
            bot.cycle()
        self.assertEqual(bot.attempts, ['A', 'A', 'A'])
        self.assertEqual(bot.visits, [])
        self.assertEqual(bot.report['checked_this_cycle'], 0)
        self.assertFalse(bot.snapshots[-1]['complete'])
        self.assertIn('操作上限', bot.snapshots[-1]['error'])

    def test_new_friend_after_return_is_discovered_despite_sort_change(self):
        class ReorderedBot(FakeBot):
            def visit(self, key, row):
                successful = super().visit(key, row)
                if key == 'A' and successful:
                    self.pages = [['C', 'B'], ['A', 'B']]
                return successful
        bot = ReorderedBot([['A', 'B']])
        bot.cycle()
        self.assertEqual(bot.visits, ['A', 'C', 'B'])
        self.assertTrue(bot.report['complete'])

    def test_initial_navigation_error_saves_incomplete_report(self):
        class BadTopBot(FakeBot):
            def top(self):
                raise RuntimeError('无法打开好友列表')
        bot = BadTopBot([['A']])
        with self.assertRaisesRegex(RuntimeError, '无法打开好友列表'):
            bot.cycle()
        self.assertTrue(bot.snapshots, 'Initial navigation errors must be saved')
        self.assertFalse(bot.snapshots[-1]['complete'])
        self.assertIn('无法打开好友列表', bot.snapshots[-1]['error'])


if __name__ == '__main__':
    unittest.main()
