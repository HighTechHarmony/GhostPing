# GhostPing

A lightweight Python utility for identifying a local network device by watching
which host stops being observed after you physically disconnect it.

GhostPing has two operating modes:

- **Realtime TUI mode**: `GhostPing_TUI.py` opens a terminal dashboard and
  continuously watches a local IPv4 subnet up to `/24` via concurrent ICMP pings.
- **Classic terminal mode**: `GhostPing_TUI.py --classic` runs the original
  Baseline/Secondary two-scan ICMP workflow and exits.

The original standalone `GhostPing.py` script is still present for the simple
two-scan workflow.

## Requirements

- Python 3.6+
- No external dependencies
- `curses` for the realtime TUI

`curses` is standard on Linux and macOS. On Windows, run the TUI inside WSL or
install `windows-curses`.

## Command Line Options

| Option                                          | Description                                                             |
| ----------------------------------------------- | ----------------------------------------------------------------------- |
| `subnet`                                        | IPv4 subnet to watch, e.g. `192.168.1.0/24`. Defaults to `local-ip/24`. |
| `--classic`                                     | Run the original two-phase ICMP comparison and exit                     |
| `--rescan-time SECONDS` or `--interval SECONDS` | Seconds between rescans (default: 30)                                   |
| `--ping-workers N`                              | Concurrent ping worker count (default: 50)                              |
| `--ping-timeout SECONDS`                        | Per-host ping timeout in seconds (default: 1.0)                         |
| `--ascii`                                       | Use ASCII symbols instead of Unicode block glyphs                       |

## Usage

### Realtime TUI Mode (default)

```bash
python GhostPing_TUI.py 192.168.1.0/24
```

If you omit the subnet, the TUI tries to infer `local-ip/24`:

```bash
python GhostPing_TUI.py
```

The TUI pings every address in the subnet concurrently, then displays a 16x16
grid where each cell represents the last octet of an IPv4 address. For example,
cell `42` represents `192.168.1.42` when watching `192.168.1.0/24`.

On wider terminals, the side panel shows a **Reporting In** list of observed IP
addresses sorted by status (present first, then missing). Each entry uses the
same visual state as the grid.

The TUI header shows a countdown and progress bar to the next refresh.

#### Initial Discovery

On startup, the tool runs **3 rapid scans** (about 3 seconds apart) to discover
all responding hosts quickly. Any host that replies during any of these initial
scans gets the same "new" (white) status.

#### Visual States

| Grid           | Side Panel     | Meaning                                    |
| -------------- | -------------- | ------------------------------------------ |
| White `■`      | White IP, bold | Host seen for the first time (1 scan)      |
| Light grey `■` | Light grey IP  | Host still present (2+ scans)              |
| Red `▼`        | Red IP, bold   | Host just stopped replying (1 scan missed) |
| Dark grey `.`  | Dark grey IP   | Host still missing (2+ scans missed)       |
| (empty)        | —              | Never observed                             |

#### Controls

- `q` or `Esc`: quit
- `p` or `Space`: pause/resume
- `r`: request immediate refresh
- Arrow keys: move the selected cell

#### Tuning

```bash
# Faster refresh cycle
python GhostPing_TUI.py 192.168.1.0/24 --rescan-time 10

# Quicker ping timeout for responsive networks
python GhostPing_TUI.py 192.168.1.0/24 --ping-timeout 0.5

# ASCII-only symbols (for terminals without Unicode)
python GhostPing_TUI.py 192.168.1.0/24 --ascii
```

### Classic Terminal Mode

```bash
python GhostPing_TUI.py --classic 192.168.1.0/24
```

If you omit the subnet, it prompts for one:

```bash
python GhostPing_TUI.py --classic
```

Classic mode performs two sequential ICMP scans and compares the results:

1. **Baseline scan**: pings every IP in the target subnet and records which
   hosts respond.
2. **Disconnect prompt**: waits while you physically disconnect the target
   device.
3. **Secondary scan**: scans again and notes which previously active host is
   now missing.
4. **Result comparison**: prints the IP address or addresses that disappeared.

```bash
python GhostPing_TUI.py --classic 192.168.1.0/24 --ping-workers 25 --ping-timeout 2
```

The original `GhostPing.py` script can still be run directly:

```bash
python GhostPing.py
```

#### Classic Example

```text
Enter your subnet (e.g., 192.168.1.0/24): 192.168.1.0/24

--- Phase 1: Baseline Scan ---
Scanning 192.168.1.0/24...
Found 12 active devices.

Physically disconnect the target device, wait a few seconds, and press Enter to continue...

--- Phase 2: Secondary Scan ---
Scanning 192.168.1.0/24...
Found 11 active devices.

--- Results ---
Devices that dropped off the network:
  [-] 192.168.1.42
```

## How It Works

Both modes use ICMP Echo Requests (pings). The realtime TUI pings every address
in the subnet concurrently using a thread pool, then applies the results to a
scan-counting state machine:

- A host is **present** if it replied to ping in the most recent scan.
- Each consecutive scan where the host replies increments its presence counter,
  advancing from white (first scan) to light grey (established).
- The first scan where the host **does not** reply marks it as missing (red).
- Additional scans where it remains absent increment the missing counter (dark
  grey).

On startup, the tool runs 3 quick bootstrapping scans (about 3 seconds apart)
to populate the grid rapidly, then settles into the configured `--rescan-time`
interval.

## Feature Summary

- **Realtime TUI**: continuously watches a subnet with concurrent pings and ages
  missing devices visually.
- **Scan-count aging**: host status advances by scan count, not wall clock,
  giving consistent behavior regardless of rescan interval.
- **Bootstrap discovery**: 3 rapid scans on startup to find all hosts quickly.
- **Classic compatibility**: `--classic` retains the original two-phase
  Baseline/Secondary workflow.
- **Cross-platform**: adjusts ping commands for Windows, Linux, and macOS.
- **No third-party dependencies**: uses only the Python standard library.

## Notes

- Both modes depend on ICMP ping responses. Windows machines, IoT devices,
  firewalls, and routers may ignore ping even while connected.
- The realtime TUI uses scan-count aging: a host that stops replying moves to
  its first missing state on the first scan where it is absent, then progresses
  through one color step per additional missed scan.
- Sleeping battery devices, client isolation, VLANs, and aggressive firewalls
  can create false positives or delayed disappearance.
- For classic mode, wait a few seconds after disconnecting the device before
  continuing to the Secondary scan.
