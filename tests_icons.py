"""Icon-only list decisions; no screen capture, OCR service or live input."""
from copy import deepcopy
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

import cv2
import numpy as np

import farm
from all_friends import TextBox, icon_rows
from farm import IconFriendsBot

farm.cv2 = cv2
farm.np = np
HEIGHT = 1468
ATTACHMENT = Path(os.environ.get('QQFARM_TEST_LIST_IMAGE', str(Path(__file__).parent / 'tests' / 'fixtures' / 'list.png')))


def controls():
    return [TextBox('同玩好友', 100, 270, 140, 30),
            TextBox('微信好友', 410, 270, 140, 30),
            TextBox('拜访', 700, 450, 60, 30),
            TextBox('拜访', 700, 620, 60, 30),
            TextBox('拜访', 700, 790, 60, 30)]


class IconRowsTests(unittest.TestCase):
    def test_missing_all_names_does_not_block_pairing(self):
        icons = [(400, 465, .99), (400, 635, .97), (400, 805, .98)]
        rows = icon_rows(list(reversed(controls())), HEIGHT, list(reversed(icons)))
        self.assertEqual([row.button.cy for row in rows], [465, 635, 805])
        self.assertEqual([row.icon for row in rows], icons)

    def test_duplicate_variants_only_visit_one_button(self):
        rows = icon_rows(controls(), HEIGHT, [(400, 465, .99), (403, 466, .95)])
        self.assertEqual(len(rows), 1)

    def test_no_icons_does_not_require_names(self):
        self.assertEqual(icon_rows(controls(), HEIGHT, []), [])

    def test_footer_and_header_icons_are_excluded(self):
        rows = icon_rows(controls(), HEIGHT,
                         [(400, 200, .99), (400, 1300, .99), (400, 465, .99)])
        self.assertEqual([row.button.cy for row in rows], [465])

    def test_icon_in_different_row_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, '同一行'):
            icon_rows(controls(), HEIGHT, [(400, 550, .99)])

    def test_icon_at_or_beyond_button_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, '同一行'):
            icon_rows(controls(), HEIGHT, [(750, 465, .99)])

    def test_missing_visit_button_is_not_a_quiet_page(self):
        boxes = [box for box in controls() if box.text != '拜访']
        with self.assertRaisesRegex(RuntimeError, '拜访按钮'):
            icon_rows(boxes, HEIGHT, [(400, 465, .99)])

    def test_missing_tabs_are_not_a_quiet_page(self):
        boxes = [box for box in controls() if box.text != '同玩好友']
        with self.assertRaisesRegex(RuntimeError, '同玩好友'):
            icon_rows(boxes, HEIGHT, [])


class FakeIconBot(IconFriendsBot):
    """Page IDs represent the stationary ranking/profile-photo signature."""
    def __init__(self, pages, *, config=None, stale=()):
        self.cfg = {'max_pages': 20, 'max_operations': 1000, 'max_friends': 500,
                    'scroll_amount': -3, 'max_stale_icon_retries': 3}
        self.cfg.update(config or {})
        self.report = {'mode': 'stealable_icons', 'cycle': 0,
                       'complete': False, 'actions': []}
        self.pages = deepcopy(pages)
        self.page = 0
        self.stale = set(stale)
        self.attempts = []
        self.top_calls = 0
        self.scroll_calls = []
        self.scanned_pages = []
        self.snapshots = []
        self.signature_ids = {}

    def top(self):
        self.top_calls += 1
        self.page = 0

    def scan_list(self):
        current = self.pages[self.page]
        self.scanned_pages.append(current['id'])
        signature_id = self.signature_ids.setdefault(current['id'], len(self.signature_ids) + 1)
        signature = np.full((8, 8), signature_id * 20, dtype=np.uint8)
        targets = [SimpleNamespace(key=key, button=SimpleNamespace(cy=465 + index * 170))
                   for index, key in enumerate(current['targets'])]
        return targets, signature

    def scroll_ocr(self, amount):
        self.scroll_calls.append((self.page, amount))
        self.page = min(self.page + 1, len(self.pages) - 1)

    def visit_icon(self, target):
        _, signature = self.scan_list()
        self.attempts.append(target.key)
        clicks = 0 if target.key in self.stale else 1
        if clicks:
            for page in self.pages:
                page['targets'] = [key for key in page['targets'] if key != target.key]
        self.top()
        return {'harvest_clicks': clicks, 'ready_after': 0}, signature, target.button.cy

    def save_report(self):
        self.snapshots.append(deepcopy(self.report))


def page(key, *targets):
    return {'id': key, 'targets': list(targets)}


class IconCycleTests(unittest.TestCase):
    def test_no_icon_page_continues_to_later_stealable_page(self):
        bot = FakeIconBot([page('first'), page('second', 'A'), page('last')])
        bot.cycle()
        self.assertEqual(bot.attempts, ['A'])
        self.assertIn('last', bot.scanned_pages)
        self.assertTrue(bot.report['complete'])
        self.assertEqual(bot.report['visits'], 1)
        self.assertEqual(bot.report['harvest_clicks'], 1)

    def test_reordered_pages_after_return_still_find_remaining_icons(self):
        class ReorderedBot(FakeIconBot):
            def visit_icon(self, target):
                result = super().visit_icon(target)
                if target.key == 'A':
                    self.pages = [page('reordered-top', 'C'), page('reordered-bottom', 'B')]
                return result
        bot = ReorderedBot([page('top', 'A', 'B'), page('bottom', 'C')])
        bot.cycle()
        self.assertEqual(bot.attempts, ['A', 'C', 'B'])
        self.assertTrue(bot.report['complete'])

    def test_persistent_same_row_stops_after_three_visits_incomplete(self):
        bot = FakeIconBot([page('top', 'stale')], stale=('stale',))
        with self.assertRaisesRegex(RuntimeError, '停止避免空转'):
            bot.cycle()
        self.assertEqual(bot.attempts, ['stale'] * 3)
        self.assertEqual(bot.report['visits'], 3)
        self.assertEqual(len(bot.report['actions']), 3)
        self.assertFalse(bot.report['complete'])
        self.assertFalse(bot.snapshots[-1]['complete'])

    def test_empty_list_passes_visit_bottom_and_restart_from_top(self):
        bot = FakeIconBot([page('top'), page('middle'), page('bottom')])
        bot.cycle()
        self.assertTrue(bot.report['complete'])
        self.assertEqual(bot.attempts, [])
        self.assertEqual(bot.top_calls, 2)
        self.assertEqual(bot.scanned_pages.count('top'), 2)
        self.assertEqual(len([amount for _, amount in bot.scroll_calls if amount == -12]), 2)
        self.assertIn('bottom', bot.scanned_pages)

    def test_single_empty_page_requires_two_confirmed_passes(self):
        bot = FakeIconBot([page('only')])
        bot.cycle()
        self.assertEqual(bot.top_calls, 2)
        self.assertEqual(len(bot.scroll_calls), 6)
        self.assertEqual(len(bot.scanned_pages), 8)
        self.assertTrue(bot.report['complete'])

    def test_scroll_limit_preserves_incomplete_status(self):
        bot = FakeIconBot([page('one'), page('two'), page('three')], config={'max_pages': 1})
        with self.assertRaisesRegex(RuntimeError, '滚动上限'):
            bot.cycle()
        self.assertFalse(bot.report['complete'])
        self.assertIn('滚动上限', bot.snapshots[-1]['error'])

    def test_visit_limit_does_not_claim_complete(self):
        bot = FakeIconBot([page('one', 'A', 'B')], config={'max_friends': 1})
        with self.assertRaisesRegex(RuntimeError, '拜访次数上限'):
            bot.cycle()
        self.assertEqual(bot.attempts, ['A'])
        self.assertFalse(bot.report['complete'])

    def test_same_screen_row_on_different_page_is_not_merged(self):
        bot = FakeIconBot([page('one', 'A'), page('two', 'B')])
        bot.cycle()
        self.assertEqual(bot.attempts, ['A', 'B'])
        self.assertTrue(bot.report['complete'])

    def test_disappearing_target_is_bounded_by_operation_limit(self):
        class ChangedTargetBot(FakeIconBot):
            def visit_icon(self, target):
                return None
        bot = ChangedTargetBot([page('one', 'A')], config={'max_operations': 4})
        with self.assertRaisesRegex(RuntimeError, '操作次数上限'):
            bot.cycle()
        self.assertFalse(bot.report['complete'])
        self.assertEqual(bot.report['visits'], 0)

    def test_large_scroll_confirmation_finds_target_after_apparent_bottom(self):
        class DelayedScrollBot(FakeIconBot):
            def scroll_ocr(self, amount):
                self.scroll_calls.append((self.page, amount))
                if amount == -12:
                    self.page = min(self.page + 1, len(self.pages) - 1)
        bot = DelayedScrollBot([page('one'), page('two', 'A')])
        bot.cycle()
        self.assertEqual(bot.attempts, ['A'])
        self.assertTrue(bot.report['complete'])

    def test_initial_navigation_failure_is_saved(self):
        class BadTopBot(FakeIconBot):
            def top(self):
                raise RuntimeError('无法打开列表')
        bot = BadTopBot([page('one')])
        with self.assertRaisesRegex(RuntimeError, '无法打开列表'):
            bot.cycle()
        self.assertFalse(bot.snapshots[-1]['complete'])
        self.assertIn('无法打开列表', bot.snapshots[-1]['error'])


@unittest.skipUnless(ATTACHMENT.is_file(), 'Current user attachment is not available')
class AttachmentIconTests(unittest.TestCase):
    def setUp(self):
        self.image = cv2.imread(str(ATTACHMENT), cv2.IMREAD_GRAYSCALE)
        cfg = farm.settings()
        cfg['region'] = [0, 0, self.image.shape[1], self.image.shape[0]]
        self.bot = IconFriendsBot(cfg)
        self.bot.frame = self.image.copy()

    def test_three_hand_mask_icons_detected_at_actual_positions(self):
        hits = self.bot.matches('stealable')
        self.assertEqual(len(hits), 3)
        for hit, expected_y in zip(hits, [81.5, 252.5, 424.5]):
            self.assertAlmostEqual(hit[0], 88, delta=3)
            self.assertAlmostEqual(hit[1], expected_y, delta=3)

    def test_farm_worker_and_chest_are_not_steal_icons(self):
        # Keep the original canvas geometry, so templates use normal scaling.
        for start in (55, 225, 397):
            self.bot.frame[start:start + 60, 60:115] = 255
        self.assertEqual(self.bot.matches('stealable'), [])

    def test_small_worker_crop_does_not_match_tiny_rescaled_icons(self):
        self.bot.frame = self.image[48:114, 117:161]
        self.assertEqual(self.bot.matches('stealable'), [])


if __name__ == '__main__':
    unittest.main()
