#!/bin/zsh
# 可分发入口：源码随压缩包移动，依赖和运行记录归当前 macOS 用户所有。
setopt PIPE_FAIL

farm_source_dir="${0:A:h}"
farm_requirements="$farm_source_dir/requirements.txt"
farm_data_dir="${QQFARM_DATA_DIR:-$HOME/Library/Application Support/QQFarmMonitor}"
farm_data_dir="${farm_data_dir:A}"
export QQFARM_DATA_DIR="$farm_data_dir"
farm_mode=launch
farm_requested_python=''
farm_public_args=()
farm_lock_owned=0

farm_error() {
  print -u2 -- "$*"
}

farm_release_lock() {
  if [[ "$farm_lock_owned" == 1 ]]; then
    rm -f -- "$farm_data_dir/.bootstrap.lock/pid"
    rmdir -- "$farm_data_dir/.bootstrap.lock" 2>/dev/null
  fi
}

trap farm_release_lock EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

while (( $# )); do
  case "$1" in
    --select-python) farm_mode=select; shift ;;
    --self-check) farm_mode=check; shift ;;
    --python)
      if (( $# < 2 )); then farm_error '--python 后需要 Python 的完整路径。'; exit 2; fi
      farm_requested_python="$2"; shift 2 ;;
    --) shift; farm_public_args+=("$@"); break ;;
    *) farm_public_args+=("$1"); shift ;;
  esac
done

if [[ ! -r "$farm_requirements" ]]; then
  farm_error "安装包缺少 requirements.txt：$farm_source_dir"
  exit 2
fi

# 探测只读取本地信息，不安装依赖、不申请权限，也不访问微信。
farm_probe_python() {
  local farm_candidate="$1" farm_probe farm_lines
  [[ -x "$farm_candidate" ]] || return 1
  farm_probe=$("$farm_candidate" - "$farm_requirements" 2>/dev/null <<'PY'
import sys
if not (3, 9) <= sys.version_info[:2] < (3, 14):
    raise SystemExit(1)
import ctypes, ensurepip, hashlib, json, pathlib, platform, sqlite3, ssl, venv
requirements = pathlib.Path(sys.argv[1]).read_bytes()
identity = {
    "format": 1,
    "python_executable": str(pathlib.Path(sys.executable).resolve()),
    "python_version": sys.version,
    "architecture": platform.machine(),
    "requirements_sha256": hashlib.sha256(requirements).hexdigest(),
}
encoded = json.dumps(identity, sort_keys=True, ensure_ascii=True)
print(identity["python_executable"])
print(platform.python_version())
print(identity["architecture"])
print(hashlib.sha256(encoded.encode()).hexdigest())
print(encoded)
PY
  ) || return 1
  farm_lines=("${(@f)farm_probe}")
  (( ${#farm_lines} == 5 )) || return 1
  [[ -x "${farm_lines[1]}" && "${farm_lines[4]}" =~ '^[0-9a-f]{64}$' ]] || return 1
  farm_python="${farm_lines[1]}"
  farm_python_version="${farm_lines[2]}"
  farm_python_arch="${farm_lines[3]}"
  farm_fingerprint="${farm_lines[4]}"
  farm_identity="${farm_lines[5]}"
}

farm_python=''
if [[ -n "$farm_requested_python" ]]; then
  farm_probe_python "$farm_requested_python" || {
    farm_error "指定的 Python 不可用：$farm_requested_python"
    farm_error '需要完整的 Python 3.9–3.13（含 venv、ensurepip 和 SSL）。推荐 Python.org 的 Python 3.12 或 3.13。'
    exit 2
  }
else
  # 独立安装优先；避免把好友电脑的 Apple 开发工具安装弹窗作为起点。
  farm_candidates=(
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3
    /Library/Frameworks/Python.framework/Versions/3.11/bin/python3
    /Library/Frameworks/Python.framework/Versions/3.10/bin/python3
    /Library/Frameworks/Python.framework/Versions/3.9/bin/python3
    /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12
    /opt/homebrew/bin/python3 /usr/local/bin/python3.13
    /usr/local/bin/python3.12 /usr/local/bin/python3
  )
  farm_path_python=$(command -v python3 2>/dev/null)
  [[ -n "$farm_path_python" && "$farm_path_python" != /usr/bin/python3 ]] && farm_candidates+=("$farm_path_python")
  # 已安装命令行工具时才尝试系统 Python；不会主动安装 Xcode。
  [[ -x /Library/Developer/CommandLineTools/usr/bin/python3 ]] && farm_candidates+=(/Library/Developer/CommandLineTools/usr/bin/python3)
  for farm_candidate in "${farm_candidates[@]}"; do
    if farm_probe_python "$farm_candidate"; then break; fi
  done
fi

if [[ -z "$farm_python" ]]; then
  farm_error '没有找到可用的 Python 3.9–3.13。'
  farm_error '请打开 https://www.python.org/downloads/macos/，安装 Python 3.12 或 3.13，再双击“开始使用.command”。'
  farm_error '如果已安装在其他位置，可运行：zsh portable_bootstrap.sh --python /完整路径/python3 --self-check'
  exit 2
fi

farm_environment="$farm_data_dir/environments/$farm_fingerprint"
if [[ "$farm_mode" == select ]]; then
  print -r -- "$farm_python"
  exit 0
fi
if [[ "$farm_mode" == check ]]; then
  print -r -- "Python：$farm_python"
  print -r -- "版本：$farm_python_version；架构：$farm_python_arch"
  print -r -- "用户数据：$farm_data_dir"
  print -r -- "依赖环境：$farm_environment"
  print -r -- "依赖指纹：$farm_fingerprint"
  print -r -- '本地自检通过；未下载或安装任何内容。'
  exit 0
fi

if [[ ! -r "$farm_source_dir/public_launcher.py" ]]; then
  farm_error '安装包缺少 public_launcher.py，请重新完整解压安装包。'
  exit 2
fi
mkdir -p -- "$farm_data_dir/environments" || {
  farm_error "无法创建用户数据目录：$farm_data_dir"; exit 1
}

farm_environment_ready() {
  [[ -f "$farm_environment/.ready" && -f "$farm_environment/identity.json" && -x "$farm_environment/bin/python" ]] || return 1
  [[ "$(<"$farm_environment/.ready")" == "$farm_fingerprint" ]] || return 1
  [[ "$(<"$farm_environment/identity.json")" == "$farm_identity" ]] || return 1
  "$farm_environment/bin/python" -c 'import cv2, numpy, PIL, pyautogui, Quartz, Vision' >/dev/null 2>&1
}

if ! farm_environment_ready; then
  if ! mkdir -- "$farm_data_dir/.bootstrap.lock" 2>/dev/null; then
    farm_previous_pid=''
    [[ -r "$farm_data_dir/.bootstrap.lock/pid" ]] && farm_previous_pid=$(<"$farm_data_dir/.bootstrap.lock/pid")
    if [[ "$farm_previous_pid" == <-> ]] && ! kill -0 "$farm_previous_pid" 2>/dev/null; then
      rm -f -- "$farm_data_dir/.bootstrap.lock/pid"
      rmdir -- "$farm_data_dir/.bootstrap.lock" 2>/dev/null
    fi
    if ! mkdir -- "$farm_data_dir/.bootstrap.lock" 2>/dev/null; then
      farm_error '另一个窗口正在准备运行环境。请等它完成后再启动。'
      exit 1
    fi
  fi
  farm_lock_owned=1
  print -r -- "$$" > "$farm_data_dir/.bootstrap.lock/pid"

  # 拿到锁后再检查，另一个启动窗口可能刚完成依赖安装。
  if ! farm_environment_ready; then
    print -r -- "首次准备依赖：Python $farm_python_version（$farm_python_arch）"
    print -r -- '此步骤需要联网下载依赖。完成后，普通启动会复用本地环境。'
    # 路径仅由当前用户数据目录和 64 位十六进制指纹组成；不操作原脚本的 .venv。
    if [[ -e "$farm_environment" ]]; then
      rm -rf -- "$farm_environment" || { farm_error '无法重建未完成的依赖环境。'; exit 1; }
    fi
    "$farm_python" -m venv "$farm_environment" || {
      farm_error '创建 Python 环境失败。请使用 Python.org 的完整 Python 3.12 或 3.13 安装包后重试。'
      exit 1
    }
    "$farm_environment/bin/python" -m pip install --disable-pip-version-check --timeout 25 --retries 1 -r "$farm_requirements"
    farm_install_status=$?
    if (( farm_install_status != 0 )); then
      farm_error '依赖安装未完成。请检查网络连接并保留上方报错；重新启动会重试。'
      farm_error '如果报错提到证书，请运行 Python.org 安装目录里的 Install Certificates.command。'
      exit "$farm_install_status"
    fi
    "$farm_environment/bin/python" -c 'import cv2, numpy, PIL, pyautogui, Quartz, Vision' || {
      farm_error '依赖已下载，但加载失败。请保留上方报错及启动日志。'
      exit 1
    }
    print -r -- "$farm_identity" > "$farm_environment/identity.json" || exit 1
    print -r -- "$farm_fingerprint" > "$farm_environment/.ready" || exit 1
  fi
  farm_release_lock
  farm_lock_owned=0
fi

print -r -- "运行环境：Python $farm_python_version；用户数据：$farm_data_dir"
"$farm_environment/bin/python" -u "$farm_source_dir/public_launcher.py" "${farm_public_args[@]}"
exit $?
