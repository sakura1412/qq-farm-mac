"""Regression tests for OCR navigation decisions; no screenshots or live clicks."""
import unittest

from all_friends import BottomTracker, TextBox, exact, friend_rows, page_kind


HEIGHT = 1468


def list_boxes():
    return [
        TextBox('同玩好友', 910, 270, 140, 30),
        TextBox('微信好友', 1230, 270, 140, 30),
        TextBox('拜访', 1460, 520, 60, 30),
        TextBox('拜访', 1460, 690, 60, 30),
        TextBox('拜访', 1460, 860, 60, 30),
        TextBox('示例甲', 1057, 500, 50, 30),
        TextBox('示例乙', 1057, 670, 50, 30),
        TextBox('示例丙', 1057, 840, 50, 30),
    ]


class ExactTests(unittest.TestCase):
    def test_whitespace_is_normalized_but_substrings_are_rejected(self):
        boxes = [TextBox('拜 访', 0, 0, 40, 20),
                 TextBox('欢迎拜访', 0, 40, 80, 20)]
        self.assertEqual(exact(boxes, '拜访'), boxes[:1])

    def test_low_confidence_text_is_rejected(self):
        boxes = [TextBox('拜访', 0, 0, 40, 20, .44),
                 TextBox('拜访', 0, 40, 40, 20, .45)]
        self.assertEqual(exact(boxes, '拜访'), boxes[1:])


class FriendRowsTests(unittest.TestCase):
    def test_buttons_belong_to_correct_nickname_in_visual_order(self):
        boxes = list_boxes()
        rows = friend_rows(list(reversed(boxes)), HEIGHT)
        self.assertEqual([row.name for row in rows], ['示例甲', '示例乙', '示例丙'])
        self.assertEqual([row.button.y for row in rows], [520, 690, 860])
        self.assertEqual([row.label.y for row in rows], [500, 670, 840])

    def test_numeric_coin_values_and_sequence_number_are_filtered(self):
        boxes = list_boxes() + [
            TextBox('1', 935, 500, 15, 30),
            TextBox('100,200', 1190, 490, 90, 30),
            TextBox('23.5万', 1190, 660, 90, 30),
            TextBox('互动', 1100, 830, 50, 30),
        ]
        self.assertEqual([row.name for row in friend_rows(boxes, HEIGHT)],
                         ['示例甲', '示例乙', '示例丙'])

    def test_missing_name_is_an_error_instead_of_silently_skipping(self):
        boxes = [box for box in list_boxes() if box.text != '示例乙']
        with self.assertRaisesRegex(RuntimeError, '昵称'):
            friend_rows(boxes, HEIGHT)

    def test_numeric_nicknames_are_allowed_in_name_position(self):
        for nickname in ('123456', '11.'):
            boxes = [TextBox(nickname, b.x, b.y, b.w, b.h) if b.text == '示例甲' else b
                     for b in list_boxes()]
            self.assertEqual(friend_rows(boxes, HEIGHT)[0].name, nickname)

    def test_missing_list_marker_is_an_error(self):
        boxes = [box for box in list_boxes() if box.text != '同玩好友']
        with self.assertRaisesRegex(RuntimeError, '同玩好友'):
            friend_rows(boxes, HEIGHT)

    def test_footer_row_is_omitted_until_scrolled_into_full_view(self):
        boxes = list_boxes() + [TextBox('拜访', 1460, 1300, 60, 30)]
        # Its absent nickname is intentional: that part of the row is hidden.
        self.assertEqual(len(friend_rows(boxes, HEIGHT)), 3)

    def test_two_people_with_same_nickname_remain_two_rows(self):
        boxes = [TextBox('示例甲', box.x, box.y, box.w, box.h)
                 if box.text == '示例乙' else box for box in list_boxes()]
        rows = friend_rows(boxes, HEIGHT)
        self.assertEqual([row.name for row in rows], ['示例甲', '示例甲', '示例丙'])
        self.assertNotEqual(rows[0].button, rows[1].button)


class BottomTrackerTests(unittest.TestCase):
    def test_bottom_requires_two_unchanged_scroll_results(self):
        tracker = BottomTracker()
        signature = ['示例甲', '示例乙', '示例丙']
        self.assertFalse(tracker.update(signature))
        self.assertFalse(tracker.update(signature))
        self.assertTrue(tracker.update(signature))

    def test_order_change_resets_unchanged_scroll_count(self):
        tracker = BottomTracker()
        self.assertFalse(tracker.update(['示例甲', '示例乙']))
        self.assertFalse(tracker.update(['示例甲', '示例乙']))
        self.assertFalse(tracker.update(['示例乙', '示例甲']))
        self.assertFalse(tracker.update(['示例乙', '示例甲']))
        self.assertTrue(tracker.update(['示例乙', '示例甲']))

    def test_empty_ocr_results_never_claim_completion(self):
        tracker = BottomTracker()
        with self.assertRaisesRegex(RuntimeError, '识别失败'):
            tracker.update([])


class PageKindTests(unittest.TestCase):
    def test_friend_dialog_requires_both_tabs(self):
        self.assertEqual(page_kind(list_boxes(), HEIGHT), 'friends')
        boxes = [box for box in list_boxes() if box.text != '微信好友']
        self.assertEqual(page_kind(boxes, HEIGHT), 'unknown')

    def test_farm_marker_is_respected(self):
        self.assertEqual(page_kind([], HEIGHT, farm=True), 'farm')
        # The dialog can overlay the farm: dialog recognition takes precedence.
        self.assertEqual(page_kind(list_boxes(), HEIGHT, farm=True), 'friends')

    def test_home_requires_both_footer_labels(self):
        boxes = [TextBox('仓库', 100, 1300, 80, 30),
                 TextBox('好友', 300, 1300, 80, 30)]
        self.assertEqual(page_kind(boxes, HEIGHT), 'home')
        self.assertEqual(page_kind(boxes[:1], HEIGHT), 'unknown')

    def test_top_text_does_not_misclassify_home(self):
        boxes = [TextBox('仓库', 100, 100, 80, 30),
                 TextBox('好友', 300, 100, 80, 30)]
        self.assertEqual(page_kind(boxes, HEIGHT), 'unknown')


if __name__ == '__main__':
    unittest.main()
