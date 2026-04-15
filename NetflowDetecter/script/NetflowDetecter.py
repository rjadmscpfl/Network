import os
import re
import traceback
from datetime import datetime
from typing import Dict, List, Set

import pandas as pd
from dotenv import load_dotenv
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoTimeoutException, NetmikoAuthenticationException


# =========================================================
# 1. 환경변수 로드
# =========================================================
load_dotenv()

BASE_DIR = r"D:\Backup Automation\Network\NetflowDetecter"
OUTPUT_BASE_DIR = os.path.join(BASE_DIR, "output")
LOG_BASE_DIR = os.path.join(BASE_DIR, "logs")

NOW = datetime.now()
TODAY = NOW.strftime("%Y-%m-%d")
RUN_TIME = NOW.strftime("%H-%M-%S")

OUTPUT_DIR = os.path.join(OUTPUT_BASE_DIR, TODAY)
LOG_DIR = os.path.join(LOG_BASE_DIR, TODAY)

RESULT_CSV = os.path.join(OUTPUT_DIR, f"mac_count_summary_{RUN_TIME}.csv")
DEBUG_TXT = os.path.join(OUTPUT_DIR, f"mac_table_raw_{RUN_TIME}.txt")
LOG_FILE = os.path.join(LOG_DIR, f"mac_count_check_{RUN_TIME}.log")

SWITCH_HOST = os.getenv("SWITCH_HOST", "").strip()
SWITCH_USERNAME = os.getenv("SWITCH_USERNAME", "").strip()
SWITCH_PASSWORD = os.getenv("SWITCH_PASSWORD", "").strip()
SWITCH_ENABLE_SECRET = os.getenv("SWITCH_ENABLE_SECRET", "").strip()

CONNECT_TIMEOUT = int(os.getenv("CONNECT_TIMEOUT", "8"))
GLOBAL_DELAY_FACTOR = float(os.getenv("GLOBAL_DELAY_FACTOR", "1"))

EXCLUDE_NON_EDGE_PORTS = os.getenv("EXCLUDE_NON_EDGE_PORTS", "1").strip() == "1"
MIN_MAC_COUNT = int(os.getenv("MIN_MAC_COUNT", "2"))


# =========================================================
# 2. 공통 함수
# =========================================================
def ensure_directories() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)


def log(message: str, also_print: bool = True) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {message}"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    if also_print:
        print(line)


def log_separator(char: str = "=", length: int = 100) -> None:
    log(char * length)


def append_debug_text(title: str, content: str) -> None:
    with open(DEBUG_TXT, "a", encoding="utf-8") as f:
        f.write("=" * 120 + "\n")
        f.write(title + "\n")
        f.write("=" * 120 + "\n")
        f.write(content if content else "(empty output)")
        f.write("\n\n")


def normalize_interface_name(ifname: str) -> str:
    if not ifname:
        return ""

    s = str(ifname).strip()

    replacements = {
        "TwentyFiveGigabitEthernet": "Twe",
        "TwentyFiveGigE": "Twe",
        "HundredGigabitEthernet": "Hu",
        "HundredGigE": "Hu",
        "FortyGigabitEthernet": "Fo",
        "GigabitEthernet": "Gi",
        "TenGigabitEthernet": "Te",
        "FastEthernet": "Fa",
        "Port-channel": "Po",
        "Ethernet": "Eth",
    }

    for old, new in replacements.items():
        if s.startswith(old):
            s = s.replace(old, new, 1)
            break

    return s.lower()


def is_edge_like_port(port: str) -> bool:
    norm = normalize_interface_name(port)
    if not norm:
        return False

    if re.match(r"^(gi|te|fa|eth)\d+(/\d+){1,3}$", norm):
        return True

    return False


def classify_suspicion(mac_count: int) -> Dict[str, str]:
    if mac_count >= 3:
        return {
            "suspicion": "HIGH",
            "note": "Multiple MACs on port - shared router / bridge / hub suspected"
        }
    if mac_count == 2:
        return {
            "suspicion": "MEDIUM",
            "note": "2 MACs on port - verify IP Phone / daisy chain / bridge"
        }
    if mac_count == 1:
        return {
            "suspicion": "LOW",
            "note": "Single MAC on port"
        }
    return {
        "suspicion": "UNKNOWN",
        "note": "No MAC learned or parsing issue"
    }


def format_mac_list_for_console(mac_list_str: str) -> str:
    """
    콘솔 출력용 MAC 리스트 번호 표시
    예:
    aa, bb, cc
    ->
    1. aa
    2. bb
    3. cc
    """
    if not mac_list_str:
        return ""

    macs = [m.strip() for m in mac_list_str.split(",") if m.strip()]
    lines = [f"{idx}. {mac}" for idx, mac in enumerate(macs, start=1)]
    return "\n".join(lines)


# =========================================================
# 3. 스위치 접속
# =========================================================
def connect_switch():
    if not SWITCH_HOST or not SWITCH_USERNAME or not SWITCH_PASSWORD:
        raise ValueError("SWITCH_HOST / SWITCH_USERNAME / SWITCH_PASSWORD 를 .env에 입력해야 합니다.")

    device = {
        "device_type": "cisco_ios",
        "host": SWITCH_HOST,
        "username": SWITCH_USERNAME,
        "password": SWITCH_PASSWORD,
        "secret": SWITCH_ENABLE_SECRET,
        "timeout": CONNECT_TIMEOUT,
        "global_delay_factor": GLOBAL_DELAY_FACTOR,
        "fast_cli": False,
    }

    conn = ConnectHandler(**device)
    if SWITCH_ENABLE_SECRET:
        conn.enable()

    conn.send_command("terminal length 0")
    return conn


# =========================================================
# 4. MAC 테이블 파싱
# =========================================================
def parse_full_mac_table(output: str) -> Dict[str, Set[str]]:
    port_macs: Dict[str, Set[str]] = {}

    for line in output.splitlines():
        mac_match = re.search(r"([0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4})", line)
        if not mac_match:
            continue

        mac_addr = mac_match.group(1).lower()
        tokens = line.split()

        port = ""
        for token in reversed(tokens):
            norm = normalize_interface_name(token)
            if re.match(r"^(gi|te|fa|po|eth|fo|hu|twe)\d+(/\d+){0,3}$", norm):
                port = token
                break

        if not port:
            continue

        if EXCLUDE_NON_EDGE_PORTS and not is_edge_like_port(port):
            continue

        port_macs.setdefault(port, set()).add(mac_addr)

    return port_macs


# =========================================================
# 5. 메인
# =========================================================
def main() -> None:
    ensure_directories()

    log_separator("=")
    log("START MAC COUNT SUMMARY")
    log_separator("=")
    log(f"[INFO] SWITCH HOST   : {SWITCH_HOST}")
    log(f"[INFO] OUTPUT DIR    : {OUTPUT_DIR}")
    log(f"[INFO] LOG FILE      : {LOG_FILE}")
    log(f"[INFO] DEBUG TXT     : {DEBUG_TXT}")
    log(f"[INFO] MIN_MAC_COUNT : {MIN_MAC_COUNT}")
    log(f"[INFO] EXCLUDE_NON_EDGE_PORTS : {EXCLUDE_NON_EDGE_PORTS}")

    conn = None

    try:
        try:
            conn = connect_switch()
            log(f"[OK] Connected to switch: {SWITCH_HOST}")
        except (NetmikoTimeoutException, NetmikoAuthenticationException) as e:
            raise RuntimeError(f"스위치 SSH 접속 실패: {e}")

        log_separator("-")
        log("STEP 1. GET FULL MAC TABLE")
        log_separator("-")

        mac_cmd = "show mac address-table"
        mac_out = conn.send_command(mac_cmd)
        append_debug_text(mac_cmd, mac_out)

        port_macs = parse_full_mac_table(mac_out)

        if not port_macs:
            log("[WARN] 파싱된 MAC 테이블 결과가 없습니다.")
            pd.DataFrame(columns=[
                "switch_host",
                "access_port",
                "mac_count",
                "mac_list",
                "suspicion",
                "note",
            ]).to_csv(RESULT_CSV, index=False, encoding="utf-8-sig")
            log(f"[OK] Empty result CSV saved: {RESULT_CSV}")
            return

        log_separator("-")
        log("STEP 2. BUILD SUMMARY")
        log_separator("-")

        results: List[Dict[str, str]] = []

        for port, macs in sorted(port_macs.items(), key=lambda x: (-len(x[1]), normalize_interface_name(x[0]))):
            mac_count = len(macs)
            if mac_count < MIN_MAC_COUNT:
                continue

            classified = classify_suspicion(mac_count)

            row = {
                "switch_host": SWITCH_HOST,
                "access_port": port,
                "mac_count": mac_count,
                "mac_list": ", ".join(sorted(macs)),
                "suspicion": classified["suspicion"],
                "note": classified["note"],
            }
            results.append(row)

            log(
                f"[PORT] {port} "
                f"MAC_COUNT={mac_count} "
                f"SUSPICION={classified['suspicion']} "
                f"MACS={', '.join(sorted(macs))}"
            )

        result_df = pd.DataFrame(results)

        if result_df.empty:
            log("[INFO] 조건에 맞는 포트가 없습니다.")
            result_df = pd.DataFrame(columns=[
                "switch_host",
                "access_port",
                "mac_count",
                "mac_list",
                "suspicion",
                "note",
            ])

        result_df.to_csv(RESULT_CSV, index=False, encoding="utf-8-sig")
        log(f"[OK] Result CSV saved: {RESULT_CSV}")

        log_separator("-")
        log("SUMMARY")
        log_separator("-")

        high_count = len(result_df[result_df["suspicion"] == "HIGH"])
        medium_count = len(result_df[result_df["suspicion"] == "MEDIUM"])
        low_count = len(result_df[result_df["suspicion"] == "LOW"])

        log(f"HIGH   : {high_count}")
        log(f"MEDIUM : {medium_count}")
        log(f"LOW    : {low_count}")

        if not result_df.empty:
            print("\n" + "=" * 120)
            print("MAC COUNT SUMMARY")
            print("=" * 120)

            for _, row in result_df.iterrows():
                print(f"PORT       : {row['access_port']}")
                print(f"MAC COUNT  : {row['mac_count']}")
                print(f"SUSPICION  : {row['suspicion']}")
                print(f"NOTE       : {row['note']}")
                print("MAC LIST   :")
                print(format_mac_list_for_console(row["mac_list"]))
                print("-" * 120)

        log_separator("=")
        log("END MAC COUNT SUMMARY")
        log_separator("=")

    except Exception as e:
        log(f"[ERROR] {e}")
        log(traceback.format_exc(), also_print=False)
        raise

    finally:
        if conn is not None:
            try:
                conn.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    main()