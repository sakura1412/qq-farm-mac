"""Local visual automation; run capture/check before enabling live clicks."""
import argparse
import json
import logging
from pathlib import Path
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from runtime_paths import (data_dir, data_path, load_settings, save_settings,
                           template_path, template_paths, monitoring_lock)

ROOT = Path(__file__).resolve().parent


def permission_status(quartz=None, accessibility=None):
    if quartz is None:
        import Quartz as quartz
    if accessibility is None:
        import ctypes
        # AX belongs to ApplicationServices, not the PyObjC Quartz module.
        services = ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        services.AXIsProcessTrusted.argtypes = []
        services.AXIsProcessTrusted.restype = ctypes.c_bool
        accessibility = bool(services.AXIsProcessTrusted())
    return {
        '屏幕录制': bool(quartz.CGPreflightScreenCaptureAccess()),
        '辅助功能': bool(accessibility),
    }


def require_permissions(clicks=False):
    status = permission_status()
    needed = ['屏幕录制'] + (['辅助功能'] if clicks else [])
    missing = [name for name in needed if not status[name]]
    if missing:
        raise RuntimeError('当前运行进程缺少 ' + '、'.join(missing) +
                           ' 权限。请在系统设置→隐私与安全性中给实际运行终端开启权限，'
                           '然后用 Command+Q 完全退出终端并重新打开。')


def doctor():
    import importlib.util
    import sys
    print('Python:', sys.executable, flush=True)
    print('项目目录:', ROOT, flush=True)
    print('运行数据目录:', data_dir(), flush=True)
    missing = [name for name in ('cv2', 'numpy', 'pyautogui', 'PIL', 'Quartz', 'Vision')
               if importlib.util.find_spec(name) is None]
    print('依赖:', '缺少 ' + ', '.join(missing) if missing else '已安装', flush=True)
    if 'Quartz' in missing:
        return False
    status = permission_status()
    for name, granted in status.items():
        print(name + ': ' + ('已授权' if granted else '未授权／当前进程尚未生效'), flush=True)
    if not all(status.values()):
        print('请给运行脚本的终端开启上面的两个权限，Command+Q 退出终端后重开。', flush=True)
    elif not settings()['region']:
        try:
            print('已找到农场窗口:', auto_region(), flush=True)
        except RuntimeError as exc:
            print('窗口提示:', exc, flush=True)
    return not missing and all(status.values())


def settings():
    return load_settings()


def dependencies():
    global cv2, np, pg
    import cv2
    import numpy as np
    import pyautogui as pg
    pg.FAILSAFE = True
    pg.PAUSE = .3


def rectangle(prompt):
    input(prompt + '：将鼠标移到左上角，回到终端按回车（不要移动鼠标）')
    x, y = pg.position()
    input('将鼠标移到右下角，回到终端按回车（不要移动鼠标）')
    right, bottom = pg.position()
    if right <= x or bottom <= y:
        raise RuntimeError('矩形必须由左上到右下选取')
    return [x, y, right - x, bottom - y]


def capture_region(region):
    # macOS Retina screenshots may use physical pixels; input uses logical points.
    shot = pg.screenshot()
    width, height = pg.size()
    sx, sy = shot.width / width, shot.height / height
    x, y, w, h = region
    if min(x, y) < 0 or x + w > width or y + h > height:
        raise RuntimeError('游戏区域不在主显示器内，请重新校准')
    crop = shot.crop((round(x * sx), round(y * sy),
                      round((x + w) * sx), round((y + h) * sy)))
    return crop, sx, sy


def find_matches(frame, template, threshold, limit=30):
    fh, fw = frame.shape[:2]
    th, tw = template.shape[:2]
    if th > fh or tw > fw or template.std() < 3:
        return []
    scores = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
    matches = []
    for _ in range(limit):
        _, score, _, (x, y) = cv2.minMaxLoc(scores)
        if score < threshold:
            break
        matches.append((x + tw / 2, y + th / 2, score))
        scores[max(0, y - th // 2):min(scores.shape[0], y + th // 2 + 1),
               max(0, x - tw // 2):min(scores.shape[1], x + tw // 2 + 1)] = -1
    return sorted(matches, key=lambda m: m[1])


def auto_region():
    import Quartz
    windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly,
                                               Quartz.kCGNullWindowID)
    candidates = [w for w in windows if 'QQ经典农场' in w.get('kCGWindowName', '')
                  and w.get('kCGWindowLayer', 0) == 0]
    if len(candidates) != 1:
        raise RuntimeError('未找到唯一 QQ经典农场 窗口，请先打开游戏或用 region 手动选取')
    bounds = candidates[0]['kCGWindowBounds']
    return [int(bounds[k]) for k in ('X', 'Y', 'Width', 'Height')]


def scaled_matches(frame, template, threshold, minimum_size=16):
    # Attachments contain game pixels. Account for window chrome and Retina scale.
    base = frame.shape[0] / 1382
    scales = sorted(set([1.0] + [base * n for n in (.92, .94, .96, .98, 1.0, 1.02)]))
    best = []
    for scale in scales:
        size = (round(template.shape[1] * scale), round(template.shape[0] * scale))
        # Very small manual crops must not turn detailed icons into a few pixels.
        if min(size) < minimum_size:
            continue
        resized = cv2.resize(template, size, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
        matches = find_matches(frame, resized, threshold)
        if matches and (not best or max(m[2] for m in matches) > max(m[2] for m in best)):
            best = matches
    return best


class Bot:
    def __init__(self, cfg, current=False):
        self.cfg = cfg
        self.region = cfg['region'] or auto_region()
        self.current = current
        self.templates = {}
        names = ['farm', 'harvest', 'ready', 'return_home']
        for name in names:
            path = template_path(name)
            template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if template is None:
                raise RuntimeError(f'缺少模板：{path.name}')
            self.templates[name] = template
        self.ready_variants = [self.templates['ready']]
        for path in template_paths('ready[0-9]*.png'):
            self.ready_variants.append(cv2.imread(str(path), cv2.IMREAD_GRAYSCALE))

    def observe(self):
        if self.cfg['region'] is None:
            self.region = auto_region()
        shot, self.sx, self.sy = capture_region(self.region)
        self.frame = cv2.cvtColor(np.array(shot), cv2.COLOR_RGB2GRAY)

    def matches(self, name):
        if name == 'ready':
            hits = []
            radius = max(10, 12 * self.frame.shape[0] / 1382)
            for template in self.ready_variants:
                for hit in scaled_matches(self.frame, template, self.cfg['threshold'], minimum_size=8):
                    if not any((hit[0]-old[0])**2 + (hit[1]-old[1])**2 < radius**2 for old in hits):
                        hits.append(hit)
            return hits
        if name in ('farm', 'harvest', 'ready', 'return_home'):
            return scaled_matches(self.frame, self.templates[name], self.cfg['threshold'])
        return find_matches(self.frame, self.templates[name], self.cfg['threshold'])

    def require(self, page):
        self.observe()
        if not self.matches(page):
            raise RuntimeError(f'未确认 {page} 页面；可能登录失效、窗口被遮挡或游戏界面变化，已停止')

    def click(self, name, page, index=0):
        self.require(page)
        matches = self.matches(name)
        if len(matches) <= index:
            return False
        x, y, score = matches[index]
        logging.info('点击 %s，匹配 %.3f', name, score)
        pg.click(self.region[0] + x / self.sx, self.region[1] + y / self.sy)
        time.sleep(self.cfg['settle_seconds'])
        return True

    def harvest(self):
        self.require('farm')
        clicks = 0
        for _ in range(self.cfg['max_harvest_clicks']):
            self.require('farm')
            if not self.matches('ready'):
                break
            if not self.click('harvest', 'farm'):
                raise RuntimeError('有可摘标记，但找不到一键偷菜按钮')
            clicks += 1
        self.require('farm')
        remaining = len(self.matches('ready'))
        logging.info('采摘操作 %d 次，剩余可摘标记 %d 个', clicks, remaining)
        if clicks and remaining:
            raise RuntimeError('点击后仍有可摘标记，未确认采摘成功，停止以便检查')
        return {'harvest_clicks': clicks, 'ready_after': remaining,
                'state': '已采摘' if clicks else '未识别到可摘标记'}

    def cycle(self):
        return self.harvest()


class AllFriendsBot(Bot):
    """Visit every list entry, then repeat a full scan to confirm coverage."""
    def __init__(self, cfg):
        super().__init__(cfg, current=True)
        from all_friends import recognize, page_kind, friend_rows, exact
        self.ocr = recognize
        self.page_kind = page_kind
        self.friend_rows = friend_rows
        self.exact = exact
        self.identities = []
        self.report = {'mode': 'all_friends', 'cycle': 0, 'complete': False, 'friends': {}}

    def read_page(self):
        self.observe()
        self.words = self.ocr(self.frame)
        # Do not operate through login/reconnect dialogs covering game controls.
        joined = ''.join(b.text for b in self.words)
        if any(word in joined for word in ('重新登录', '重新连接', '账号安全', '验证码')):
            raise RuntimeError('出现登录或连接提示，停止等待人工处理')
        return self.page_kind(self.words, self.frame.shape[0], farm=bool(self.matches('farm')))

    def click_pixel(self, x, y):
        pg.click(self.region[0] + x / self.sx, self.region[1] + y / self.sy)
        time.sleep(self.cfg['settle_seconds'])

    def ensure_list(self):
        kind = self.read_page()
        if kind == 'farm':
            if not self.click('return_home', 'farm'):
                raise RuntimeError('找不到回家按钮')
            kind = self.read_page()
        if kind == 'home':
            buttons = [b for b in self.exact(self.words, '好友') if b.cy > self.frame.shape[0] * .72]
            if len(buttons) != 1:
                raise RuntimeError('自家农场好友入口识别不唯一')
            self.click_pixel(buttons[0].cx, buttons[0].cy)
            kind = self.read_page()
        if kind != 'friends':
            raise RuntimeError('未确认好友列表，请打开游戏里的同玩好友列表再启动')
        tabs = self.exact(self.words, '同玩好友')
        if len(tabs) != 1:
            raise RuntimeError('同玩好友选项卡识别不唯一')
        self.click_pixel(tabs[0].cx, tabs[0].cy)
        if self.read_page() != 'friends':
            raise RuntimeError('无法确认已打开同玩好友列表')

    def scroll_ocr(self, amount):
        if self.read_page() != 'friends':
            raise RuntimeError('滚动前无法确认好友列表')
        buttons = self.exact(self.words, '拜访')
        if not buttons:
            raise RuntimeError('未识别到拜访按钮，不能定位列表滚动区')
        button = buttons[len(buttons)//2]
        pg.moveTo(self.region[0] + button.cx / self.sx, self.region[1] + button.cy / self.sy)
        pg.scroll(amount)
        time.sleep(self.cfg['settle_seconds'])

    def top(self):
        self.ensure_list()
        self.scroll_ocr(100)

    def identity(self, row):
        import difflib
        # Stable central avatar pixels; exclude level badges, coins and steal icon.
        span = row.button.x - row.label.x
        x0, x1 = round(row.label.x - span * .30), round(row.label.x - span * .06)
        y0, y1 = round(row.button.cy - span * .14), round(row.button.cy + span * .015)
        if span <= 0 or x0 < 0 or y0 < 0 or x1 > self.frame.shape[1] or y1 > self.frame.shape[0]:
            raise RuntimeError('好友头像区域无法定位，停止避免漏访')
        avatar = cv2.resize(self.frame[y0:y1, x0:x1], (64, 48), interpolation=cv2.INTER_AREA)
        # Rank number is stable across the interaction sort and distinguishes
        # identical nicknames/profile photos belonging to different friends.
        rx0, rx1 = round(row.label.x - span * .48), round(row.label.x - span * .30)
        ry0, ry1 = round(row.button.cy - span * .065), round(row.button.cy + span * .065)
        if rx0 < 0 or ry0 < 0:
            raise RuntimeError('无法读取好友序号区域')
        rank = cv2.resize(self.frame[ry0:ry1, rx0:rx1], (64, 48), interpolation=cv2.INTER_AREA)
        for known in self.identities:
            # OCR boxes jitter slightly between frames; align within two pixels
            # without lowering the rank threshold and merging different numbers.
            similarity = float(cv2.minMaxLoc(cv2.matchTemplate(
                avatar, known['avatar'][2:-2, 2:-2], cv2.TM_CCOEFF_NORMED))[1])
            rank_similarity = float(cv2.minMaxLoc(cv2.matchTemplate(
                rank, known['rank'][2:-2, 2:-2], cv2.TM_CCOEFF_NORMED))[1])
            same_name = row.name == known['name']
            close_name = difflib.SequenceMatcher(None, row.name, known['name']).ratio() >= .85
            if rank_similarity >= .97 and ((same_name and similarity >= .9) or (close_name and similarity >= .98)):
                return known['id']
        key = str(len(self.identities) + 1)
        self.identities.append({'id': key, 'name': row.name, 'avatar': avatar, 'rank': rank})
        return key

    def rows(self):
        if self.read_page() != 'friends':
            raise RuntimeError('无法确认同玩好友列表')
        rows = self.friend_rows(self.words, self.frame.shape[0])
        if not rows:
            raise RuntimeError('未读取到完整好友行，不能判定所有好友已巡查')
        return [(self.identity(row), row) for row in rows]

    def save_report(self):
        path = data_path('friends-status.json')
        temporary = data_path('friends-status.tmp')
        temporary.write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)

    def visit(self, key, row):
        # Read again immediately before input; list can reorder after interactions.
        fresh = self.rows()
        matching = [(k, r) for k, r in fresh if k == key]
        if len(matching) != 1:
            logging.info('好友列表已变化，重新从顶部定位')
            return False
        row = matching[0][1]
        logging.info('检查好友：%s', row.name)
        self.click_pixel(row.button.cx, row.button.cy)
        result = self.harvest()
        result.update(name=row.name, checked_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat())
        self.report['friends'][key] = result
        self.save_report()
        if not self.click('return_home', 'farm'):
            raise RuntimeError('采摘后找不到回家按钮')
        self.top()
        return True

    def cycle(self):
        from all_friends import BottomTracker
        self.report['cycle'] += 1
        self.report.update(complete=False, checked_this_cycle=0, error=None)
        visited = set()
        quiet_passes = 0
        operations = 0
        try:
            self.save_report()
            self.top()
            # One discovery pass, followed by two full passes with no new friends.
            for _ in range(self.cfg.get('max_verification_passes', 6)):
                added = 0
                pages = 0
                bottom = BottomTracker()
                while pages < self.cfg['max_pages']:
                    operations += 1
                    if operations > self.cfg.get('max_operations', 10000):
                        raise RuntimeError('列表反复变化，达到操作上限；巡查未完成')
                    pairs = self.rows()
                    next_friend = next(((k, r) for k, r in pairs if k not in visited), None)
                    if next_friend:
                        if len(visited) >= self.cfg.get('max_friends', 500):
                            raise RuntimeError('达到好友数量上限，巡查未完成')
                        key, row = next_friend
                        if self.visit(key, row):
                            visited.add(key)
                            added += 1
                            self.report['checked_this_cycle'] = len(visited)
                            self.save_report()
                        # Returning through home restarts list traversal from the top.
                        pages = 0
                        bottom = BottomTracker()
                        continue
                    if bottom.update([k for k, _ in pairs]):
                        break
                    self.scroll_ocr(self.cfg.get('scroll_amount', -3))
                    pages += 1
                else:
                    raise RuntimeError('达到列表滚动上限，尚未确认到底；巡查未完成')
                quiet_passes = quiet_passes + 1 if added == 0 else 0
                if quiet_passes >= 2:
                    self.report.update(complete=True, checked_this_cycle=len(visited),
                                       finished_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat())
                    self.save_report()
                    logging.info('全好友巡查完成：本轮检查 %d 位好友', len(visited))
                    return
                self.top()
            raise RuntimeError('多次复核仍有新增或变化好友，本轮覆盖未确认')
        except Exception as exc:
            self.report.update(complete=False, checked_this_cycle=len(visited), error=str(exc))
            self.save_report()
            raise


class IconFriendsBot(AllFriendsBot):
    """Scan all pages for stealable icons without parsing any friend names."""
    def __init__(self, cfg):
        super().__init__(cfg)
        from all_friends import icon_rows
        self.icon_rows = icon_rows
        self.stealable_variants = []
        for path in template_paths('stealable*.png'):
            template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if template is not None:
                self.stealable_variants.append(template)
        if not self.stealable_variants:
            raise RuntimeError('缺少可偷菜图标模板')
        self.report = {'mode': 'stealable_icons', 'cycle': 0, 'complete': False, 'actions': []}

    def matches(self, name):
        if name != 'stealable':
            return super().matches(name)
        hits = []
        radius = max(12, self.frame.shape[0] * .012)
        for template in self.stealable_variants:
            for hit in scaled_matches(self.frame, template, self.cfg.get('stealable_threshold', .92)):
                if not any((hit[0]-old[0])**2 + (hit[1]-old[1])**2 < radius**2 for old in hits):
                    hits.append(hit)
        return sorted(hits, key=lambda hit: hit[1])

    def content_signature(self):
        tabs = self.exact(self.words, '同玩好友')
        buttons = self.exact(self.words, '拜访')
        if len(tabs) != 1 or not buttons:
            raise RuntimeError('列表控件缺失，不能确认滚动覆盖')
        tab = tabs[0]
        span = max(b.cx for b in buttons) - tab.x
        # Ranking/profile-photo column: no steal icon, coins, or animated pets.
        x0, x1 = round(tab.x - span * .16), round(tab.x + span * .25)
        y0 = round(tab.y + tab.h + self.frame.shape[0] * .09)
        y1 = round(self.frame.shape[0] * .86)
        if x0 < 0 or x1 > self.frame.shape[1] or y1 <= y0:
            raise RuntimeError('无法定位好友列表内容区域')
        image = self.frame[y0:y1, x0:x1]
        if image.size == 0 or float(image.std()) < 3:
            raise RuntimeError('列表内容区域为空，停止避免误报到底')
        return cv2.resize(image, (96, 256), interpolation=cv2.INTER_AREA)

    @staticmethod
    def signatures_equal(first, second):
        return first is not None and second is not None and first.shape == second.shape and (
            float(np.mean(cv2.absdiff(first, second))) < 2.0)

    def scan_list(self):
        if self.read_page() != 'friends':
            raise RuntimeError('未确认同玩好友列表')
        targets = self.icon_rows(self.words, self.frame.shape[0], self.matches('stealable'))
        return targets, self.content_signature()

    def scroll_ocr(self, amount):
        if self.read_page() != 'friends':
            raise RuntimeError('滚动前无法确认好友列表')
        buttons = self.exact(self.words, '拜访')
        tabs = self.exact(self.words, '同玩好友')
        if not buttons or len(tabs) != 1:
            raise RuntimeError('缺少列表控件，无法滚动')
        button = buttons[len(buttons)//2]
        # Scroll over the card background, rather than over its visit button.
        x = button.cx - (button.cx - tabs[0].x) * .30
        pg.moveTo(self.region[0] + x / self.sx, self.region[1] + button.cy / self.sy)
        pg.scroll(amount)
        time.sleep(self.cfg['settle_seconds'])

    def visit_icon(self, target):
        fresh, signature = self.scan_list()
        if not fresh:
            logging.info('可偷菜标识已变化，重新扫描列表')
            return None
        # Select from the freshly confirmed icon/button pairs, never old coords.
        target = fresh[0]
        logging.info('发现可偷菜标识，拜访对应行，标识匹配 %.3f', target.icon[2])
        self.click_pixel(target.button.cx, target.button.cy)
        result = self.harvest()
        result.update(checked_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat())
        if not self.click('return_home', 'farm'):
            raise RuntimeError('采摘后找不到回家按钮')
        self.top()
        return result, signature, target.button.cy

    def cycle(self):
        self.report['cycle'] += 1
        self.report.update(complete=False, actions=[], list_frames_scanned=0, visits=0,
                           harvest_clicks=0, error=None)
        previous = None
        unchanged = 0
        quiet_passes = 0
        pages = 0
        operations = 0
        last_failure = None
        no_progress = 0
        try:
            self.save_report()
            self.top()
            while operations < self.cfg.get('max_operations', 10000):
                operations += 1
                targets, signature = self.scan_list()
                self.report['list_frames_scanned'] += 1
                if targets:
                    if self.report['visits'] >= self.cfg.get('max_friends', 500):
                        raise RuntimeError('达到本轮拜访次数上限，巡查未完成')
                    attempted = self.visit_icon(targets[0])
                    if attempted is not None:
                        result, before, row_y = attempted
                        self.report['actions'].append(result)
                        self.report['visits'] += 1
                        self.report['harvest_clicks'] += result['harvest_clicks']
                        if not result['harvest_clicks']:
                            same = last_failure is not None and self.signatures_equal(before, last_failure[0]) and (
                                abs(row_y-last_failure[1]) < self.cfg.get('row_retry_tolerance', 20))
                            no_progress = no_progress + 1 if same else 1
                            last_failure = (before.copy(), row_y)
                            if no_progress >= self.cfg.get('max_stale_icon_retries', 3):
                                raise RuntimeError('同一行可偷标识持续存在，但进入农场未能采摘；停止避免空转')
                        else:
                            last_failure = None
                            no_progress = 0
                        self.save_report()
                    previous, unchanged, quiet_passes, pages = None, 0, 0, 0
                    continue
                unchanged = unchanged + 1 if self.signatures_equal(signature, previous) else 0
                previous = signature.copy()
                if unchanged >= 2:
                    # A larger wheel step must also leave the content stationary.
                    self.scroll_ocr(self.cfg.get('scroll_amount', -3) * 4)
                    verify_targets, verify_signature = self.scan_list()
                    if verify_targets or not self.signatures_equal(signature, verify_signature):
                        previous, unchanged = None, 0
                        pages += 1
                        continue
                    quiet_passes += 1
                    if quiet_passes >= 2:
                        self.report.update(complete=True,
                            finished_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat())
                        self.save_report()
                        logging.info('列表扫描完成：拜访 %d 次，采摘操作 %d 次；未发现剩余可偷标识',
                                     self.report['visits'], self.report['harvest_clicks'])
                        return
                    self.top()
                    previous, unchanged, pages = None, 0, 0
                    continue
                if pages >= self.cfg['max_pages']:
                    raise RuntimeError('达到列表滚动上限，尚未确认到底；巡查未完成')
                self.scroll_ocr(self.cfg.get('scroll_amount', -3))
                pages += 1
            raise RuntimeError('达到本轮操作次数上限，巡查未完成')
        except Exception as exc:
            self.report.update(complete=False, error=str(exc))
            self.save_report()
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description='QQ经典农场 Mac 视觉巡查工具')
    parser.add_argument('command', choices=['doctor', 'region', 'capture', 'check', 'check-image', 'run'])
    parser.add_argument('name', nargs='?')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--current', action='store_true', help='只监控当前已打开的好友农场')
    args = parser.parse_args(argv)
    if args.command == 'doctor':
        raise SystemExit(0 if doctor() else 2)
    if args.command == 'check-image':
        global cv2, np
        import cv2
        import numpy as np
        if not args.name:
            parser.error('check-image 后面需要截图路径')
        frame = cv2.imread(args.name, cv2.IMREAD_GRAYSCALE)
        if frame is None:
            parser.error('无法读取截图')
        cfg = settings()
        cfg['region'] = [0, 0, frame.shape[1], frame.shape[0]]
        bot = Bot(cfg, current=True) if args.current else IconFriendsBot(cfg)
        bot.frame = frame
        for name in ('farm', 'ready', 'harvest', 'return_home') + (() if args.current else ('stealable',)):
            print(name, bot.matches(name))
        return
    require_permissions(clicks=args.command == 'run')
    dependencies()
    cfg = settings()
    if args.command == 'region':
        cfg['region'] = rectangle('选择右侧游戏区域，不包含微信聊天和福利侧栏')
        cfg['calibrated'] = False
        save_settings(cfg)
        return
    if args.command == 'capture':
        allowed = {'home', 'friends', 'farm', 'open_friends', 'visit', 'harvest', 'return_home', 'ready'}
        if args.name not in allowed:
            parser.error('模板名必须是：' + ', '.join(sorted(allowed)))
        region = rectangle('选择模板：' + args.name)
        shot, _, _ = capture_region(region)
        folder = data_path('templates')
        folder.mkdir(exist_ok=True)
        shot.save(folder / (args.name + '.png'))
        return
    if not 0.8 <= cfg['threshold'] <= 1 or cfg['interval_seconds'] < 30:
        parser.error('threshold 应在 0.8–1 之间，巡查间隔至少 30 秒')
    bot = Bot(cfg, current=True) if args.current else IconFriendsBot(cfg)
    if args.command == 'check':
        if not args.current:
            kind = bot.read_page()
            print('识别页面:', kind)
            if kind == 'friends':
                targets = bot.icon_rows(bot.words, bot.frame.shape[0], bot.matches('stealable'))
                print('可偷菜标识对应的拜访行数:', len(targets))
                for row in targets:
                    print('可偷标识:', tuple(round(v, 2) for v in row.icon),
                          '拜访位置:', round(row.button.cx), round(row.button.cy))
            print('check 只识别，不点击。')
            return
        bot.observe()
        for name in bot.templates:
            print(name, bot.matches(name))
        print('check 只识别，不点击。坐标为游戏截图中的像素坐标。')
        return
    with monitoring_lock():
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s',
                            handlers=[logging.StreamHandler(), logging.FileHandler(data_path('farm.log'), encoding='utf-8')])
        print('5 秒后开始。Ctrl+C 或将鼠标移到屏幕左上角可停止。请勿移动或遮挡游戏窗口。')
        time.sleep(5)
        while True:
            bot.cycle()
            if args.once:
                break
            logging.info('等待 %s 秒后开始下一轮', cfg['interval_seconds'])
            time.sleep(cfg['interval_seconds'])


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('已停止')
    except Exception as exc:
        print(f'已停止：{exc}')
        raise SystemExit(1)
