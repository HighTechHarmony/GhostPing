#!/usr/bin/env python3
"""
GhostPing TUI: watch a local IPv4 subnet for devices that stop being observed.

Pings every address in the subnet concurrently and tracks which hosts reply.
Hosts that stop replying are aged through visual states.
"""

import argparse
import concurrent.futures
import ipaddress
import locale
import math
import platform
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

try:
    import curses
except ImportError:  # pragma: no cover - exercised on Windows without curses
    curses = None


@dataclass
class HostRecord:
    ip: ipaddress.IPv4Address
    first_seen: float
    last_seen: float
    last_appeared: float
    present_scans: int
    missing_scans: int
    present: bool = False
    seen_count: int = 0


@dataclass
class MonitorView:
    records: Dict[int, HostRecord]
    network: ipaddress.IPv4Network
    interval: float
    paused: bool
    last_error: Optional[str]
    last_scan_started: Optional[float]
    last_scan_finished: Optional[float]
    cycles: int
    host_count: int
    next_refresh_due: Optional[float]
    scan_in_progress: bool
    bootstrap_scans_left: int





def ping_host(ip: ipaddress.IPv4Address, timeout: float) -> Optional[str]:
    os_name = platform.system().lower()
    timeout_ms = str(max(1, int(timeout * 1000)))

    if os_name == "windows":
        command = ["ping", "-n", "1", "-w", timeout_ms, str(ip)]
    elif os_name == "darwin":
        command = ["ping", "-c", "1", "-W", timeout_ms, str(ip)]
    else:
        command = ["ping", "-c", "1", "-W", str(max(1, int(math.ceil(timeout)))), str(ip)]

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout + 2.0,
            check=False,
        )
        if result.returncode == 0:
            return str(ip)
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def scan_network_icmp(
    network: ipaddress.IPv4Network,
    workers: int,
    timeout: float,
) -> Set[str]:
    print("Scanning {}...".format(network))
    active_hosts: Set[str] = set()

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        results = executor.map(lambda host: ping_host(host, timeout), network.hosts())
        for result in results:
            if result:
                active_hosts.add(result)

    return active_hosts


def build_classic_network(subnet_arg: Optional[str]) -> ipaddress.IPv4Network:
    if subnet_arg:
        return build_network(subnet_arg)

    subnet = input("Enter your subnet (e.g., 192.168.1.0/24): ").strip()
    if not subnet:
        raise ValueError("subnet is required for classic mode")
    return build_network(subnet)


def run_classic_mode(args: argparse.Namespace) -> int:
    if args.ping_workers <= 0:
        print("--ping-workers must be greater than zero", file=sys.stderr)
        return 2

    try:
        network = build_classic_network(args.subnet)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print("Classic mode uses the original two-scan ICMP workflow.")
    print("Devices that block ping will still be invisible in this mode.")

    print("\n--- Phase 1: Baseline Scan ---")
    initial_hosts = scan_network_icmp(network, args.ping_workers, args.ping_timeout)
    print("Found {} active devices.".format(len(initial_hosts)))

    input("\nPhysically disconnect the target device, wait a few seconds, and press Enter to continue...")

    print("\n--- Phase 2: Secondary Scan ---")
    subsequent_hosts = scan_network_icmp(network, args.ping_workers, args.ping_timeout)
    print("Found {} active devices.".format(len(subsequent_hosts)))

    print("\n--- Results ---")
    missing_hosts = initial_hosts - subsequent_hosts
    new_hosts = subsequent_hosts - initial_hosts

    if missing_hosts:
        print("Devices that dropped off the network:")
        for ip in sorted(missing_hosts, key=ipaddress.IPv4Address):
            print("  [-] {}".format(ip))
    else:
        print("No devices dropped off. The device either doesn't respond to pings, or it's still connected.")

    if new_hosts:
        print("\nNote: The following new devices appeared during the test:")
        for ip in sorted(new_hosts, key=ipaddress.IPv4Address):
            print("  [+] {}".format(ip))

    return 0





def guess_subnet() -> Optional[ipaddress.IPv4Network]:
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP connect only asks the kernel which local address would be used.
        sock.connect(("8.8.8.8", 80))
        local_ip = ipaddress.IPv4Address(sock.getsockname()[0])
    except OSError:
        return None
    finally:
        sock.close()

    if local_ip.is_loopback or local_ip.is_link_local:
        return None
    return ipaddress.ip_network("{}/24".format(local_ip), strict=False)


def iter_probe_hosts(network: ipaddress.IPv4Network) -> List[ipaddress.IPv4Address]:
    return [ip for ip in network.hosts() if not ip.is_multicast]


def address_for_octet(network: ipaddress.IPv4Network, octet: int) -> ipaddress.IPv4Address:
    base = int(network.network_address) & 0xFFFFFF00
    return ipaddress.IPv4Address(base + octet)


class NetworkMonitor:
    def __init__(
        self,
        network: ipaddress.IPv4Network,
        interval: float,
        ping_timeout: float = 1.0,
        ping_workers: int = 50,
    ) -> None:
        self.network = network
        self.interval = interval
        self.ping_timeout = ping_timeout
        self.ping_workers = ping_workers
        self.probe_hosts = list(network.hosts())

        self.records: Dict[int, HostRecord] = {}
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread: Optional[threading.Thread] = None

        self.paused = False
        self.last_error: Optional[str] = None
        self.last_scan_started: Optional[float] = None
        self.last_scan_finished: Optional[float] = None
        self.next_refresh_due: Optional[float] = None
        self.scan_in_progress = False
        self.cycles = 0
        self.bootstrap_scans_left = 3

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="ghostping-monitor", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake_event.set()
        if self.thread:
            self.thread.join(timeout=3.0)

    def set_paused(self, paused: bool) -> None:
        with self.lock:
            self.paused = paused
            if paused:
                self.next_refresh_due = None
                self.scan_in_progress = False
        self.wake_event.set()

    def toggle_paused(self) -> None:
        with self.lock:
            self.paused = not self.paused
            if self.paused:
                self.next_refresh_due = None
                self.scan_in_progress = False
        self.wake_event.set()

    def request_refresh(self) -> None:
        with self.lock:
            if not self.paused:
                self.next_refresh_due = time.time()
        self.wake_event.set()

    def snapshot(self) -> MonitorView:
        with self.lock:
            records = {
                octet: HostRecord(
                    ip=record.ip,
                    first_seen=record.first_seen,
                    last_seen=record.last_seen,
                    last_appeared=record.last_appeared,
                    present_scans=record.present_scans,
                    missing_scans=record.missing_scans,
                    present=record.present,
                    seen_count=record.seen_count,
                )
                for octet, record in self.records.items()
            }
            return MonitorView(
                records=records,
                network=self.network,
                interval=self.interval,
                paused=self.paused,
                last_error=self.last_error,
                last_scan_started=self.last_scan_started,
                last_scan_finished=self.last_scan_finished,
                cycles=self.cycles,
                host_count=len(self.probe_hosts),
                next_refresh_due=self.next_refresh_due,
                scan_in_progress=self.scan_in_progress,
                bootstrap_scans_left=self.bootstrap_scans_left,
            )

    def _run(self) -> None:
        while not self.stop_event.is_set():
            with self.lock:
                paused = self.paused

            if paused:
                self.wake_event.wait(0.5)
                self.wake_event.clear()
                continue

            started = time.time()
            with self.lock:
                self.last_scan_started = started
                self.next_refresh_due = None
                self.scan_in_progress = True

            # Ping all hosts concurrently
            active_ips: Set[str] = set()
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.ping_workers) as executor:
                futures = {
                    executor.submit(ping_host, ip, self.ping_timeout): ip
                    for ip in self.probe_hosts
                }
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    if result:
                        active_ips.add(result)

            self._apply_ping_results(active_ips, time.time())

            elapsed = time.time() - started
            if self.bootstrap_scans_left > 0:
                self.bootstrap_scans_left -= 1
                effective_interval = 3.0
            else:
                effective_interval = self.interval
            wait_for = max(0.1, effective_interval - elapsed)
            with self.lock:
                self.next_refresh_due = time.time() + wait_for
                self.scan_in_progress = False
            self.wake_event.wait(wait_for)
            self.wake_event.clear()

    def _apply_ping_results(self, active_ips: Set[str], now: float) -> None:
        with self.lock:
            previous_present = {octet: record.present for octet, record in self.records.items()}
            for record in self.records.values():
                record.present = False

            for ip_str in active_ips:
                ip = ipaddress.IPv4Address(ip_str)
                octet = int(str(ip).rsplit(".", 1)[1])

                if ip == self.network.network_address or ip == self.network.broadcast_address:
                    continue

                record = self.records.get(octet)
                if record is None:
                    record = HostRecord(
                        ip=ip,
                        first_seen=now,
                        last_seen=now,
                        last_appeared=now,
                        present_scans=1,
                        missing_scans=0,
                    )
                    self.records[octet] = record
                elif previous_present.get(octet, False):
                    if self.bootstrap_scans_left > 0 and record.present_scans >= 1:
                        pass  # stay yellow during bootstrap
                    else:
                        record.present_scans += 1
                else:
                    record.last_appeared = now
                    record.present_scans = 1
                record.missing_scans = 0
                record.last_seen = now
                record.present = True
                record.seen_count += 1

            for octet, record in self.records.items():
                if record.present:
                    continue
                if previous_present.get(octet, False):
                    record.missing_scans = 1
                elif record.missing_scans > 0:
                    record.missing_scans += 1
                else:
                    record.missing_scans = 1
                record.present_scans = 0

            self.last_scan_finished = now
            self.cycles += 1


def format_age(seconds: Optional[float]) -> str:
    if seconds is None:
        return "--"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "{}s".format(seconds)
    minutes = seconds // 60
    if minutes < 60:
        return "{}m".format(minutes)
    hours = minutes // 60
    minutes = minutes % 60
    return "{}h{:02d}m".format(hours, minutes)


def compact_time(timestamp: Optional[float]) -> str:
    if not timestamp:
        return "--"
    return time.strftime("%H:%M:%S", time.localtime(timestamp))


def format_countdown(view: MonitorView, now: float) -> str:
    if view.paused:
        return "paused"
    if view.scan_in_progress:
        return "refreshing"
    if view.next_refresh_due is None:
        return "due"
    return "{}s".format(max(0, int(math.ceil(view.next_refresh_due - now))))


def format_scans(count: int) -> str:
    if count == 1:
        return "1 scan"
    return "{} scans".format(count)





def addstr_safe(screen, y: int, x: int, text: str, attr: int = 0) -> None:
    height, width = screen.getmaxyx()
    if y < 0 or y >= height or x < 0 or x >= width:
        return
    available = width - x - 1
    if available <= 0:
        return
    try:
        screen.addstr(y, x, text[:available], attr)
    except curses.error:
        pass


class GhostPingTUI:
    def __init__(self, monitor: NetworkMonitor, use_ascii: bool) -> None:
        self.monitor = monitor
        self.use_ascii = use_ascii
        self.selected_octet = 1
        self.colors: Dict[str, int] = {}

    def run(self, screen) -> None:
        curses.curs_set(0)
        screen.nodelay(True)
        screen.timeout(250)
        self._init_colors()

        while True:
            key = screen.getch()
            if key in (ord("q"), ord("Q"), 27):
                break
            if key in (ord("p"), ord("P"), ord(" ")):
                self.monitor.toggle_paused()
            elif key in (ord("r"), ord("R")):
                self.monitor.request_refresh()
            elif key == curses.KEY_LEFT:
                self.selected_octet = max(0, self.selected_octet - 1)
            elif key == curses.KEY_RIGHT:
                self.selected_octet = min(255, self.selected_octet + 1)
            elif key == curses.KEY_UP:
                self.selected_octet = max(0, self.selected_octet - 16)
            elif key == curses.KEY_DOWN:
                self.selected_octet = min(255, self.selected_octet + 16)

            self._draw(screen)

    def _init_colors(self) -> None:
        if not curses.has_colors():
            self.colors = {
                name: 0
                for name in (
                    "present",
                    "present_new",
                    "missing_first",
                    "missing_stale",
                    "dim",
                    "title",
                    "error",
                )
            }
            return

        curses.start_color()
        try:
            curses.use_default_colors()
            background = -1
        except curses.error:
            background = curses.COLOR_BLACK

        rich_colors = getattr(curses, "COLORS", 0) >= 256
        pairs = {
            "present": (1, 250 if rich_colors else curses.COLOR_WHITE, background),
            "present_new": (2, curses.COLOR_WHITE, background),
            "missing_first": (3, curses.COLOR_RED, background),
            "missing_stale": (4, 240 if rich_colors else curses.COLOR_WHITE, background),
            "dim": (5, curses.COLOR_CYAN, background),
            "title": (6, curses.COLOR_WHITE, background),
            "error": (7, curses.COLOR_RED, background),
        }
        for name, (pair_id, foreground, bg) in pairs.items():
            curses.init_pair(pair_id, foreground, bg)
            self.colors[name] = curses.color_pair(pair_id)

    def _draw(self, screen) -> None:
        view = self.monitor.snapshot()
        now = time.time()
        screen.erase()
        height, width = screen.getmaxyx()

        if height < 23 or width < 58:
            addstr_safe(screen, 0, 0, "GhostPing TUI needs at least 58x22.")
            addstr_safe(screen, 1, 0, "Resize the terminal or press q.")
            screen.refresh()
            return

        title_attr = self.colors.get("title", 0) | curses.A_BOLD
        addstr_safe(screen, 0, 0, "GhostPing TUI", title_attr)

        status = "paused" if view.paused else "watching"
        addstr_safe(
            screen,
            1,
            0,
            "{}  {}  hosts:{}  cycles:{}  rescan:{}".format(
                view.network,
                status,
                len(view.records),
                view.cycles,
                format_age(view.interval),
            ),
            self.colors.get("dim", 0),
        )
        addstr_safe(
            screen,
            2,
            0,
            "NEXT REFRESH: {}  last:{}  rescan:{}  q quit  p pause  r refresh".format(
                format_countdown(view, now).upper(),
                compact_time(view.last_scan_finished),
                format_age(view.interval),
            ),
            self.colors.get("title", 0) | curses.A_BOLD,
        )

        # Progress bar on row 3
        if view.paused:
            addstr_safe(
                screen, 3, 0,
                " [PAUSED]",
                self.colors.get("title", 0),
            )
        elif view.scan_in_progress:
            addstr_safe(
                screen, 3, 0,
                " [scanning ...]",
                self.colors.get("title", 0) | curses.A_BOLD,
            )
        elif view.next_refresh_due is not None:
            remaining = max(0.0, view.next_refresh_due - now)
            eff_interval = 3.0 if view.bootstrap_scans_left > 0 else view.interval
            progress = max(0.0, min(1.0, 1.0 - remaining / eff_interval)) if eff_interval > 0 else 0.0
            bar_width = max(5, min(width - 12, 50))
            filled = int(progress * bar_width)
            bar_char = "#" if self.use_ascii else "\u2588"
            empty_char = "." if self.use_ascii else "\u2591"
            bar = " " + bar_char * filled + empty_char * (bar_width - filled)
            pct = int(progress * 100)
            label = "{} {:>3d}%".format(bar, pct)
            if view.bootstrap_scans_left > 0:
                label = " (boot+{}){}".format(view.bootstrap_scans_left, label)
            addstr_safe(
                screen, 3, 0,
                label,
                self.colors.get("title", 0),
            )

        self._draw_grid(screen, view, now)
        self._draw_side_panel(screen, view, now, width, height)

        if view.last_error:
            addstr_safe(screen, height - 1, 0, view.last_error, self.colors.get("error", 0))
        else:
            block = "#" if self.use_ascii else "\u25a0"
            down = "v" if self.use_ascii else "\u25bc"
            addstr_safe(
                screen,
                height - 1,
                0,
                "{} present  {} missing  . stale    new IPs white for 1 scan    q quit  p pause".format(
                    block, down,
                ),
                self.colors.get("title", 0),
            )

        screen.refresh()

    def _draw_grid(self, screen, view: MonitorView, now: float) -> None:
        left = 0
        top = 5
        addstr_safe(screen, top - 1, left + 4, "0 1 2 3 4 5 6 7 8 9 A B C D E F", self.colors.get("dim", 0))

        for row in range(16):
            addstr_safe(screen, top + row, left, "{:X}".format(row), self.colors.get("dim", 0))
            for col in range(16):
                octet = row * 16 + col
                y = top + row
                x = left + 4 + col * 2
                char, attr = self._cell_for(octet, view, now)
                if octet == self.selected_octet:
                    attr |= curses.A_REVERSE
                addstr_safe(screen, y, x, char, attr)

    def _cell_for(self, octet: int, view: MonitorView, now: float) -> Tuple[str, int]:
        record = view.records.get(octet)
        chars = {
            "present": "#" if self.use_ascii else "\u25a0",
            "present_new": "#" if self.use_ascii else "\u25a0",
            "missing_first": "v" if self.use_ascii else "\u25bc",
            "missing_stale": ".",
            "empty": " ",
        }

        if octet in (0, 255):
            return chars["missing_stale"], self.colors.get("dim", 0) | curses.A_DIM
        if address_for_octet(view.network, octet) not in view.network:
            return chars["empty"], self.colors.get("dim", 0)
        if record is None:
            return chars["empty"], 0

        status, attr = self._status_for_record(record, view, now)
        return chars[status], attr

    def _status_for_record(self, record: HostRecord, view: MonitorView, now: float) -> Tuple[str, int]:
        if record.present:
            if record.present_scans <= 1:
                return "present_new", self.colors.get("present_new", 0) | curses.A_BOLD
            return "present", self.colors.get("present", 0)

        if record.missing_scans <= 1:
            return "missing_first", self.colors.get("missing_first", 0) | curses.A_BOLD
        return "missing_stale", self.colors.get("missing_stale", 0) | curses.A_DIM

    def _draw_side_panel(self, screen, view: MonitorView, now: float, width: int, height: int) -> None:
        panel_x = 40 if width >= 92 else 0
        panel_y = 5 if width >= 92 else 22
        attr_title = self.colors.get("title", 0) | curses.A_BOLD

        selected_ip = address_for_octet(view.network, self.selected_octet)
        selected = view.records.get(self.selected_octet)
        addstr_safe(screen, panel_y, panel_x, "Selected", attr_title)
        addstr_safe(screen, panel_y + 1, panel_x, str(selected_ip))
        if selected:
            if selected.present:
                addstr_safe(
                    screen,
                    panel_y + 2,
                    panel_x,
                    "state: present  seen: {}".format(format_scans(selected.present_scans)),
                )
            else:
                addstr_safe(
                    screen,
                    panel_y + 2,
                    panel_x,
                    "state: missing  absent: {}".format(format_scans(selected.missing_scans)),
                )
            addstr_safe(screen, panel_y + 3, panel_x, "first: {}".format(compact_time(selected.first_seen)))
        else:
            addstr_safe(screen, panel_y + 2, panel_x, "not observed")

        if width < 92:
            return

        list_y = panel_y + 7
        available_rows = max(0, height - list_y - 1)
        panel_width = max(1, width - panel_x - 1)
        reporting_column_width = 16
        reporting_columns = max(1, min(5, panel_width // reporting_column_width))

        # Combine present and missing hosts into a single sorted list:
        # present first (by IP), then missing (most-recently-lost first)
        present_records = sorted(
            (r for r in view.records.values() if r.present),
            key=lambda r: r.ip,
        )
        missing_records = sorted(
            (r for r in view.records.values() if not r.present and r.missing_scans > 0),
            key=lambda r: (-r.missing_scans, r.ip),
        )
        all_records = present_records + missing_records

        addstr_safe(
            screen, list_y, panel_x,
            "Reporting In ({})".format(len(all_records)),
            attr_title,
        )
        col_width = reporting_column_width
        max_items = available_rows * reporting_columns
        for index, record in enumerate(all_records[:max_items]):
            _status, attr = self._status_for_record(record, view, now)
            row = index // reporting_columns
            column = index % reporting_columns
            addstr_safe(
                screen,
                list_y + 1 + row,
                panel_x + (column * col_width),
                "{:15s}".format(str(record.ip)),
                attr,
            )


def positive_float(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Terminal ICMP ping monitor for a local IPv4 subnet."
    )
    parser.add_argument(
        "subnet",
        nargs="?",
        help="IPv4 subnet to watch, for example 192.168.1.0/24. Defaults to local-ip/24.",
    )
    parser.add_argument(
        "--classic",
        action="store_true",
        help="run the original two-phase ICMP comparison and exit instead of opening the TUI",
    )
    parser.add_argument(
        "--rescan-time",
        "--interval",
        dest="interval",
        type=positive_float,
        default=30.0,
        help="seconds between rescans",
    )
    parser.add_argument("--ascii", action="store_true", help="use ASCII symbols instead of block glyphs")
    parser.add_argument("--ping-workers", type=int, default=50, help="concurrent ping worker count")
    parser.add_argument("--ping-timeout", type=positive_float, default=1.0, help="per-host ping timeout")
    return parser.parse_args(argv)


def build_network(subnet_arg: Optional[str]) -> ipaddress.IPv4Network:
    if subnet_arg:
        network = ipaddress.ip_network(subnet_arg, strict=False)
    else:
        guessed = guess_subnet()
        if guessed is None:
            raise ValueError("could not infer local subnet; pass one explicitly, e.g. 192.168.1.0/24")
        network = guessed

    if not isinstance(network, ipaddress.IPv4Network):
        raise ValueError("only IPv4 subnets are supported")
    if network.num_addresses > 256:
        raise ValueError("refusing to watch more than /24; pass a /24 or smaller subnet")
    return network


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.classic:
        return run_classic_mode(args)

    if curses is None:
        print("GhostPing_TUI requires curses. On Windows, install windows-curses or run in WSL.")
        return 2

    try:
        network = build_network(args.subnet)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    locale.setlocale(locale.LC_ALL, "")
    monitor = NetworkMonitor(
        network=network,
        interval=args.interval,
        ping_timeout=args.ping_timeout,
        ping_workers=args.ping_workers,
    )

    monitor.start()
    try:
        curses.wrapper(GhostPingTUI(monitor, args.ascii).run)
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
