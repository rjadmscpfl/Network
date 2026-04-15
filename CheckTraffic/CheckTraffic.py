import os
import re
import csv
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoTimeoutException, NetmikoAuthenticationException

load_dotenv()

# =========================================================
# Configuration
# =========================================================
BASE_DIR = r"D:\Backup Automation\Network\CheckTraffic"
INPUT_CSV = os.path.join(BASE_DIR, "devices.csv")

LOG_FILE = ""  # 실행마다 main()에서 갱신

SSH_USERNAME = os.getenv("SSH_USERNAME", "")
SSH_PASSWORD = os.getenv("SSH_PASSWORD", "")
ENABLE_PASSWORD = os.getenv("ENABLE_PASSWORD", "")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
CONNECT_TIMEOUT = int(os.getenv("CONNECT_TIMEOUT", "10"))
GLOBAL_DELAY_FACTOR = float(os.getenv("GLOBAL_DELAY_FACTOR", "2"))

SKIP_TRUNK_PORTS = os.getenv("SKIP_TRUNK_PORTS", "true").lower() == "true"

LOW_TRAFFIC_BYTES_THRESHOLD = int(os.getenv("LOW_TRAFFIC_BYTES_THRESHOLD", "1024"))
LOW_TRAFFIC_PACKETS_THRESHOLD = int(os.getenv("LOW_TRAFFIC_PACKETS_THRESHOLD", "5"))
ENABLE_PACKET_THRESHOLD = os.getenv("ENABLE_PACKET_THRESHOLD", "true").lower() == "true"

STATUS_OK = "OK"
STATUS_NO_TRAFFIC = "NO_TRAFFIC"
STATUS_LOW_TRAFFIC = "LOW_TRAFFIC"
STATUS_ERROR = "ERROR"
STATUS_SKIP = "SKIP"

EXCLUDE_DESC_KEYWORDS = [
    "uplink",
    "up-link",
    "server",
    "firewall",
    "ap",
    "wlc",
    "core",
    "trunk"
]

EXCLUDE_INTERFACE_PREFIXES = (
    "Po",   # Port-channel
    "Vl",   # VLAN
    "Lo",   # Loopback
    "Tu",   # Tunnel
    "Ap",   # AppGig 등
    "Mg",   # Mgmt
    "Nu",   # NVE 등
)

# =========================================================
# Logging
# =========================================================
def write_log(msg: str) -> None:
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def wait_with_progress(seconds: int, device_name: str) -> None:
    write_log(f"[INFO] Waiting {seconds} seconds before second snapshot: {device_name}")

    for remaining in range(seconds, 0, -1):
        if remaining == seconds or remaining % 10 == 0 or remaining <= 5:
            write_log(f"[INFO] {device_name} - second snapshot starts in {remaining}s")
        time.sleep(1)

    write_log(f"[INFO] Wait finished. Starting second snapshot: {device_name}")

# =========================================================
# Helpers
# =========================================================
def load_devices(csv_file: str) -> List[Dict[str, str]]:
    devices = []
    with open(csv_file, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            devices.append({
                "name": row["name"].strip(),
                "host": row["host"].strip(),
                "device_type": row.get("device_type", "cisco_ios").strip() or "cisco_ios",
            })
    return devices

def normalize_interface_name(port: str) -> str:
    port = port.strip()

    if port.startswith("GigabitEthernet"):
        return port
    if port.startswith("TenGigabitEthernet"):
        return port
    if port.startswith("TwentyFiveGigE"):
        return port
    if port.startswith("FortyGigabitEthernet"):
        return port
    if port.startswith("HundredGigE"):
        return port
    if port.startswith("FastEthernet"):
        return port

    if port.startswith("Twe"):
        return "TwentyFiveGigE" + port[len("Twe"):]
    if port.startswith("Hu"):
        return "HundredGigE" + port[len("Hu"):]
    if port.startswith("Fo"):
        return "FortyGigabitEthernet" + port[len("Fo"):]
    if port.startswith("Te"):
        return "TenGigabitEthernet" + port[len("Te"):]
    if port.startswith("Gi"):
        return "GigabitEthernet" + port[len("Gi"):]
    if port.startswith("Fa"):
        return "FastEthernet" + port[len("Fa"):]

    return port

def should_exclude_interface(port: str) -> bool:
    return port.startswith(EXCLUDE_INTERFACE_PREFIXES)

def parse_interfaces_status(output: str) -> List[Dict[str, str]]:
    """
    show interfaces status 출력에서 connected 포트만 추출
    """
    results = []
    lines = output.splitlines()

    for line in lines:
        line = line.rstrip()
        if not line or line.startswith("Port ") or line.startswith("---"):
            continue

        if " connected " not in f" {line} ":
            continue

        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 3:
            continue

        port = parts[0].strip()
        name = parts[1].strip() if len(parts) >= 2 else ""
        status = parts[2].strip() if len(parts) >= 3 else ""
        vlan = parts[3].strip() if len(parts) >= 4 else ""

        results.append({
            "port": port,
            "name": name,
            "status": status,
            "vlan": vlan,
            "raw": line
        })

    return results

def parse_switchport_mode(output: str) -> Tuple[str, str]:
    admin_mode = ""
    operational_mode = ""

    for line in output.splitlines():
        s = line.strip()
        if s.startswith("Administrative Mode:"):
            admin_mode = s.split(":", 1)[1].strip()
        elif s.startswith("Operational Mode:"):
            operational_mode = s.split(":", 1)[1].strip()

    return admin_mode, operational_mode

def parse_interface_counters(output: str) -> Dict[str, Optional[int]]:
    result = {
        "is_up": False,
        "description": "",
        "input_packets": None,
        "input_bytes": None,
        "output_packets": None,
        "output_bytes": None,
        "input_rate_bps": None,
        "output_rate_bps": None,
    }

    lines = output.splitlines()
    if not lines:
        return result

    first_line = lines[0].strip()
    if " is up, line protocol is up" in first_line:
        result["is_up"] = True

    m_desc = re.search(r"Description:\s*(.+)", output)
    if m_desc:
        result["description"] = m_desc.group(1).strip()

    m_in_rate = re.search(r"5 minute input rate\s+(\d+)\s+bits/sec", output)
    if m_in_rate:
        result["input_rate_bps"] = int(m_in_rate.group(1))

    m_out_rate = re.search(r"5 minute output rate\s+(\d+)\s+bits/sec", output)
    if m_out_rate:
        result["output_rate_bps"] = int(m_out_rate.group(1))

    m_in = re.search(r"(\d+)\s+packets input,\s+(\d+)\s+bytes", output)
    if m_in:
        result["input_packets"] = int(m_in.group(1))
        result["input_bytes"] = int(m_in.group(2))

    m_out = re.search(r"(\d+)\s+packets output,\s+(\d+)\s+bytes", output)
    if m_out:
        result["output_packets"] = int(m_out.group(1))
        result["output_bytes"] = int(m_out.group(2))

    return result

def is_excluded_by_description(desc: str) -> bool:
    if not desc:
        return False
    desc_lower = desc.lower()
    return any(keyword in desc_lower for keyword in EXCLUDE_DESC_KEYWORDS)

def safe_int(value: Optional[int]) -> int:
    return 0 if value is None else value

# =========================================================
# Traffic Classification
# =========================================================
def classify_traffic(
    in_bytes_delta: int,
    out_bytes_delta: int,
    in_pkts_delta: int,
    out_pkts_delta: int
) -> Tuple[str, str]:
    total_bytes = in_bytes_delta + out_bytes_delta
    total_pkts = in_pkts_delta + out_pkts_delta

    if in_bytes_delta == 0 and out_bytes_delta == 0:
        return STATUS_NO_TRAFFIC, f"No traffic during {CHECK_INTERVAL}s"

    if ENABLE_PACKET_THRESHOLD:
        if total_bytes <= LOW_TRAFFIC_BYTES_THRESHOLD and total_pkts <= LOW_TRAFFIC_PACKETS_THRESHOLD:
            return (
                STATUS_LOW_TRAFFIC,
                f"Low traffic: total_bytes={total_bytes}, total_pkts={total_pkts}, "
                f"threshold_bytes<={LOW_TRAFFIC_BYTES_THRESHOLD}, "
                f"threshold_pkts<={LOW_TRAFFIC_PACKETS_THRESHOLD}"
            )
    else:
        if total_bytes <= LOW_TRAFFIC_BYTES_THRESHOLD:
            return (
                STATUS_LOW_TRAFFIC,
                f"Low traffic: total_bytes={total_bytes}, "
                f"threshold_bytes<={LOW_TRAFFIC_BYTES_THRESHOLD}"
            )

    return STATUS_OK, f"Traffic detected: total_bytes={total_bytes}, total_pkts={total_pkts}"

# =========================================================
# Device Connection
# =========================================================
def connect_device(device: Dict[str, str]):
    conn = ConnectHandler(
        device_type=device["device_type"],
        host=device["host"],
        username=SSH_USERNAME,
        password=SSH_PASSWORD,
        secret=ENABLE_PASSWORD,
        timeout=CONNECT_TIMEOUT,
        global_delay_factor=GLOBAL_DELAY_FACTOR,
    )
    if ENABLE_PASSWORD:
        conn.enable()

    conn.send_command("terminal length 0", strip_prompt=False, strip_command=False)
    return conn

def get_connected_ports(conn) -> List[Dict[str, str]]:
    output = conn.send_command(
        "show interfaces status",
        expect_string=r"#",
        read_timeout=60
    )
    ports = parse_interfaces_status(output)

    filtered = []
    for item in ports:
        port = item["port"]
        if should_exclude_interface(port):
            continue
        filtered.append(item)

    return filtered

def enrich_port_info(conn, port_item: Dict[str, str]) -> Dict[str, str]:
    port_short = port_item["port"]
    port_full = normalize_interface_name(port_short)

    int_output = conn.send_command(
        f"show interfaces {port_full}",
        expect_string=r"#",
        read_timeout=60
    )
    counters = parse_interface_counters(int_output)

    admin_mode = ""
    operational_mode = ""
    try:
        sw_output = conn.send_command(
            f"show interfaces {port_full} switchport",
            expect_string=r"#",
            read_timeout=60
        )
        admin_mode, operational_mode = parse_switchport_mode(sw_output)
    except Exception:
        pass

    result = {
        **port_item,
        "port_full": port_full,
        "is_up": counters["is_up"],
        "description": counters["description"],
        "input_packets": safe_int(counters["input_packets"]),
        "input_bytes": safe_int(counters["input_bytes"]),
        "output_packets": safe_int(counters["output_packets"]),
        "output_bytes": safe_int(counters["output_bytes"]),
        "input_rate_bps": safe_int(counters["input_rate_bps"]),
        "output_rate_bps": safe_int(counters["output_rate_bps"]),
        "admin_mode": admin_mode,
        "operational_mode": operational_mode,
    }
    return result

def collect_first_snapshot(conn, ports: List[Dict[str, str]]) -> List[Dict[str, str]]:
    first = []

    for item in ports:
        write_log(f"[INFO] First snapshot collecting: {item['port']}")
        info = enrich_port_info(conn, item)

        if not info["is_up"]:
            continue

        if SKIP_TRUNK_PORTS:
            op_mode = (info.get("operational_mode") or "").lower()
            admin_mode = (info.get("admin_mode") or "").lower()
            if "trunk" in op_mode or "trunk" in admin_mode:
                write_log(f"[INFO] Skip trunk port: {item['port']}")
                continue

        if is_excluded_by_description(info.get("description", "")):
            write_log(f"[INFO] Skip by description: {item['port']} / {info.get('description', '')}")
            continue

        first.append(info)

    return first

def collect_second_snapshot(conn, port_full: str) -> Dict[str, int]:
    output = conn.send_command(
        f"show interfaces {port_full}",
        expect_string=r"#",
        read_timeout=60
    )
    data = parse_interface_counters(output)

    return {
        "input_packets": safe_int(data["input_packets"]),
        "input_bytes": safe_int(data["input_bytes"]),
        "output_packets": safe_int(data["output_packets"]),
        "output_bytes": safe_int(data["output_bytes"]),
        "input_rate_bps": safe_int(data["input_rate_bps"]),
        "output_rate_bps": safe_int(data["output_rate_bps"]),
        "is_up": data["is_up"],
    }

# =========================================================
# Device Analysis
# =========================================================
def analyze_device(device: Dict[str, str]) -> List[Dict[str, str]]:
    results = []
    conn = None

    try:
        write_log(f"[INFO] Connecting: {device['name']} ({device['host']})")
        conn = connect_device(device)

        connected_ports = get_connected_ports(conn)
        write_log(f"[INFO] {device['name']} connected ports found: {len(connected_ports)}")

        if not connected_ports:
            return results

        first_snapshot = collect_first_snapshot(conn, connected_ports)
        write_log(f"[INFO] {device['name']} candidate access ports: {len(first_snapshot)}")

        if not first_snapshot:
            return results

        write_log(f"[INFO] First snapshot completed: {device['name']}")
        wait_with_progress(CHECK_INTERVAL, device['name'])

        for item in first_snapshot:
            write_log(f"[INFO] Second snapshot collecting: {device['name']} / {item['port']}")
            second = collect_second_snapshot(conn, item["port_full"])

            if not second["is_up"]:
                results.append({
                    "device_name": device["name"],
                    "host": device["host"],
                    "port": item["port"],
                    "description": item["description"],
                    "vlan": item["vlan"],
                    "admin_mode": item["admin_mode"],
                    "operational_mode": item["operational_mode"],
                    "status": STATUS_SKIP,
                    "reason": "Port went down during interval",
                    "in_bytes_delta": "",
                    "out_bytes_delta": "",
                    "total_bytes_delta": "",
                    "in_pkts_delta": "",
                    "out_pkts_delta": "",
                    "total_pkts_delta": "",
                    "first_in_rate_bps": item["input_rate_bps"],
                    "first_out_rate_bps": item["output_rate_bps"],
                    "second_in_rate_bps": second["input_rate_bps"],
                    "second_out_rate_bps": second["output_rate_bps"],
                })
                continue

            in_bytes_delta = second["input_bytes"] - item["input_bytes"]
            out_bytes_delta = second["output_bytes"] - item["output_bytes"]
            total_bytes_delta = in_bytes_delta + out_bytes_delta

            in_pkts_delta = second["input_packets"] - item["input_packets"]
            out_pkts_delta = second["output_packets"] - item["output_packets"]
            total_pkts_delta = in_pkts_delta + out_pkts_delta

            status, reason = classify_traffic(
                in_bytes_delta=in_bytes_delta,
                out_bytes_delta=out_bytes_delta,
                in_pkts_delta=in_pkts_delta,
                out_pkts_delta=out_pkts_delta
            )

            write_log(
                f"[INFO] Result {device['name']} {item['port']} - "
                f"in_bytes_delta={in_bytes_delta}, out_bytes_delta={out_bytes_delta}, "
                f"total_bytes_delta={total_bytes_delta}, total_pkts_delta={total_pkts_delta}, "
                f"status={status}"
            )

            results.append({
                "device_name": device["name"],
                "host": device["host"],
                "port": item["port"],
                "description": item["description"],
                "vlan": item["vlan"],
                "admin_mode": item["admin_mode"],
                "operational_mode": item["operational_mode"],
                "status": status,
                "reason": reason,
                "in_bytes_delta": in_bytes_delta,
                "out_bytes_delta": out_bytes_delta,
                "total_bytes_delta": total_bytes_delta,
                "in_pkts_delta": in_pkts_delta,
                "out_pkts_delta": out_pkts_delta,
                "total_pkts_delta": total_pkts_delta,
                "first_in_rate_bps": item["input_rate_bps"],
                "first_out_rate_bps": item["output_rate_bps"],
                "second_in_rate_bps": second["input_rate_bps"],
                "second_out_rate_bps": second["output_rate_bps"],
            })

    except NetmikoAuthenticationException:
        write_log(f"[ERROR] AUTH FAIL: {device['name']} ({device['host']})")
        results.append({
            "device_name": device["name"],
            "host": device["host"],
            "port": "",
            "description": "",
            "vlan": "",
            "admin_mode": "",
            "operational_mode": "",
            "status": STATUS_ERROR,
            "reason": "Authentication failed",
            "in_bytes_delta": "",
            "out_bytes_delta": "",
            "total_bytes_delta": "",
            "in_pkts_delta": "",
            "out_pkts_delta": "",
            "total_pkts_delta": "",
            "first_in_rate_bps": "",
            "first_out_rate_bps": "",
            "second_in_rate_bps": "",
            "second_out_rate_bps": "",
        })
    except NetmikoTimeoutException:
        write_log(f"[ERROR] TIMEOUT: {device['name']} ({device['host']})")
        results.append({
            "device_name": device["name"],
            "host": device["host"],
            "port": "",
            "description": "",
            "vlan": "",
            "admin_mode": "",
            "operational_mode": "",
            "status": STATUS_ERROR,
            "reason": "Connection timeout",
            "in_bytes_delta": "",
            "out_bytes_delta": "",
            "total_bytes_delta": "",
            "in_pkts_delta": "",
            "out_pkts_delta": "",
            "total_pkts_delta": "",
            "first_in_rate_bps": "",
            "first_out_rate_bps": "",
            "second_in_rate_bps": "",
            "second_out_rate_bps": "",
        })
    except Exception as e:
        write_log(f"[ERROR] {device['name']} ({device['host']}) - {e}")
        results.append({
            "device_name": device["name"],
            "host": device["host"],
            "port": "",
            "description": "",
            "vlan": "",
            "admin_mode": "",
            "operational_mode": "",
            "status": STATUS_ERROR,
            "reason": str(e),
            "in_bytes_delta": "",
            "out_bytes_delta": "",
            "total_bytes_delta": "",
            "in_pkts_delta": "",
            "out_pkts_delta": "",
            "total_pkts_delta": "",
            "first_in_rate_bps": "",
            "first_out_rate_bps": "",
            "second_in_rate_bps": "",
            "second_out_rate_bps": "",
        })
    finally:
        if conn:
            conn.disconnect()

    return results

# =========================================================
# Save / Summary
# =========================================================
def save_results(rows: List[Dict[str, str]], filename: str) -> None:
    fieldnames = [
        "device_name", "host", "port", "description", "vlan",
        "admin_mode", "operational_mode",
        "status", "reason",
        "in_bytes_delta", "out_bytes_delta", "total_bytes_delta",
        "in_pkts_delta", "out_pkts_delta", "total_pkts_delta",
        "first_in_rate_bps", "first_out_rate_bps",
        "second_in_rate_bps", "second_out_rate_bps"
    ]

    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

def print_summary(rows: List[Dict[str, str]]) -> None:
    total = len(rows)
    no_traffic = sum(1 for r in rows if r["status"] == STATUS_NO_TRAFFIC)
    low_traffic = sum(1 for r in rows if r["status"] == STATUS_LOW_TRAFFIC)
    ok = sum(1 for r in rows if r["status"] == STATUS_OK)
    error = sum(1 for r in rows if r["status"] == STATUS_ERROR)
    skip = sum(1 for r in rows if r["status"] == STATUS_SKIP)

    print("=" * 100)
    print("UP PORT TRAFFIC THRESHOLD CHECK RESULT")
    print("=" * 100)
    print(f"TOTAL         : {total}")
    print(f"NO_TRAFFIC    : {no_traffic}")
    print(f"LOW_TRAFFIC   : {low_traffic}")
    print(f"OK            : {ok}")
    print(f"SKIP          : {skip}")
    print(f"ERROR         : {error}")
    print("=" * 100)

    if no_traffic > 0 or low_traffic > 0:
        print("PORT ALERT LIST")
        print("=" * 100)
        for r in rows:
            if r["status"] in [STATUS_NO_TRAFFIC, STATUS_LOW_TRAFFIC]:
                print(
                    f"[{r['status']}] {r['device_name']:15} {r['host']:15} "
                    f"{r['port']:20} VLAN={r['vlan']:8} "
                    f"BYTES={str(r['total_bytes_delta']):10} "
                    f"PKTS={str(r['total_pkts_delta']):8} "
                    f"DESC={r['description']}"
                )
        print("=" * 100)

# =========================================================
# Main
# =========================================================
def main():
    global LOG_FILE

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    run_time = now.strftime("%H-%M-%S")

    output_dir = os.path.join(BASE_DIR, "outputs", today)
    log_dir = os.path.join(BASE_DIR, "logs", today)
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)

    result_csv = os.path.join(output_dir, f"no_traffic_result_{run_time}.csv")
    LOG_FILE = os.path.join(log_dir, f"run_{run_time}.log")

    write_log("=" * 100)
    write_log("START UP PORT TRAFFIC THRESHOLD CHECK")
    write_log("=" * 100)
    write_log(
        f"[INFO] CHECK_INTERVAL={CHECK_INTERVAL}, "
        f"LOW_TRAFFIC_BYTES_THRESHOLD={LOW_TRAFFIC_BYTES_THRESHOLD}, "
        f"LOW_TRAFFIC_PACKETS_THRESHOLD={LOW_TRAFFIC_PACKETS_THRESHOLD}, "
        f"ENABLE_PACKET_THRESHOLD={ENABLE_PACKET_THRESHOLD}"
    )

    devices = load_devices(INPUT_CSV)
    all_results = []

    for device in devices:
        device_results = analyze_device(device)
        all_results.extend(device_results)

    save_results(all_results, result_csv)
    print_summary(all_results)

    write_log(f"[INFO] Result CSV saved: {result_csv}")
    write_log(f"[INFO] Log saved: {LOG_FILE}")

RERUN_INTERVAL = 60  # 재실행 대기 시간(초)

if __name__ == "__main__":
    while True:
        main()
        write_log(f"[INFO] Next run starts in {RERUN_INTERVAL} seconds...")
        for remaining in range(RERUN_INTERVAL, 0, -1):
            print(f"\r[INFO] Next run in {remaining:3d}s...", end="", flush=True)
            time.sleep(1)
        print()
        write_log("[INFO] Restarting...")