"""
스위치 포트별 MAC 수 기반 공유기/허브 탐지
- .env 파일로 네트워크 대역·인증 정보 관리
- SNMP / SSH / demo 수집 방식 지원
- 복수 대역·복수 스위치 일괄 스캔
"""

import os
import re
import csv
import ipaddress
import argparse
from datetime import datetime
from collections import defaultdict
from pathlib import Path

# pip install python-dotenv
try:
    from dotenv import load_dotenv
    load_dotenv()                        # .env 파일 자동 로드
except ImportError:
    print("[경고] python-dotenv 미설치 → .env 로드 생략 (pip install python-dotenv)")

# pip install pysnmp
try:
    from pysnmp.hlapi import *
    SNMP_AVAILABLE = True
except ImportError:
    SNMP_AVAILABLE = False

# pip install netmiko
try:
    from netmiko import ConnectHandler
    NETMIKO_AVAILABLE = True
except ImportError:
    NETMIKO_AVAILABLE = False


# ── .env 값 읽기 ──────────────────────────────────────────────────────────────

def env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()

def env_list(key: str, default: str = "") -> list[str]:
    raw = env(key, default)
    return [v.strip() for v in raw.split(",") if v.strip()]

def env_int(key: str, default: int = 0) -> int:
    try:
        return int(env(key, str(default)))
    except ValueError:
        return default

def env_bool(key: str, default: bool = False) -> bool:
    return env(key, str(default)).lower() in ("1", "true", "yes")


# ── 설정 로드 ─────────────────────────────────────────────────────────────────

NETWORK_RANGES  = env_list("NETWORK_RANGES", "192.168.75.0/24")
SWITCH_IPS      = env_list("SWITCH_IPS")           # 비어있으면 대역에서 자동 추출

SNMP_COMMUNITY  = env("SNMP_COMMUNITY",  "public")
SNMP_PORT       = env_int("SNMP_PORT",   161)

SSH_USERNAME    = env("SSH_USERNAME",    "admin")
SSH_PASSWORD    = env("SSH_PASSWORD",    "password")
SSH_SECRET      = env("SSH_ENABLE_SECRET", "")
SSH_DEVICE_TYPE = env("SSH_DEVICE_TYPE", "cisco_ios")

THRESHOLD       = env_int("MAC_THRESHOLD", 3)
COLLECT_METHOD  = env("COLLECT_METHOD",  "demo")
SAVE_CSV        = env_bool("SAVE_CSV",    True)


# ── 네트워크 대역에서 스위치 IP 추출 ──────────────────────────────────────────

def expand_networks(ranges: list[str]) -> list[str]:
    """
    SWITCH_IPS 가 지정되어 있으면 그대로 사용.
    없으면 NETWORK_RANGES 각 서브넷의 첫 번째 호스트(게이트웨이/L3스위치)를 사용.
    """
    if SWITCH_IPS:
        return SWITCH_IPS

    candidates = []
    for cidr in ranges:
        try:
            net   = ipaddress.ip_network(cidr, strict=False)
            hosts = list(net.hosts())
            if hosts:
                candidates.append(str(hosts[0]))   # .1 주소 = 게이트웨이 가정
        except ValueError as e:
            print(f"[경고] 잘못된 네트워크 대역 '{cidr}': {e}")
    return candidates


def is_in_networks(ip: str, ranges: list[str]) -> bool:
    """IP가 설정된 네트워크 대역 중 하나에 속하는지 확인"""
    addr = ipaddress.ip_address(ip)
    for cidr in ranges:
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            pass
    return False


# ── SNMP OID ──────────────────────────────────────────────────────────────────

OID_FDB_MAC  = "1.3.6.1.2.1.17.4.3.1.1"
OID_FDB_PORT = "1.3.6.1.2.1.17.4.3.1.2"
OID_PORT_IDX = "1.3.6.1.2.1.17.1.4.1.2"
OID_IF_NAME  = "1.3.6.1.2.1.31.1.1.1.1"


def snmp_walk(ip, community, oid, port=161):
    result = {}
    for (errInd, errStat, _, varBinds) in nextCmd(
        SnmpEngine(),
        CommunityData(community, mpModel=1),
        UdpTransportTarget((ip, port), timeout=3, retries=2),
        ContextData(),
        ObjectType(ObjectIdentity(oid)),
        lexicographicMode=False,
    ):
        if errInd or errStat:
            break
        for vb in varBinds:
            key = str(vb[0]).split(oid.replace(".", r"\."))[- 1].lstrip(".")
            result[key] = vb[1]
    return result


def mac_from_index(index_str: str):
    parts = index_str.strip(".").split(".")
    if len(parts) < 6:
        return None
    return ":".join(f"{int(p):02x}" for p in parts[-6:])


def collect_snmp(ip: str) -> dict:
    print(f"  [SNMP] {ip} ...")
    raw_mac  = snmp_walk(ip, SNMP_COMMUNITY, OID_FDB_MAC,  SNMP_PORT)
    raw_port = snmp_walk(ip, SNMP_COMMUNITY, OID_FDB_PORT, SNMP_PORT)
    port_idx = snmp_walk(ip, SNMP_COMMUNITY, OID_PORT_IDX, SNMP_PORT)
    if_name  = snmp_walk(ip, SNMP_COMMUNITY, OID_IF_NAME,  SNMP_PORT)

    bridge_to_ifname = {
        bp: if_name.get(str(int(ifidx)), f"Port{bp}")
        for bp, ifidx in port_idx.items()
    }

    port_mac_map = defaultdict(list)
    for idx, _ in raw_mac.items():
        mac = mac_from_index(idx)
        bp  = str(raw_port.get(idx, "?"))
        if mac:
            port_mac_map[bridge_to_ifname.get(bp, f"Port{bp}")].append(mac)
    return dict(port_mac_map)


# ── SSH (Netmiko) ──────────────────────────────────────────────────────────────

MAC_LINE_RE = re.compile(
    r"(\S+)\s+([\da-f]{4}\.[\da-f]{4}\.[\da-f]{4})\s+\S+\s+(\S+)",
    re.IGNORECASE
)

def dot_to_colon(mac: str) -> str:
    c = mac.replace(".", "")
    return ":".join(c[i:i+2] for i in range(0, 12, 2))


def collect_ssh(ip: str) -> dict:
    print(f"  [SSH] {ip} ...")
    cfg = {
        "device_type": SSH_DEVICE_TYPE,
        "host":        ip,
        "username":    SSH_USERNAME,
        "password":    SSH_PASSWORD,
        "secret":      SSH_SECRET,
    }
    port_mac_map = defaultdict(list)
    try:
        with ConnectHandler(**cfg) as conn:
            if SSH_SECRET:
                conn.enable()
            output = conn.send_command("show mac address-table")
        for line in output.splitlines():
            m = MAC_LINE_RE.search(line)
            if m:
                port_mac_map[m.group(3)].append(dot_to_colon(m.group(2)))
    except Exception as e:
        print(f"  [오류] SSH 실패 ({ip}): {e}")
    return dict(port_mac_map)


# ── 데모 데이터 ───────────────────────────────────────────────────────────────

def demo_data(ip: str) -> dict:
    """IP 끝자리로 다른 샘플 생성 (멀티 스위치 시뮬레이션)"""
    last = int(ip.split(".")[-1])
    base = (last * 10) % 256
    return {
        "Gi0/1": [f"aa:bb:cc:{base:02x}:00:01"],
        "Gi0/2": [f"aa:bb:cc:{base:02x}:00:{i:02x}" for i in range(2, 7)],    # 의심
        "Gi0/3": [f"aa:bb:cc:{base:02x}:00:{i:02x}" for i in range(7, 9)],
        "Gi0/4": [f"aa:bb:cc:{base:02x}:00:09"],
        "Gi0/5": [f"aa:bb:cc:{base:02x}:00:{i:02x}" for i in range(10, 18)],  # 의심
    }


# ── 분석 ──────────────────────────────────────────────────────────────────────

def analyze(switch_ip: str, port_mac_map: dict, threshold: int) -> list[dict]:
    results = []
    for port, macs in sorted(port_mac_map.items()):
        count     = len(macs)
        suspected = count > threshold
        results.append({
            "switch":    switch_ip,
            "port":      port,
            "mac_count": count,
            "macs":      macs,
            "suspected": suspected,
            "reason":    f"MAC {count}개 > 임계값 {threshold}" if suspected else "정상",
        })
    return results


# ── 출력 ──────────────────────────────────────────────────────────────────────

def print_report(all_results: list[dict], threshold: int):
    print()
    print("=" * 68)
    print(f"  공유기/허브 탐지 결과  (임계값: MAC > {threshold}개/포트)")
    print(f"  네트워크 대역: {', '.join(NETWORK_RANGES)}")
    print("=" * 68)

    current_switch = None
    for r in all_results:
        if r["switch"] != current_switch:
            current_switch = r["switch"]
            print(f"\n  ▶ 스위치: {current_switch}")
            print(f"  {'포트':<20} {'MAC 수':>6}  상태")
            print("  " + "-" * 46)

        status = "⚠  공유기/허브 의심" if r["suspected"] else "   정상"
        print(f"  {r['port']:<20} {r['mac_count']:>6}  {status}")
        if r["suspected"]:
            for mac in r["macs"]:
                print(f"  {'':20}    └ {mac}")

    suspects = [r for r in all_results if r["suspected"]]
    print()
    print("=" * 68)
    print(f"  총 포트: {len(all_results)}개  |  의심 포트: {len(suspects)}개")
    if suspects:
        print("\n  [의심 포트 요약]")
        for r in suspects:
            print(f"  • {r['switch']}  {r['port']}  → MAC {r['mac_count']}개")
    print()


def save_csv(all_results: list[dict]):
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(f"mac_detect_{ts}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["스위치IP", "포트", "MAC수", "의심여부", "사유", "MAC목록"])
        for r in all_results:
            writer.writerow([
                r["switch"], r["port"], r["mac_count"],
                "의심" if r["suspected"] else "정상",
                r["reason"],
                " | ".join(r["macs"]),
            ])
    print(f"[저장] {path.resolve()}")


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MAC 수 기반 공유기 탐지")
    parser.add_argument("--method",    choices=["snmp", "ssh", "demo"],
                        default=COLLECT_METHOD)
    parser.add_argument("--threshold", type=int, default=THRESHOLD)
    parser.add_argument("--csv",       action="store_true", default=SAVE_CSV)
    args = parser.parse_args()

    # 스위치 IP 목록 결정 (SWITCH_IPS 우선, 없으면 NETWORK_RANGES에서 추출)
    switch_list = expand_networks(NETWORK_RANGES)
    if not switch_list:
        print("[오류] 스캔할 스위치 IP가 없습니다. .env의 NETWORK_RANGES를 확인하세요.")
        return

    print(f"\n수집 방식 : {args.method.upper()}")
    print(f"스위치 목록: {', '.join(switch_list)}")
    print(f"임계값     : MAC > {args.threshold}개/포트\n")

    all_results = []
    for ip in switch_list:
        if args.method == "snmp":
            if not SNMP_AVAILABLE:
                print("[오류] pip install pysnmp")
                return
            port_mac_map = collect_snmp(ip)
        elif args.method == "ssh":
            if not NETMIKO_AVAILABLE:
                print("[오류] pip install netmiko")
                return
            port_mac_map = collect_ssh(ip)
        else:
            port_mac_map = demo_data(ip)

        if not port_mac_map:
            print(f"  [경고] {ip} — MAC 테이블 없음")
            continue

        all_results.extend(analyze(ip, port_mac_map, args.threshold))

    if all_results:
        print_report(all_results, args.threshold)
        if args.csv:
            save_csv(all_results)


if __name__ == "__main__":
    main()