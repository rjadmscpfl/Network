#!/usr/bin/env python3
"""
Cisco Switch MAC Address Finder
================================
특정 MAC 주소 뒤 4자리를 입력받아 Cisco 스위치 전체 토폴로지에서
해당 클라이언트를 찾는 스크립트.

요구사항:
    pip install netmiko

사용법:
    python cisco_mac_finder.py
"""

import re
import sys
import logging
from getpass import getpass
from dataclasses import dataclass, field
from typing import Optional

try:
    from netmiko import ConnectHandler, NetmikoTimeoutException, NetmikoAuthenticationException
except ImportError:
    print("❌ netmiko가 설치되지 않았습니다. 다음 명령어로 설치하세요:")
    print("   pip install netmiko")
    sys.exit(1)


# ── 로깅 설정 ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ── 데이터 클래스 ──────────────────────────────────────────────────────────────
@dataclass
class SwitchInfo:
    ip: str
    hostname: str = ""
    visited: bool = False
    error: Optional[str] = None


@dataclass
class MACResult:
    switch_ip: str
    switch_hostname: str
    mac_address: str
    vlan: str
    interface: str
    mac_type: str


# ── 유틸리티 함수 ──────────────────────────────────────────────────────────────
def normalize_mac(raw: str) -> str:
    """
    MAC 주소를 소문자 점 표기(xxxx.xxxx.xxxx)로 정규화.
    입력 예: 00:1A:2B:3C:4D:5E / 00-1a-2b-3c-4d-5e / 001a.2b3c.4d5e
    """
    digits = re.sub(r"[^0-9a-fA-F]", "", raw)
    if len(digits) != 12:
        raise ValueError(f"유효하지 않은 MAC 주소: {raw!r}")
    d = digits.lower()
    return f"{d[0:4]}.{d[4:8]}.{d[8:12]}"


def suffix_to_partial(suffix4: str) -> str:
    """뒤 4자리 hex → 정규화된 부분 문자열 (마지막 그룹)."""
    suffix4 = re.sub(r"[^0-9a-fA-F]", "", suffix4).lower()
    if len(suffix4) != 4:
        raise ValueError("MAC 뒤 4자리를 정확히 입력하세요 (hex 4자리).")
    return suffix4  # xxxx.xxxx.XXXX 의 마지막 4자리


def connect(ip: str, username: str, password: str, secret: str = "") -> ConnectHandler:
    """Cisco IOS 스위치에 SSH 접속."""
    device = {
        "device_type": "cisco_ios",
        "host": ip,
        "username": username,
        "password": password,
        "secret": secret or password,
        "timeout": 15,
        "session_timeout": 60,
        "fast_cli": False,
    }
    conn = ConnectHandler(**device)
    conn.enable()
    return conn


# ── CDP 이웃 파싱 ──────────────────────────────────────────────────────────────
# 예시 출력:
# Device ID        Local Intrfce     Holdtme    Capability  Platform  Port ID
# SW2.example.com  Gig 0/1           156        R S I       WS-C2960  Gig 0/24
# ...
CDP_DETAIL_IP_RE = re.compile(r"IP(?:v4)?\s+[Aa]ddress\s*:\s*([\d\.]+)")
CDP_NEIGHBOR_BLOCK_RE = re.compile(
    r"Device ID\s*:\s*(.+?)\n.*?IP(?:v4)?\s+[Aa]ddress\s*:\s*([\d\.]+)",
    re.DOTALL,
)


def get_cdp_neighbor_ips(conn: ConnectHandler) -> list[SwitchInfo]:
    """
    'show cdp neighbors detail' 출력에서 이웃 스위치 IP 목록을 추출.
    """
    output = conn.send_command("show cdp neighbors detail", read_timeout=30)
    neighbors: list[SwitchInfo] = []

    # 각 Device 블록 분리
    blocks = re.split(r"-{10,}", output)
    for block in blocks:
        hostname_m = re.search(r"Device ID\s*:\s*(.+)", block)
        ip_m = CDP_DETAIL_IP_RE.search(block)
        if hostname_m and ip_m:
            hostname = hostname_m.group(1).strip()
            ip = ip_m.group(1).strip()
            neighbors.append(SwitchInfo(ip=ip, hostname=hostname))

    return neighbors


# ── MAC 주소 테이블 파싱 ───────────────────────────────────────────────────────
# 예시:
#           Mac Address Table
# Vlan    Mac Address       Type        Ports
# ----    -----------       --------    -----
#    1    0050.7966.6800    DYNAMIC     Gi0/1
MAC_TABLE_RE = re.compile(
    r"^\s*(\d+)\s+([\da-f]{4}\.[\da-f]{4}\.[\da-f]{4})\s+(\S+)\s+(\S+)",
    re.MULTILINE | re.IGNORECASE,
)


def search_mac_in_switch(
    conn: ConnectHandler,
    switch_info: SwitchInfo,
    suffix4: str,
) -> list[MACResult]:
    """MAC 주소 테이블에서 뒤 4자리가 일치하는 항목 검색."""
    output = conn.send_command("show mac address-table", read_timeout=30)
    results: list[MACResult] = []

    for m in MAC_TABLE_RE.finditer(output):
        vlan, mac, mac_type, port = m.group(1), m.group(2), m.group(3), m.group(4)
        # 마지막 4자리 비교
        if mac.replace(".", "")[-4:].lower() == suffix4.lower():
            results.append(
                MACResult(
                    switch_ip=switch_info.ip,
                    switch_hostname=switch_info.hostname,
                    mac_address=mac,
                    vlan=vlan,
                    interface=port,
                    mac_type=mac_type,
                )
            )
    return results


# ── 메인 탐색 로직 ─────────────────────────────────────────────────────────────
def discover_and_search(
    seed_ip: str,
    username: str,
    password: str,
    secret: str,
    suffix4: str,
    verbose: bool = True,
    stop_event=None,
) -> list[MACResult]:
    """
    Seed 스위치부터 시작하여 CDP로 발견된 모든 스위치를 BFS 탐색하며
    해당 MAC 뒤 4자리를 가진 클라이언트를 검색.
    stop_event: threading.Event — set() 호출 시 탐색 중단.
    """
    all_results: list[MACResult] = []
    visited_ips: set[str] = set()
    queue: list[SwitchInfo] = [SwitchInfo(ip=seed_ip, hostname="seed")]

    def _log(msg: str):
        if verbose:
            print(msg)

    while queue:
        if stop_event and stop_event.is_set():
            _log("⛔ 탐색이 중단되었습니다.")
            break

        current = queue.pop(0)
        if current.ip in visited_ips:
            continue
        visited_ips.add(current.ip)

        _log(f"\n{'='*60}")
        _log(f"🔌 접속 중: {current.ip}  ({current.hostname or '알 수 없음'})")

        try:
            conn = connect(current.ip, username, password, secret)
            current.visited = True

            # 1) 이 스위치에서 MAC 검색
            results = search_mac_in_switch(conn, current, suffix4)
            if results:
                for r in results:
                    _log(
                        f"  ✅ MAC 발견! {r.mac_address}  VLAN {r.vlan}  포트 {r.interface}"
                    )
                all_results.extend(results)
            else:
                _log(f"  ➖ MAC 없음")

            # 2) CDP 이웃 발견 → 큐에 추가
            neighbors = get_cdp_neighbor_ips(conn)
            _log(f"  📡 CDP 이웃 {len(neighbors)}개 발견")
            for nb in neighbors:
                if nb.ip not in visited_ips:
                    _log(f"     → {nb.ip}  ({nb.hostname}) 큐에 추가")
                    queue.append(nb)

            conn.disconnect()

        except NetmikoAuthenticationException:
            _log(f"  ❌ 인증 실패: {current.ip}")
            current.error = "auth"
        except NetmikoTimeoutException:
            _log(f"  ⏱️  타임아웃: {current.ip}")
            current.error = "timeout"
        except Exception as exc:
            _log(f"  ❌ 오류 ({current.ip}): {exc}")
            current.error = str(exc)

    return all_results


# ── CLI 엔트리포인트 ───────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  🔍 Cisco 스위치 MAC 주소 탐색기")
    print("=" * 60)

    seed_ip   = input("시작 스위치 IP 주소: ").strip()
    username  = input("SSH 사용자명: ").strip()
    password  = getpass("SSH 비밀번호: ")
    secret    = getpass("Enable 비밀번호 (없으면 Enter): ")
    suffix_raw = input("검색할 MAC 뒤 4자리 (예: 4d5e): ").strip()

    try:
        suffix4 = suffix_to_partial(suffix_raw)
    except ValueError as e:
        print(f"❌ {e}")
        sys.exit(1)

    print(f"\n🚀 탐색 시작 — MAC 뒤 4자리: {suffix4}")

    results = discover_and_search(
        seed_ip=seed_ip,
        username=username,
        password=password,
        secret=secret,
        suffix4=suffix4,
        verbose=True,
    )

    # ── 결과 출력 ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"  📋 최종 결과: {len(results)}건 발견")
    print("=" * 60)

    if not results:
        print("  검색된 MAC 주소가 없습니다.")
    else:
        header = f"{'스위치 IP':<18} {'호스트명':<20} {'MAC':<18} {'VLAN':<6} {'포트':<14} {'타입'}"
        print(header)
        print("-" * len(header))
        for r in results:
            print(
                f"{r.switch_ip:<18} {r.switch_hostname:<20} "
                f"{r.mac_address:<18} {r.vlan:<6} {r.interface:<14} {r.mac_type}"
            )

    print()


if __name__ == "__main__":
    main()