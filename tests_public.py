"""Portable paths and login gate tests, without screenshots or live clicks."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import public_launcher as launcher
import runtime_paths as paths


class RuntimePathsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='QQ Farm 分享 测试 ')
        folder = Path(self.temp.name).resolve()
        self.source = folder / '共享文件'
        self.data = folder / '私人数据'
        self.source.mkdir()
        (self.source / 'templates').mkdir()
        self.defaults = {'region': [31, 94, 803, 734], 'calibrated': True,
                         'threshold': .93, 'interval_seconds': 180}
        (self.source / 'config.json').write_text(json.dumps(self.defaults), encoding='utf-8')
        self.root_patch = patch.object(paths, 'ROOT', self.source)
        self.lock_dir = folder / '统一锁'
        self.env_patch = patch.dict(os.environ, {'QQFARM_DATA_DIR': str(self.data),
                                                'QQFARM_LOCK_DIR': str(self.lock_dir)})
        self.root_patch.start()
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.root_patch.stop()
        self.temp.cleanup()

    def test_fresh_user_discovers_own_window_without_changing_shared_config(self):
        cfg = paths.load_settings()
        self.assertIsNone(cfg['region'])
        self.assertFalse(cfg['calibrated'])
        self.assertEqual(json.loads((self.source / 'config.json').read_text()), self.defaults)
        self.assertTrue((self.data / 'config.json').is_file())

    def test_existing_preferences_preserved_and_missing_defaults_merged(self):
        self.data.mkdir()
        (self.data / 'config.json').write_text('{"interval_seconds": 90, "region": null}')
        cfg = paths.load_settings()
        self.assertEqual(cfg['interval_seconds'], 90)
        self.assertEqual(cfg['threshold'], .93)
        self.assertIsNone(cfg['region'])
        paths.save_settings(cfg)
        self.assertEqual(paths.load_settings(), cfg)

    def test_legacy_no_environment_still_uses_project_directory(self):
        del os.environ['QQFARM_DATA_DIR']
        self.assertEqual(paths.data_dir(), self.source)
        self.assertEqual(paths.load_settings(), self.defaults)

    def test_two_users_do_not_share_configs(self):
        first = paths.load_settings()
        first['interval_seconds'] = 60
        paths.save_settings(first)
        second_dir = self.data.parent / '另一位用户'
        with patch.dict(os.environ, {'QQFARM_DATA_DIR': str(second_dir)}):
            self.assertEqual(paths.load_settings()['interval_seconds'], 180)
        self.assertEqual(paths.load_settings()['interval_seconds'], 60)

    def test_template_override_preserves_unmodified_shared_variants(self):
        for name in ('ready.png', 'ready1.png'):
            (self.source / 'templates' / name).write_bytes(b'common')
        override = self.data / 'templates'
        override.mkdir(parents=True)
        (override / 'ready1.png').write_bytes(b'local')
        self.assertEqual(paths.template_path('ready'), self.source / 'templates' / 'ready.png')
        self.assertEqual(paths.template_paths('ready*.png'),
                         [self.source / 'templates' / 'ready.png', override / 'ready1.png'])

    def test_lock_is_reused_inside_same_process_but_blocks_another_process(self):
        code = ('from runtime_paths import monitoring_lock\n'
                'try:\n'
                ' with monitoring_lock(): print("entered")\n'
                'except RuntimeError: raise SystemExit(7)\n')
        with paths.monitoring_lock():
            with paths.monitoring_lock():
                result = subprocess.run([sys.executable, '-c', code],
                                        cwd=str(Path(__file__).parent), env=os.environ.copy(),
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        result = subprocess.run([sys.executable, '-c', code], cwd=str(Path(__file__).parent),
                                env=os.environ.copy(), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_lock_released_after_exception(self):
        with self.assertRaisesRegex(RuntimeError, 'test failure'):
            with paths.monitoring_lock():
                raise RuntimeError('test failure')
        with paths.monitoring_lock():
            self.assertIsNotNone(paths._active_lock)

    def test_legacy_and_portable_launchers_share_one_lock(self):
        with paths.monitoring_lock():
            code = ('from runtime_paths import monitoring_lock\n'
                    'try:\n'
                    ' with monitoring_lock(): print("entered")\n'
                    'except RuntimeError: raise SystemExit(7)\n')
            env = os.environ.copy()
            env.pop('QQFARM_DATA_DIR')
            result = subprocess.run([sys.executable, '-c', code], cwd=str(Path(__file__).parent),
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 7, result.stdout + result.stderr)


class LoginGateTests(unittest.TestCase):
    def test_quitting_does_not_verify_or_request_permissions(self):
        verifier = Mock()
        checker = Mock()
        self.assertFalse(launcher.prepare_login(reader=lambda _: 'q', opener=Mock(),
                                               verifier=verifier, permission_checker=checker, sleeper=Mock()))
        verifier.assert_not_called()
        checker.assert_not_called()

    def test_missing_permissions_cannot_start_game_verification(self):
        verifier = Mock()
        self.assertFalse(launcher.prepare_login(reader=lambda _: '', opener=Mock(), verifier=verifier,
                                               permission_checker=lambda: False, sleeper=Mock()))
        verifier.assert_not_called()

    def test_invalid_page_retries_before_accepting_login(self):
        verifier = Mock(side_effect=[RuntimeError('未登录'), 'friends'])
        sleep = Mock()
        self.assertTrue(launcher.prepare_login(reader=lambda _: '', opener=Mock(), verifier=verifier,
                                              permission_checker=lambda: True, sleeper=sleep))
        self.assertEqual(verifier.call_count, 2)
        self.assertEqual(sleep.call_count, 2)

    def test_permission_success_does_not_open_settings(self):
        opener = Mock()
        reader = Mock()
        self.assertTrue(launcher.check_permissions({'屏幕录制': True, '辅助功能': True}, reader, opener))
        opener.assert_not_called()
        reader.assert_not_called()

    def test_missing_permission_returns_without_implicitly_granting_it(self):
        opener = Mock()
        self.assertFalse(launcher.check_permissions({'屏幕录制': True, '辅助功能': False}, lambda _: '', opener))
        opener.assert_called_once()
        self.assertEqual(opener.call_args.args[0], ['open', '-b', 'com.apple.systempreferences'])

    def test_wechat_not_installed_has_actionable_message(self):
        opener = Mock(return_value=SimpleNamespace(returncode=1))
        with self.assertRaisesRegex(RuntimeError, '安装微信'):
            launcher.open_wechat(opener)
        self.assertEqual(opener.call_args.args[0], ['open', '-b', 'com.tencent.xinWeChat'])

    def test_unrecognized_or_login_page_is_rejected_before_run(self):
        with patch.object(launcher.farm, 'require_permissions'), patch.object(launcher.farm, 'dependencies'), \
             patch.object(launcher.farm, 'settings', return_value={}), \
             patch.object(launcher.farm, 'IconFriendsBot') as bot:
            bot.return_value.read_page.return_value = 'unknown'
            with self.assertRaisesRegex(RuntimeError, '未确认游戏'):
                launcher.verify_game()
            bot.return_value.harvest.assert_not_called()

    def test_diagnose_never_starts_wechat_or_monitoring(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {'QQFARM_DATA_DIR': directory, 'QQFARM_LOCK_DIR': directory}), \
             patch.object(launcher.sys, 'platform', 'darwin'), \
             patch.object(launcher, 'prepare_login') as login, \
             patch.object(launcher.farm, 'main') as monitor, \
             patch.object(launcher.farm, 'doctor', return_value=True):
            self.assertEqual(launcher.main(['--diagnose']), 0)
            login.assert_not_called()
            monitor.assert_not_called()

    def test_successful_login_forwards_requested_mode_and_run_limit(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {'QQFARM_DATA_DIR': directory, 'QQFARM_LOCK_DIR': directory}), \
             patch.object(launcher.sys, 'platform', 'darwin'), \
             patch.object(launcher, 'prepare_login', return_value=True), \
             patch.object(launcher.farm, 'main') as monitor:
            self.assertEqual(launcher.main(['--current', '--once']), 0)
            monitor.assert_called_once_with(['run', '--current', '--once'])


FARM_ATTACHMENT = Path(os.environ.get('QQFARM_TEST_FARM_IMAGE', str(Path(__file__).parent / 'tests' / 'fixtures' / 'farm.png')))


@unittest.skipUnless(FARM_ATTACHMENT.is_file(), 'Original game image not available')
class ScreenScaleTests(unittest.TestCase):
    def test_non_retina_ready_marks_can_trigger_one_click_harvest(self):
        import cv2
        import numpy as np
        import farm
        farm.cv2, farm.np = cv2, np
        image = cv2.imread(str(FARM_ATTACHMENT), cv2.IMREAD_GRAYSCALE)
        cfg = farm.settings()
        cfg['region'] = [0, 0, image.shape[1], image.shape[0]]
        bot = farm.Bot(cfg, current=True)
        # Template detail becomes about 13 px wide on a 1x display.
        bot.frame = cv2.resize(image, None, fx=.5, fy=.5, interpolation=cv2.INTER_AREA)
        self.assertTrue(bot.matches('ready'), 'At least one mark must trigger one-click harvest')
        self.assertTrue(bot.matches('farm'))
        self.assertTrue(bot.matches('harvest'))


if __name__ == '__main__':
    unittest.main()
