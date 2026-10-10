"""Portable onboarding: each person signs in through their own WeChat app."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

import farm
from runtime_paths import data_dir, monitoring_lock


def open_wechat(opener=subprocess.run):
    result = opener(['open', '-b', 'com.tencent.xinWeChat'], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('无法打开微信，请先安装微信 Mac 版，或手动打开后重试。')


def check_permissions(status=None, reader=input, opener=subprocess.run):
    status = farm.permission_status() if status is None else status
    missing = [name for name in ('屏幕录制', '辅助功能') if not status.get(name)]
    if not missing:
        return True
    print('\n尚未授权：' + '、'.join(missing))
    print('在 系统设置 → 隐私与安全性 中，给运行本工具的终端开启上述权限。')
    print('双击 .command 文件通常由 Terminal（终端）运行；使用 iTerm 时给 iTerm 授权。')
    print('完成后用 Command+Q 完全退出该终端，再重新打开“开始使用.command”。')
    choice = reader('按回车打开系统设置，或输入 q 退出：').strip().lower()
    if choice != 'q':
        opener(['open', '-b', 'com.apple.systempreferences'], capture_output=True, text=True)
    return False


def verify_game(current=False):
    # No mouse input before the actual in-game page is confirmed.
    farm.require_permissions(clicks=True)
    farm.dependencies()
    cfg = farm.settings()
    bot = farm.AllFriendsBot(cfg) if current else farm.IconFriendsBot(cfg)
    kind = bot.read_page()
    allowed = ('farm',) if current else ('home', 'friends', 'farm')
    if kind not in allowed:
        raise RuntimeError('未确认游戏画面。请登录自己的微信、打开 QQ经典农场，并让游戏窗口完整可见。')
    print('已确认游戏画面：' + {'home': '自家农场', 'friends': '好友列表', 'farm': '好友农场'}[kind])
    return kind


def prepare_login(current=False, reader=input, opener=open_wechat,
                  verifier=verify_game, permission_checker=check_permissions, sleeper=time.sleep):
    opener()
    print('\n请在微信里登录自己的账号（扫码或手机确认），然后打开“QQ经典农场”。')
    print('第一次使用建议停留在“同玩好友”列表。游戏须在主显示器上完整可见。')
    if current:
        print('当前好友模式：请先进入一位好友的农场。')
    print('工具不接收微信账号、密码或验证码，登录由微信完成。')
    while True:
        if reader('准备好后按回车；输入 q 退出：').strip().lower() == 'q':
            return False
        if not permission_checker():
            return False
        print('5 秒后检查游戏画面，请把终端移到游戏旁边，保持游戏不被遮挡。', flush=True)
        sleeper(5)
        try:
            verifier(current=current)
            return True
        except RuntimeError as exc:
            print('尚未就绪：' + str(exc))
            print('请完成登录并打开游戏后重试；此次检查没有点击游戏。')


def main(argv=None):
    parser = argparse.ArgumentParser(description='QQ农场监控：登录自己的微信后使用')
    parser.add_argument('--once', action='store_true', help='只巡查一轮')
    parser.add_argument('--current', action='store_true', help='只监控当前好友')
    parser.add_argument('--check', action='store_true', help='登录引导后只识别，不启动采摘')
    parser.add_argument('--diagnose', action='store_true', help='仅检查依赖、权限和窗口，不打开微信或点击')
    args = parser.parse_args(argv)
    if sys.platform != 'darwin':
        raise RuntimeError('此分享包适用于 macOS 微信电脑版。')
    os.environ.setdefault('QQFARM_DATA_DIR', str(Path.home() / 'Library' / 'Application Support' / 'QQFarmMonitor'))
    print('QQ农场监控 · Mac 分享版', flush=True)
    print('本机配置和日志：' + str(data_dir()), flush=True)
    if args.diagnose:
        return 0 if farm.doctor() else 2
    with monitoring_lock():
        if not prepare_login(current=args.current):
            return 2
        command = ['check' if args.check else 'run']
        if args.current:
            command.append('--current')
        if args.once:
            command.append('--once')
        farm.main(command)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, EOFError):
        print('\n已退出')
        raise SystemExit(0)
    except Exception as exc:
        print('已停止：' + str(exc))
        raise SystemExit(1)
