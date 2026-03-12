import os
import re
import time
import threading
import subprocess
import traceback
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv
from netmiko import ConnectHandler
from netmiko.exceptions import (
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
)

# =========================================================
# 환경변수 로드
# =========================================================
# USER / PASSWORD / SECRET 가 Windows 환경변수와 겹칠 수 있으므로
# .env 값을 우선 적용
load_dotenv(override=True)

COMMON_CREDS = {
    "user": os.getenv("USER", "").strip(),
    "password": os.getenv("PASSWORD", "").strip(),
    "secret": os.getenv("SECRET", "").strip(),
}

# Git 설정 (.env에 없으면 기본값 사용)
GIT_BRANCH     = os.getenv("GIT_BRANCH", "main").strip() or "main"
GIT_USER_NAME  = os.getenv("GIT_USER_NAME", "").strip()
GIT_USER_EMAIL = os.getenv("GIT_USER_EMAIL", "").strip()

# =========================================================
# 실행 시각
# =========================================================
NOW = datetime.now()
TODAY = NOW.strftime("%Y-%m-%d")
RUN_TIME = NOW.strftime("%H-%M-%S")
RUN_DATETIME = NOW.strftime("%Y-%m-%d %H:%M:%S")

# =========================================================
# 경로 설정
# =========================================================
BASE_DIR = r"D:\Backup Automation\Network\Network Backup"

OUTPUT_BASE   = os.path.join(BASE_DIR, "outputs")
LOG_BASE      = os.path.join(BASE_DIR, "logs")
GIT_REPO_BASE = os.path.join(BASE_DIR, "git_repo")

OUTPUT_DIR = os.path.join(OUTPUT_BASE, TODAY)
LOG_DIR    = os.path.join(LOG_BASE,    TODAY)

os.makedirs(OUTPUT_DIR,    exist_ok=True)
os.makedirs(LOG_DIR,       exist_ok=True)
os.makedirs(GIT_REPO_BASE, exist_ok=True)

# =========================================================
# 장비 목록
# 장비별 크리덴셜이 필요한 경우 username / password / secret 키를 추가하면
# COMMON_CREDS 대신 해당 값이 우선 사용됩니다.
# =========================================================
DEVICES = [
    {"device_type": "cisco_ios", "name": "OFFICE_INT_SW1", "host": "172.16.80.2",   "model": "WS-C2960S-48PS-L", "enabled": "yes"},
]

# =========================================================
# 장비별 명령
# =========================================================
COMMAND_MAP = {
    "cisco_ios": [
        "show clock",
        "show version",
        "show inventory",
        "show ip interface brief",
        "show process cpu",
        "show process cpu history",
        "show process memory",
        "show env all",
        "show ip route",
        "show int summary",
        "show running-config",
        "show logging",
    ],
    "cisco_xe": [
        "show clock",
        "show version",
        "show inventory",
        "show ip interface brief",
        "show process cpu",
        "show process cpu history",
        "show process memory",
        "show env all",
        "show ip route",
        "show int summary",
        "show running-config",
        "show logging",
    ],
}

# =========================================================
# pagination 해제
# =========================================================
PAGING_DISABLE = {
    "cisco_ios": "terminal length 0",
    "cisco_xe":  "terminal length 0",
}

SEPARATOR = "=" * 100

# Git 비교용으로 보관할 명령 (소문자 기준)
GIT_KEEP_COMMANDS: frozenset[str] = frozenset({
    "show version",
    "show inventory",
    "show running-config",
})

# =========================================================
# 스레드 안전 로그 락
# =========================================================
_log_lock = threading.Lock()


# =========================================================
# 유틸리티
# =========================================================
def sanitize_filename(value: str) -> str:
    return re.sub(r'[\\/*?:"<>|]+', "_", str(value).strip())


def write_log(filename: str, message: str) -> None:
    path = os.path.join(LOG_DIR, filename)
    with _log_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(message + "\n")


def get_credentials(device: dict) -> dict:
    return {
        "user":     device.get("username") or COMMON_CREDS["user"],
        "password": device.get("password") or COMMON_CREDS["password"],
        "secret":   device.get("secret")   or COMMON_CREDS["secret"],
    }


def get_model_dir(base_dir: str, model: str, device_type: str) -> str:
    model_name = sanitize_filename(model) if model else sanitize_filename(device_type)
    path = os.path.join(base_dir, model_name)
    os.makedirs(path, exist_ok=True)
    return path


def get_command_timeout(cmd: str) -> int:
    cmd_lower = cmd.lower()
    if "running-config" in cmd_lower:
        return 180
    if "logging" in cmd_lower:
        return 180
    if "route" in cmd_lower:
        return 150
    if "cpu history" in cmd_lower:
        return 120
    return 70


# =========================================================
# Git 유틸리티
# =========================================================
def run_git(args: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", GIT_REPO_BASE] + args,
        check=check,
        capture_output=True,
        text=True,
    )


def ensure_git_repo() -> None:
    git_dir = os.path.join(GIT_REPO_BASE, ".git")
    if os.path.isdir(git_dir):
        return

    try:
        try:
            run_git(["init", "-b", GIT_BRANCH], check=True)
        except Exception:
            run_git(["init"], check=True)

        write_log("git.log", f"{RUN_DATETIME},INIT,{GIT_REPO_BASE}")
        print(f"[GIT] Initialized repository: {GIT_REPO_BASE}")
    except Exception as e:
        write_log("git_error.log", f"{RUN_DATETIME},GIT_INIT_ERROR,{e}")
        print(f"[GIT ERROR] Repository init failed: {e}")


def ensure_git_identity() -> None:
    try:
        if GIT_USER_NAME:
            run_git(["config", "user.name",  GIT_USER_NAME],  check=True)
        if GIT_USER_EMAIL:
            run_git(["config", "user.email", GIT_USER_EMAIL], check=True)

        name_result  = run_git(["config", "--get", "user.name"],  check=False)
        email_result = run_git(["config", "--get", "user.email"], check=False)

        current_name  = (name_result.stdout  or "").strip()
        current_email = (email_result.stdout or "").strip()

        if not current_name or not current_email:
            msg = "Git user.name / user.email not configured"
            print(f"[GIT WARN] {msg}")
            write_log("git_warning.log", f"{RUN_DATETIME},{msg}")

    except Exception as e:
        print(f"[GIT WARN] Failed to configure git identity: {e}")
        write_log("git_warning.log", f"{RUN_DATETIME},IDENTITY_CONFIG_ERROR,{e}")


def ensure_git_branch() -> None:
    try:
        run_git(["checkout", "-B", GIT_BRANCH], check=True)
        write_log("git.log", f"{RUN_DATETIME},BRANCH_SET,{GIT_BRANCH}")
    except Exception as e:
        print(f"[GIT WARN] Failed to set branch {GIT_BRANCH}: {e}")
        write_log("git_warning.log", f"{RUN_DATETIME},BRANCH_SET_ERROR,{GIT_BRANCH},{e}")


def sanitize_text_for_git(text: str) -> str:
    """민감정보 마스킹 + 동적 줄 정제"""
    if not text:
        return text

    sensitive_patterns = [
        (r"(?im)^(username\s+\S+\s+secret\s+)\S+.*$",        r"\1<redacted>"),
        (r"(?im)^(username\s+\S+\s+password\s+)\S+.*$",      r"\1<redacted>"),
        (r"(?im)^(enable secret\s+)\S+.*$",                   r"\1<redacted>"),
        (r"(?im)^(enable password\s+)\S+.*$",                 r"\1<redacted>"),
        (r"(?im)^(snmp-server community\s+)\S+.*$",           r"\1<redacted>"),
        (r"(?im)^(tacacs-server key\s+)\S+.*$",               r"\1<redacted>"),
        (r"(?im)^(key\s+\d+\s+)\S+.*$",                       r"\1<redacted>"),
        (r"(?im)^(password\s+)\S+.*$",                        r"\1<redacted>"),
        (r"(?im)^(\s*pre-shared-key\s+)\S+.*$",               r"\1<redacted>"),
        (r"(?im)^(\s*aaa-server .+ key\s+)\S+.*$",            r"\1<redacted>"),
        (r"(?im)^(\s*tunnel-group .+ password\s+)\S+.*$",     r"\1<redacted>"),
        (r"(?im)^(\s*radius-server key\s+)\S+.*$",            r"\1<redacted>"),
        (r"(?im)^(\s*key-string\s+)\S+.*$",                   r"\1<redacted>"),
    ]
    for pattern, repl in sensitive_patterns:
        text = re.sub(pattern, repl, text)

    dynamic_patterns = [
        (r"(?im)^.*uptime is .*$",                        "<removed: uptime>"),
        (r"(?im)^System returned to ROM by .*$",          "<removed: reload history>"),
        (r"(?im)^System restarted at .*$",                "<removed: restart time>"),
        (r"(?im)^Current configuration : \d+ bytes$",    "<removed: config size>"),
        (r"(?im)^! Last configuration change at .*$",    "! Last configuration change at <removed>"),
        (r"(?im)^! NVRAM config last updated at .*$",    "! NVRAM config last updated at <removed>"),
        (r"(?im)^.*CPU utilization.*$",                   "<removed: cpu utilization>"),
        (r"(?im)^.*minute input rate.*$",                 "<removed: interface rate>"),
        (r"(?im)^.*minute output rate.*$",                "<removed: interface rate>"),
    ]
    for pattern, repl in dynamic_patterns:
        text = re.sub(pattern, repl, text)

    return text


def build_git_content(
    device_name: str,
    host: str,
    model: str,
    command_outputs: list[tuple[str, str]],
) -> str:
    lines = [
        SEPARATOR,
        f"DEVICE : {device_name}",
        f"IP     : {host}",
        f"MODEL  : {model}",
        "TIME   : <removed>",
        SEPARATOR,
        "",
    ]

    for cmd, output in command_outputs:
        if cmd.lower() not in GIT_KEEP_COMMANDS:
            continue
        cleaned = sanitize_text_for_git(output)
        lines += [SEPARATOR, cmd, SEPARATOR, cleaned or "", ""]

    return "\n".join(lines)


def save_git_compare_file(
    model: str,
    device_type: str,
    name: str,
    host: str,
    git_text: str,
) -> str:
    git_model_dir = get_model_dir(GIT_REPO_BASE, model, device_type)
    git_file_path = os.path.join(git_model_dir, f"{sanitize_filename(name)}_{host}.txt")
    with open(git_file_path, "w", encoding="utf-8") as f:
        f.write(git_text)
    return git_file_path


def git_commit_backup() -> None:
    try:
        ensure_git_repo()
        ensure_git_identity()
        ensure_git_branch()

        run_git(["add", "."], check=True)

        diff_result = run_git(["diff", "--cached", "--quiet"], check=False)
        if diff_result.returncode == 0:
            print("[GIT] No changes to commit.")
            write_log("git.log", f"{RUN_DATETIME},NO_CHANGE")
            return

        commit_message = f"Network backup {RUN_DATETIME}"
        run_git(["commit", "-m", commit_message], check=True)

        print(f"[GIT] Commit completed: {commit_message}")
        write_log("git.log", f"{RUN_DATETIME},COMMIT,{commit_message}")

    except subprocess.CalledProcessError as e:
        error_text = (e.stderr or e.stdout or str(e)).strip()
        print(f"[GIT ERROR] {error_text}")
        write_log("git_error.log", f"{RUN_DATETIME},GIT_ERROR,{error_text}")

    except Exception as e:
        print(f"[GIT ERROR] {e}")
        write_log("git_error.log", f"{RUN_DATETIME},GIT_GENERAL_ERROR,{e}")


# =========================================================
# 명령 수집 (SSH 세션과 파일 I/O 분리)
# =========================================================
def collect_commands(
    conn,
    commands: list[str],
    expect_pattern: str | None,
    name: str,
    host: str,
) -> list[tuple[str, str]]:
    results = []
    for cmd in commands:
        timeout = get_command_timeout(cmd)
        try:
            kwargs = dict(
                read_timeout=timeout,
                strip_prompt=False,
                strip_command=False,
            )
            if expect_pattern:
                kwargs["expect_string"] = expect_pattern

            output = conn.send_command(cmd, **kwargs)
        except Exception as cmd_error:
            output = f"COMMAND ERROR: {cmd_error}"
            write_log(
                "command_error.log",
                f"{RUN_DATETIME},{name},{host},{cmd},{cmd_error}",
            )
        results.append((cmd, output))
    return results


def write_output_file(
    file_path: str,
    name: str,
    host: str,
    model: str,
    command_outputs: list[tuple[str, str]],
) -> None:
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(SEPARATOR + "\n")
        f.write(f"DEVICE : {name}\n")
        f.write(f"IP     : {host}\n")
        f.write(f"MODEL  : {model}\n")
        f.write(f"TIME   : {RUN_DATETIME}\n")
        f.write(SEPARATOR + "\n\n")

        for cmd, output in command_outputs:
            f.write(SEPARATOR + "\n")
            f.write(cmd + "\n")
            f.write(SEPARATOR + "\n")
            f.write(output + "\n\n")


# =========================================================
# 장비 백업
# =========================================================
def backup_device(device: dict) -> tuple:
    device_type = device["device_type"]
    name        = device["name"]
    host        = device["host"]
    model       = device.get("model", "")

    creds = get_credentials(device)
    start_time = time.time()
    conn = None

    try:
        print(f"[CONNECT] {name} ({host})")

        conn_params = {
            "device_type": device_type,
            "host":        host,
            "username":    creds["user"],
            "password":    creds["password"],
            "conn_timeout": 60,
        }
        if creds["secret"]:
            conn_params["secret"] = creds["secret"]

        conn = ConnectHandler(**conn_params)
        time.sleep(1)

        prompt = conn.find_prompt()
        expect_pattern: str | None = re.escape(prompt)

        if creds["secret"]:
            try:
                if not conn.check_enable_mode():
                    conn.enable()
                    prompt = conn.find_prompt()
                    expect_pattern = re.escape(prompt)
            except Exception as enable_error:
                write_log(
                    "warning.log",
                    f"{RUN_DATETIME},{name},{host},ENABLE_FAILED,{enable_error}",
                )
                expect_pattern = None

        disable_cmd = PAGING_DISABLE.get(device_type)
        if disable_cmd:
            kwargs = dict(read_timeout=30, strip_prompt=False, strip_command=False)
            if expect_pattern:
                kwargs["expect_string"] = expect_pattern
            conn.send_command(disable_cmd, **kwargs)

        commands = COMMAND_MAP.get(device_type, ["show version"])
        command_outputs = collect_commands(conn, commands, expect_pattern, name, host)

        output_model_dir = get_model_dir(OUTPUT_DIR, model, device_type)
        output_file_path = os.path.join(
            output_model_dir, f"{sanitize_filename(name)}_{host}.txt"
        )
        write_output_file(output_file_path, name, host, model, command_outputs)

        git_text      = build_git_content(name, host, model, command_outputs)
        git_file_path = save_git_compare_file(model, device_type, name, host, git_text)

        elapsed = round(time.time() - start_time, 2)
        print(f"[OK] {name} ({elapsed} sec)")
        write_log(
            "success.log",
            f"{RUN_DATETIME},{name},{host},{elapsed},{output_file_path},{git_file_path}",
        )
        return ("SUCCESS", name, host, device_type, model, elapsed, output_file_path)

    except NetmikoAuthenticationException as e:
        elapsed = round(time.time() - start_time, 2)
        write_log("auth_fail.log", f"{RUN_DATETIME},{name},{host},{elapsed},{e}")
        print(f"[AUTH FAIL] {name} ({host})")
        return ("AUTH_FAIL", name, host, device_type, model, elapsed, "")

    except NetmikoTimeoutException as e:
        elapsed = round(time.time() - start_time, 2)
        write_log("timeout.log", f"{RUN_DATETIME},{name},{host},{elapsed},{e}")
        print(f"[TIMEOUT] {name} ({host})")
        return ("TIMEOUT", name, host, device_type, model, elapsed, "")

    except Exception as e:
        elapsed = round(time.time() - start_time, 2)
        write_log("error.log",        f"{RUN_DATETIME},{name},{host},{elapsed},{e}")
        write_log("error_detail.log", traceback.format_exc())
        print(f"[ERROR] {name} ({host}) : {e}")
        return ("ERROR", name, host, device_type, model, elapsed, "")

    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass


# =========================================================
# Summary
# =========================================================
def print_summary(results: list[tuple], summary_file: str) -> None:
    total   = len(results)
    success = sum(1 for r in results if r[0] == "SUCCESS")
    skipped = sum(1 for r in results if r[0] == "SKIP")
    auth    = sum(1 for r in results if r[0] == "AUTH_FAIL")
    timeout = sum(1 for r in results if r[0] == "TIMEOUT")
    error   = sum(1 for r in results if r[0] == "ERROR")

    print("\n" + SEPARATOR)
    print("BACKUP RESULT")
    print(SEPARATOR)
    print(f"TOTAL      : {total}")
    print(f"SUCCESS    : {success}")
    print(f"SKIPPED    : {skipped}")
    print(f"AUTH_FAIL  : {auth}")
    print(f"TIMEOUT    : {timeout}")
    print(f"ERROR      : {error}")
    print(SEPARATOR)
    print(f"OUTPUT DIR : {OUTPUT_DIR}")
    print(f"LOG DIR    : {LOG_DIR}")
    print(f"GIT REPO   : {GIT_REPO_BASE}")
    print(f"SUMMARY    : {summary_file}")
    print(SEPARATOR)


# =========================================================
# main
# =========================================================
def main() -> None:
    if not COMMON_CREDS["user"] or not COMMON_CREDS["password"]:
        print("USER / PASSWORD missing in .env")
        return

    ensure_git_repo()

    results = []
    for dev in DEVICES:
        if dev.get("enabled", "yes").lower() != "yes":
            print(f"[SKIP] {dev['name']}")
            results.append(
                ("SKIP", dev["name"], dev["host"], dev["device_type"], dev.get("model", ""), 0, "")
            )
            continue
        results.append(backup_device(dev))

    summary = pd.DataFrame(
        results,
        columns=["status", "name", "host", "device_type", "model", "elapsed_sec", "file_path"],
    )

    summary_file = os.path.join(LOG_DIR, f"summary_{TODAY}.csv")
    summary.to_csv(summary_file, index=False, encoding="utf-8-sig")

    print_summary(results, summary_file)
    git_commit_backup()


if __name__ == "__main__":
    main()
