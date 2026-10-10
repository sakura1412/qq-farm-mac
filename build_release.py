#!/usr/bin/env python3
"""Build a clean, portable distribution using an explicit payload allowlist."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys
import zipfile


ROOT = Path(__file__).resolve().parent
PACKAGE_ROOT = "QQ农场监控-Mac"
PROGRAM_FILES = (
    "farm.py",
    "all_friends.py",
    "runtime_paths.py",
    "public_launcher.py",
    "portable_bootstrap.sh",
    "开始使用.command",
    "requirements.txt",
    "分享版使用说明.txt",
    "VERSION.txt",
)
TEMPLATE_FILES = (
    "farm.png",
    "harvest.png",
    "return_home.png",
    "ready.png",
    "ready1.png",
    "ready2.png",
    "ready3.png",
    "ready4.png",
    "ready5.png",
    "ready6.png",
    "ready7.png",
    "ready8.png",
    "stealable.png",
    "stealable1.png",
    "stealable2.png",
)
PAYLOAD_FILES = PROGRAM_FILES + tuple("templates/" + name for name in TEMPLATE_FILES)
EXECUTABLE_FILES = {"开始使用.command", "portable_bootstrap.sh"}
DEFAULT_CONFIG = {
    "region": None,
    "threshold": 0.93,
    "stealable_threshold": 0.92,
    "interval_seconds": 180,
    "max_pages": 100,
    "max_harvest_clicks": 3,
    "scroll_amount": -3,
    "settle_seconds": 2,
    "max_friends": 500,
    "max_verification_passes": 6,
    "max_operations": 10000,
    "max_stale_icon_retries": 3,
    "calibrated": False,
}


def load_payload():
    """Reject missing files and symlinks before creating any release artifacts."""
    if (ROOT / "templates").is_symlink():
        raise ValueError("templates 目录不能是符号链接")
    missing = [name for name in PAYLOAD_FILES if not (ROOT / name).is_file()]
    if missing:
        raise ValueError("分享包缺少文件：" + "、".join(missing))
    payload = {}
    for name in PAYLOAD_FILES:
        source = ROOT / name
        if source.is_symlink():
            raise ValueError("分享包文件不能是符号链接：" + name)
        data = source.read_bytes()
        if not data:
            raise ValueError("分享包文件为空：" + name)
        if name.startswith("templates/") and not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("模板不是 PNG 文件：" + name)
        payload[name] = data
    version = payload["VERSION.txt"].decode("utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("VERSION.txt 必须是三段数字版本号")
    # Never copy a user's existing config, even if it happens to use safe values.
    payload["config.json"] = (
        json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    return version, payload


def archive_info(name):
    # Stable timestamps and explicit Unix modes make builds repeatable and keep
    # Finder's .command launch permission when the archive is extracted on macOS.
    info = zipfile.ZipInfo(PACKAGE_ROOT + "/" + name, date_time=(2020, 1, 1, 0, 0, 0))
    info.create_system = 3
    mode = 0o755 if name in EXECUTABLE_FILES else 0o644
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def verify_archive(path, payload):
    expected = {PACKAGE_ROOT + "/" + name for name in payload}
    with zipfile.ZipFile(path) as archive:
        if set(archive.namelist()) != expected or len(archive.infolist()) != len(expected):
            raise ValueError("分享包内容与允许名单不一致")
        if archive.testzip() is not None:
            raise ValueError("分享包 ZIP 校验失败")
        for name, data in payload.items():
            entry = PACKAGE_ROOT + "/" + name
            if archive.read(entry) != data:
                raise ValueError("分享包内容校验失败：" + name)
            actual_mode = stat.S_IMODE(archive.getinfo(entry).external_attr >> 16)
            expected_mode = 0o755 if name in EXECUTABLE_FILES else 0o644
            if actual_mode != expected_mode:
                raise ValueError("分享包文件权限不正确：" + name)


def build(output_dir):
    version, payload = load_payload()
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / (PACKAGE_ROOT + "-" + version + ".zip")
    temporary = archive_path.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(payload):
                archive.writestr(archive_info(name), payload[name])
        verify_archive(temporary, payload)
        temporary.replace(archive_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    manifest = {
        "version": version,
        "archive": archive_path.name,
        "archive_bytes": archive_path.stat().st_size,
        "archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        "payload_file_count": len(payload),
        "config": "generated from public defaults; no local configuration copied",
        "files": [
            {
                "path": name,
                "bytes": len(payload[name]),
                "sha256": hashlib.sha256(payload[name]).hexdigest(),
                "mode": "0755" if name in EXECUTABLE_FILES else "0644",
            }
            for name in sorted(payload)
        ],
    }
    manifest_path = archive_path.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("分享包：" + str(archive_path))
    print("文件数：{}；ZIP 大小：{} 字节".format(len(payload), archive_path.stat().st_size))
    print("校验清单：" + str(manifest_path))
    return archive_path, manifest_path


def main():
    parser = argparse.ArgumentParser(description="生成不含个人数据的 Mac 农场监控分享包")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--check-only", action="store_true", help="只检查打包文件是否齐全")
    args = parser.parse_args()
    try:
        if args.check_only:
            version, payload = load_payload()
            print("版本 {} 的 {} 个分享文件检查通过".format(version, len(payload)))
        else:
            build(args.output_dir.resolve())
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print("打包失败：" + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
