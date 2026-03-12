import csv
import os
import re
import time
import socket
import traceback
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple, List

import paramiko
from dotenv import load_dotenv


# =========================================================
# 1. Load environment variables
# =========================================================
load_dotenv()


# =========================================================
# 2. Fixed base path in script
# =========================================================
BASE_LOG_DIR = r"D:\Backup Automation\Network\Change Password"


# =========================================================
# 3. Constants
# =========================================================
STATUS_SUCCESS = "SUCCESS"
STATUS_SKIPPED = "SKIPPED"
STATUS_AUTH_FAIL = "AUTH_FAIL"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_ERROR = "ERROR"

PROMPT_PATTERN = re.compile(r"(?m)[^\n\r]+[>#]\s?$")

ERROR_KEYWORDS = (
    "% Invalid",
    "% Incomplete",
    "% Ambiguous",
    "% Authorization failed",
    "% Access denied",
    "% Unknown command",
    "% Bad IP address",
    "% Unrecognized command",
    "% Permission denied",
)

SUMMARY_WIDTH = 100


# =========================================================
# 4. Data classes
# =========================================================
@dataclass
class Config:
    ssh_username: str
    ssh_password: str
    enable_password: Optional[str]
    old_username: str
    new_username: str
    new_password: str
    new_user_privilege: int
    use_secret: bool
    write_memory: bool
    connect_timeout: int
    command_timeout: float
    verify_login: bool
    delete_old_user: bool
    log_dir: str
    run_log_path: str
    failed_log_path: str
    result_csv_path: str
    git_enabled: bool
    git_branch: str


@dataclass
class Device:
    name: str
    host: str
    ssh_port: int = 22


# =========================================================
# 5. Device list
# =========================================================
DEVICES = [
    Device("SW01", "172.16.75.201"),
    Device("SW02", "172.16.75.202"),
    Device("SW03", "172.16.75.203"),
    Device("SW04", "172.16.75.204"),
    Device("SW05", "172.16.75.205"),
]


# =========================================================
# 6. Result counters
# =========================================================
RESULT = {
    "TOTAL": 0,
    "SUCCESS": 0,
    "SKIPPED": 0,
    "AUTH_FAIL": 0,
    "TIMEOUT": 0,
    "ERROR": 0,
}

RESULT_ROWS = []


# =========================================================
# 7. Config / Utility
# =========================================================
def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_name(text: str) -> str:
    return text.replace("/", "_").replace("\\", "_").replace(" ", "_")


def load_config() -> Config:
    today = datetime.now().strftime("%Y-%m-%d")
    log_dir = os.path.join(BASE_LOG_DIR, "logs", today)
    os.makedirs(log_dir, exist_ok=True)

    return Config(
        ssh_username=os.getenv("SSH_USERNAME", "").strip(),
        ssh_password=os.getenv("SSH_PASSWORD", "").strip(),
        enable_password=os.getenv("ENABLE_PASSWORD", "").strip() or None,
        old_username=os.getenv("OLD_USERNAME", "").strip(),
        new_username=os.getenv("NEW_USERNAME", "").strip(),
        new_password=os.getenv("NEW_PASSWORD", "").strip(),
        new_user_privilege=int(os.getenv("NEW_USER_PRIVILEGE", "15")),
        use_secret=os.getenv("USE_SECRET", "True").lower() == "true",
        write_memory=os.getenv("WRITE_MEMORY", "True").lower() == "true",
        connect_timeout=int(os.getenv("CONNECT_TIMEOUT", "10")),
        command_timeout=float(os.getenv("COMMAND_DELAY", "1.0")),
        verify_login=os.getenv("VERIFY_LOGIN", "True").lower() == "true",
        delete_old_user=os.getenv("DELETE_OLD_USER", "True").lower() == "true",
        log_dir=log_dir,
        run_log_path=os.path.join(log_dir, "run.log"),
        failed_log_path=os.path.join(log_dir, "failed.log"),
        result_csv_path=os.path.join(log_dir, "result.csv"),
        git_enabled=os.getenv("GIT_ENABLED", "False").lower() == "true",
        git_branch=os.getenv("GIT_BRANCH", "master").strip(),
    )


def validate_config(cfg: Config) -> None:
    required = {
        "SSH_USERNAME": cfg.ssh_username,
        "SSH_PASSWORD": cfg.ssh_password,
        "OLD_USERNAME": cfg.old_username,
        "NEW_USERNAME": cfg.new_username,
        "NEW_PASSWORD": cfg.new_password,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise ValueError(f".env required value missing: {', '.join(missing)}")

    if cfg.old_username == cfg.new_username:
        raise ValueError("OLD_USERNAME and NEW_USERNAME must be different.")


def mask_sensitive(text: str, cfg: Config) -> str:
    if not text:
        return text

    masked = text
    for secret in (cfg.ssh_password, cfg.enable_password, cfg.new_password):
        if secret:
            masked = masked.replace(secret, "********")
    return masked


def write_text_file(path: str, content: str, mode: str = "a") -> None:
    with open(path, mode, encoding="utf-8") as f:
        f.write(content)


def write_device_log(cfg: Config, device: Device, content: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{safe_name(device.name)}_{device.host}_{timestamp}.log"
    path = os.path.join(cfg.log_dir, filename)

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    return path


def append_run_log(cfg: Config, line: str) -> None:
    write_text_file(cfg.run_log_path, line + "\n")


def append_failed_log(cfg: Config, content: str) -> None:
    separator = "=" * SUMMARY_WIDTH
    write_text_file(cfg.failed_log_path, separator + "\n" + content + "\n")


def init_run_log(cfg: Config) -> None:
    separator = "=" * SUMMARY_WIDTH
    header = [
        separator,
        "START CHANGE PASSWORD JOB",
        separator,
        f"START TIME : {now_str()}",
        f"LOG DIR    : {cfg.log_dir}",
        separator,
        "",
    ]
    write_text_file(cfg.run_log_path, "\n".join(header), mode="w")
    write_text_file(cfg.failed_log_path, "", mode="w")


def save_result_csv(cfg: Config) -> None:
    with open(cfg.result_csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["name", "host", "status", "message", "log_file"]
        )
        writer.writeheader()
        writer.writerows(RESULT_ROWS)


def build_create_user_command(cfg: Config) -> str:
    if cfg.use_secret:
        return (
            f"username {cfg.new_username} privilege {cfg.new_user_privilege} "
            f"secret {cfg.new_password}"
        )
    return (
        f"username {cfg.new_username} privilege {cfg.new_user_privilege} "
        f"password {cfg.new_password}"
    )


def build_delete_user_command(cfg: Config) -> str:
    return f"no username {cfg.old_username}"


def has_cli_error(output: str) -> bool:
    return any(keyword in output for keyword in ERROR_KEYWORDS)


def is_write_memory_success(output: str) -> bool:
    success_keywords = (
        "[OK]",
        "Copy complete",
        "Building configuration",
    )
    return any(keyword in output for keyword in success_keywords)


def git_commit_only(cfg: Config) -> Tuple[bool, str]:
    if not cfg.git_enabled:
        return True, "git commit skipped (GIT_ENABLED=False)"

    try:
        # git repo 여부 확인
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            check=True,
            capture_output=True,
            text=True,
        )

        # 변경사항 스테이징
        subprocess.run(["git", "add", "."], check=True)

        # 변경사항 유무 확인
        status_proc = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )

        if not status_proc.stdout.strip():
            return True, "no changes to commit"

        commit_message = (
            f"Auto commit network account rotation {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        subprocess.run(
            ["git", "commit", "-m", commit_message],
            check=True,
            capture_output=True,
            text=True,
        )

        return True, f"git commit completed: {commit_message}"

    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip() if e.stderr else str(e)
        return False, f"git commit failed: {stderr}"
    except FileNotFoundError:
        return False, "git command not found"


# =========================================================
# 8. SSH / CLI helpers
# =========================================================
def open_ssh_client(host: str, port: int, username: str, password: str, timeout: int) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        port=port,
        username=username,
        password=password,
        look_for_keys=False,
        allow_agent=False,
        timeout=timeout,
        banner_timeout=timeout,
        auth_timeout=timeout,
    )
    return client


def wait_for_output(
    shell,
    timeout: float = 8.0,
    expect_prompt: bool = True,
    expected_texts: Optional[List[str]] = None,
) -> str:
    end_time = time.time() + timeout
    output = ""
    expected_texts = expected_texts or []

    while time.time() < end_time:
        if shell.recv_ready():
            chunk = shell.recv(65535).decode("utf-8", errors="ignore")
            output += chunk

            if any(text in output for text in expected_texts):
                return output

            if expect_prompt and PROMPT_PATTERN.search(output):
                return output

        time.sleep(0.1)

    return output


def send_command(
    shell,
    command: str,
    timeout: float = 8.0,
    expect_prompt: bool = True,
    expected_texts: Optional[List[str]] = None,
) -> str:
    shell.send(command + "\n")
    return wait_for_output(
        shell,
        timeout=timeout,
        expect_prompt=expect_prompt,
        expected_texts=expected_texts,
    )


def enter_enable_mode(shell, cfg: Config) -> Tuple[bool, str]:
    output = send_command(shell, "", timeout=2.0)

    if output.strip().endswith("#"):
        return True, output

    output += send_command(
        shell,
        "enable",
        timeout=3.0,
        expect_prompt=False,
        expected_texts=["Password:", "password:", "#"],
    )

    if "Password:" in output or "password:" in output:
        if not cfg.enable_password:
            return False, output + "\n[ERROR] enable password not provided"

        shell.send(cfg.enable_password + "\n")
        output += wait_for_output(shell, timeout=5.0, expect_prompt=True)

    if output.strip().endswith("#"):
        return True, output

    return False, output


def verify_login(device: Device, username: str, password: str, cfg: Config) -> Tuple[bool, str]:
    client = None
    try:
        client = open_ssh_client(
            host=device.host,
            port=device.ssh_port,
            username=username,
            password=password,
            timeout=cfg.connect_timeout,
        )
        shell = client.invoke_shell(width=200, height=1000)
        output = wait_for_output(shell, timeout=4.0, expect_prompt=True)
        return True, output or "[VERIFY] login success"
    except paramiko.AuthenticationException:
        return False, f"[VERIFY] authentication failed for username={username}"
    except socket.timeout:
        return False, f"[VERIFY] timeout while verifying username={username}"
    except Exception as e:
        return False, f"[VERIFY] verification failed for username={username}: {e}"
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass


# =========================================================
# 9. Reusable session operations
# =========================================================
def prepare_privileged_session(shell, cfg: Config, logs: List[str]) -> Tuple[bool, str]:
    def log(msg: str) -> None:
        logs.append(msg)

    log("[STEP] terminal length 0")
    out = send_command(shell, "terminal length 0", timeout=3.0)
    log(mask_sensitive(out, cfg))

    log("[STEP] enter enable mode")
    ok, out = enter_enable_mode(shell, cfg)
    log(mask_sensitive(out, cfg))
    if not ok:
        return False, "enable mode failed"

    log("[STEP] configure terminal")
    out = send_command(shell, "configure terminal", timeout=3.0)
    log(mask_sensitive(out, cfg))
    if has_cli_error(out):
        return False, "configure terminal rejected"

    return True, "ok"


def save_and_exit_config(shell, cfg: Config, logs: List[str], step_label: str) -> Tuple[bool, str]:
    def log(msg: str) -> None:
        logs.append(msg)

    log("[STEP] end")
    out = send_command(shell, "end", timeout=3.0)
    log(mask_sensitive(out, cfg))

    if cfg.write_memory:
        log(f"[STEP] write memory {step_label}")
        out = send_command(shell, "write memory", timeout=10.0)
        log(mask_sensitive(out, cfg))

        if has_cli_error(out):
            return False, f"write memory failed {step_label}"

        if not is_write_memory_success(out):
            log("[WARN] write memory success string not found, verify manually")

    return True, "ok"


def delete_old_user_with_confirm(shell, cfg: Config, logs: List[str]) -> Tuple[bool, str]:
    def log(msg: str) -> None:
        logs.append(msg)

    delete_cmd = build_delete_user_command(cfg)

    log(f"[STEP 5] Deleting OLD account: {cfg.old_username}")
    log(delete_cmd)

    out = send_command(
        shell,
        delete_cmd,
        timeout=5.0,
        expect_prompt=False,
        expected_texts=[
            "[confirm]",
            "Do you want to continue?",
            "#",
            ">",
        ],
    )
    log(mask_sensitive(out, cfg))

    if has_cli_error(out):
        return False, "old user delete failed"

    confirm_keywords = [
        "[confirm]",
        "Do you want to continue?",
    ]

    if any(keyword in out for keyword in confirm_keywords):
        log("[STEP 5-1] Confirm detected, sending Enter")
        shell.send("\n")

        confirm_out = wait_for_output(
            shell,
            timeout=8.0,
            expect_prompt=True,
        )
        out += confirm_out
        log(mask_sensitive(confirm_out, cfg))

        if has_cli_error(confirm_out):
            return False, "old user delete confirm failed"

    if not out.strip().endswith("#"):
        final_out = wait_for_output(shell, timeout=5.0, expect_prompt=True)
        if final_out:
            out += final_out
            log(mask_sensitive(final_out, cfg))

    if has_cli_error(out):
        return False, "old user delete failed after confirm"

    return True, "ok"


# =========================================================
# 10. Main device operation
# =========================================================
def rotate_account_on_switch(device: Device, cfg: Config) -> Tuple[str, str, str]:
    client = None
    logs: List[str] = []

    def log(msg: str) -> None:
        logs.append(msg)

    try:
        log("=" * SUMMARY_WIDTH)
        log(f"START TIME   : {now_str()}")
        log(f"DEVICE       : {device.name}")
        log(f"HOST         : {device.host}")
        log(f"SSH PORT     : {device.ssh_port}")
        log("=" * SUMMARY_WIDTH)

        log(f"[STEP 1] SSH connect with old account: {cfg.ssh_username}")
        client = open_ssh_client(
            host=device.host,
            port=device.ssh_port,
            username=cfg.ssh_username,
            password=cfg.ssh_password,
            timeout=cfg.connect_timeout,
        )

        shell = client.invoke_shell(width=200, height=1000)
        banner = wait_for_output(shell, timeout=3.0, expect_prompt=True)
        if banner:
            log("[RECV] Initial banner/prompt (old account)")
            log(mask_sensitive(banner, cfg))

        ok, msg = prepare_privileged_session(shell, cfg, logs)
        if not ok:
            log(f"[RESULT] ERROR - {msg}")
            log(f"END TIME     : {now_str()}")
            return STATUS_ERROR, "\n".join(logs), msg

        log("[STEP 2] Create NEW account")
        create_cmd = build_create_user_command(cfg)
        log(
            f"username {cfg.new_username} privilege {cfg.new_user_privilege} "
            f"{'secret' if cfg.use_secret else 'password'} ********"
        )
        out = send_command(shell, create_cmd, timeout=4.0)
        log(mask_sensitive(out, cfg))
        if has_cli_error(out):
            log("[RESULT] ERROR - new user create failed")
            log(f"END TIME     : {now_str()}")
            return STATUS_ERROR, "\n".join(logs), "new user create failed"

        ok, msg = save_and_exit_config(shell, cfg, logs, "after create")
        if not ok:
            log(f"[RESULT] ERROR - {msg}")
            log(f"END TIME     : {now_str()}")
            return STATUS_ERROR, "\n".join(logs), msg

        try:
            client.close()
        except Exception:
            pass
        client = None

        if cfg.verify_login:
            log(f"[STEP 3] Verify NEW login: {cfg.new_username}")
            verify_ok, verify_out = verify_login(
                device=device,
                username=cfg.new_username,
                password=cfg.new_password,
                cfg=cfg,
            )
            log(mask_sensitive(verify_out, cfg))
            if not verify_ok:
                log("[RESULT] ERROR - new user login verification failed")
                log(f"END TIME     : {now_str()}")
                return STATUS_ERROR, "\n".join(logs), "new user login verification failed"

        log(f"[STEP 4] SSH connect with new account: {cfg.new_username}")
        client = open_ssh_client(
            host=device.host,
            port=device.ssh_port,
            username=cfg.new_username,
            password=cfg.new_password,
            timeout=cfg.connect_timeout,
        )

        shell = client.invoke_shell(width=200, height=1000)
        banner = wait_for_output(shell, timeout=3.0, expect_prompt=True)
        if banner:
            log("[RECV] Initial banner/prompt (new account)")
            log(mask_sensitive(banner, cfg))

        ok, msg = prepare_privileged_session(shell, cfg, logs)
        if not ok:
            log(f"[RESULT] ERROR - {msg} on new account session")
            log(f"END TIME     : {now_str()}")
            return STATUS_ERROR, "\n".join(logs), f"{msg} on new account session"

        if cfg.delete_old_user:
            ok, msg = delete_old_user_with_confirm(shell, cfg, logs)
            if not ok:
                log(f"[RESULT] ERROR - {msg}")
                log(f"END TIME     : {now_str()}")
                return STATUS_ERROR, "\n".join(logs), msg
        else:
            log("[STEP 5] Old user deletion skipped by config")
            log("[RESULT] SKIPPED - old user delete skipped")
            log(f"END TIME     : {now_str()}")
            return STATUS_SKIPPED, "\n".join(logs), "old user delete skipped by config"

        ok, msg = save_and_exit_config(shell, cfg, logs, "after delete")
        if not ok:
            log(f"[RESULT] ERROR - {msg}")
            log(f"END TIME     : {now_str()}")
            return STATUS_ERROR, "\n".join(logs), msg

        try:
            client.close()
        except Exception:
            pass
        client = None

        if cfg.verify_login:
            log(f"[STEP 6] Final verify login with new account: {cfg.new_username}")
            verify_ok, verify_out = verify_login(
                device=device,
                username=cfg.new_username,
                password=cfg.new_password,
                cfg=cfg,
            )
            log(mask_sensitive(verify_out, cfg))
            if not verify_ok:
                log("[RESULT] ERROR - final new user login verification failed")
                log(f"END TIME     : {now_str()}")
                return STATUS_ERROR, "\n".join(logs), "final new user login verification failed"

        log("[RESULT] SUCCESS - new user created and old user deleted on new account session")
        log(f"END TIME     : {now_str()}")
        return STATUS_SUCCESS, "\n".join(logs), "new user created and old user deleted successfully"

    except paramiko.AuthenticationException:
        log("[RESULT] AUTH_FAIL - authentication failed")
        log(f"END TIME     : {now_str()}")
        return STATUS_AUTH_FAIL, "\n".join(logs), "authentication failed"

    except socket.timeout:
        log("[RESULT] TIMEOUT - connection timeout")
        log(f"END TIME     : {now_str()}")
        return STATUS_TIMEOUT, "\n".join(logs), "connection timeout"

    except paramiko.SSHException as e:
        log(f"[RESULT] ERROR - SSHException: {e}")
        log(traceback.format_exc())
        log(f"END TIME     : {now_str()}")
        return STATUS_ERROR, "\n".join(logs), f"SSHException: {e}"

    except Exception as e:
        log(f"[RESULT] ERROR - Exception: {e}")
        log(traceback.format_exc())
        log(f"END TIME     : {now_str()}")
        return STATUS_ERROR, "\n".join(logs), f"Exception: {e}"

    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass


# =========================================================
# 11. Summary output
# =========================================================
def print_summary() -> None:
    print("=" * SUMMARY_WIDTH)
    print("CHANGE PASSWORD RESULT")
    print("=" * SUMMARY_WIDTH)
    print(f"TOTAL      : {RESULT['TOTAL']}")
    print(f"SUCCESS    : {RESULT['SUCCESS']}")
    print(f"SKIPPED    : {RESULT['SKIPPED']}")
    print(f"AUTH_FAIL  : {RESULT['AUTH_FAIL']}")
    print(f"TIMEOUT    : {RESULT['TIMEOUT']}")
    print(f"ERROR      : {RESULT['ERROR']}")
    print("=" * SUMMARY_WIDTH)


# =========================================================
# 12. Main
# =========================================================
def main() -> None:
    cfg = load_config()
    validate_config(cfg)
    init_run_log(cfg)

    print("=" * SUMMARY_WIDTH)
    print("START CHANGE PASSWORD JOB")
    print("=" * SUMMARY_WIDTH)

    append_run_log(cfg, "=" * SUMMARY_WIDTH)
    append_run_log(cfg, "DEVICE PROCESS START")
    append_run_log(cfg, "=" * SUMMARY_WIDTH)

    for device in DEVICES:
        RESULT["TOTAL"] += 1

        status, log_text, summary_message = rotate_account_on_switch(device, cfg)
        log_file_path = write_device_log(cfg, device, log_text)

        RESULT_ROWS.append(
            {
                "name": device.name,
                "host": device.host,
                "status": status,
                "message": summary_message,
                "log_file": log_file_path,
            }
        )

        if status == STATUS_SUCCESS:
            RESULT["SUCCESS"] += 1
        elif status == STATUS_SKIPPED:
            RESULT["SKIPPED"] += 1
        elif status == STATUS_AUTH_FAIL:
            RESULT["AUTH_FAIL"] += 1
        elif status == STATUS_TIMEOUT:
            RESULT["TIMEOUT"] += 1
        else:
            RESULT["ERROR"] += 1
            append_failed_log(cfg, log_text)

        line = f"[{status:<9}] {device.name:<10} {device.host} - {summary_message}"
        print(line)
        append_run_log(cfg, line)

    save_result_csv(cfg)

    append_run_log(cfg, "")
    append_run_log(cfg, "=" * SUMMARY_WIDTH)
    append_run_log(cfg, "CHANGE PASSWORD RESULT")
    append_run_log(cfg, "=" * SUMMARY_WIDTH)
    append_run_log(cfg, f"TOTAL      : {RESULT['TOTAL']}")
    append_run_log(cfg, f"SUCCESS    : {RESULT['SUCCESS']}")
    append_run_log(cfg, f"SKIPPED    : {RESULT['SKIPPED']}")
    append_run_log(cfg, f"AUTH_FAIL  : {RESULT['AUTH_FAIL']}")
    append_run_log(cfg, f"TIMEOUT    : {RESULT['TIMEOUT']}")
    append_run_log(cfg, f"ERROR      : {RESULT['ERROR']}")
    append_run_log(cfg, "=" * SUMMARY_WIDTH)
    append_run_log(cfg, f"END TIME   : {now_str()}")

    print_summary()
    print(f"RUN LOG   : {cfg.run_log_path}")
    print(f"FAILED LOG: {cfg.failed_log_path}")
    print(f"RESULT CSV: {cfg.result_csv_path}")
    print(f"LOG DIR   : {cfg.log_dir}")

    append_run_log(cfg, f"RUN LOG   : {cfg.run_log_path}")
    append_run_log(cfg, f"FAILED LOG: {cfg.failed_log_path}")
    append_run_log(cfg, f"RESULT CSV: {cfg.result_csv_path}")
    append_run_log(cfg, f"LOG DIR   : {cfg.log_dir}")

    git_ok, git_msg = git_commit_only(cfg)
    print(f"GIT       : {git_msg}")
    append_run_log(cfg, f"GIT       : {git_msg}")


if __name__ == "__main__":
    main()
