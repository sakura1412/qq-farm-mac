"""Synthetic-image identity tests; no screens, OCR, or input devices."""
import unittest
from dataclasses import replace
from unittest.mock import patch

import cv2
import numpy as np

import farm
from all_friends import FriendRow, TextBox


def synthetic_row(rank_number, offset=0):
    label = TextBox('示例甲', 1057, 500 + offset, 50, 30)
    button = TextBox('拜访', 1460, 520 + offset, 60, 30)
    row = FriendRow('示例甲', button, label)
    frame = np.full((1468, 1600), 225, dtype=np.uint8)
    span = button.x - label.x
    x0, x1 = round(label.x - span * .30), round(label.x - span * .06)
    y0, y1 = round(button.cy - span * .14), round(button.cy + span * .015)
    avatar = np.random.default_rng(48).integers(0, 255, size=(y1-y0, x1-x0), dtype=np.uint8)
    frame[y0:y1, x0:x1] = avatar
    rx0, rx1 = round(label.x - span * .48), round(label.x - span * .30)
    ry0, ry1 = round(button.cy - span * .065), round(button.cy + span * .065)
    rank = np.full((ry1-ry0, rx1-rx0), 225, dtype=np.uint8)
    cv2.putText(rank, str(rank_number), (12, 36), cv2.FONT_HERSHEY_SIMPLEX,
                1.0, 30, 2, cv2.LINE_AA)
    frame[ry0:ry1, rx0:rx1] = rank
    return frame, row


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.patch_cv2 = patch.object(farm, 'cv2', cv2, create=True)
        self.patch_cv2.start()
        # Avoid any constructor that reads screen capture templates.
        self.bot = farm.AllFriendsBot.__new__(farm.AllFriendsBot)
        self.bot.identities = []

    def tearDown(self):
        self.patch_cv2.stop()

    def identify(self, rank_number, offset=0):
        self.bot.frame, row = synthetic_row(rank_number, offset)
        return self.bot.identity(row)

    def test_same_name_avatar_and_rank_deduplicate(self):
        first = self.identify(12)
        self.assertEqual(self.identify(12), first)
        self.assertEqual(len(self.bot.identities), 1)

    def test_same_name_and_avatar_but_different_rank_stay_separate(self):
        first = self.identify(12)
        second = self.identify(13)
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.bot.identities), 2)

    def test_scrolling_row_to_another_position_preserves_identity(self):
        first = self.identify(12)
        self.assertEqual(self.identify(12, offset=170), first)
        self.assertEqual(len(self.bot.identities), 1)

    def test_one_pixel_ocr_vertical_jitter_preserves_identity(self):
        self.bot.frame, row = synthetic_row(12)
        first = self.bot.identity(row)
        shifted = replace(row, label=replace(row.label, y=row.label.y + 1),
                          button=replace(row.button, y=row.button.y + 1))
        self.assertEqual(self.bot.identity(shifted), first)
        self.assertEqual(len(self.bot.identities), 1)


if __name__ == '__main__':
    unittest.main()
