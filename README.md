# GhostPing

A lightweight Python tool that helps you identify an unknown device on your local network by detecting which host drops off after you physically disconnect it.

## How It Works

GhostPing performs two sequential subnet scans and compares the results:

1. **Baseline scan** — pings every IP in the target subnet and records which hosts respond.
2. **Secondary scan** — after you disconnect the target device, scans again and notes which previously active host is now missing.

The IP that was present in the first scan but absent in the second is your device.

## Requirements

- Python 3.6+
- No external dependencies (uses only the standard library)

## Usage

```bash
python GhostPing.py
```

You'll be prompted for a subnet in CIDR notation:

```
Enter your subnet (e.g., 192.168.1.0/24):
```

The script then:

1. Runs an initial scan of all hosts in the subnet.
2. Asks you to physically disconnect the target device and press Enter.
3. Runs a second scan.
4. Prints the IP(s) that disappeared — that's your device.

### Example

```
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

## Features

- **Fast** — uses up to 50 concurrent threads to scan an entire `/24` subnet in seconds.
- **Cross-platform** — works on Linux, macOS, and Windows (adjusts the ping command automatically).
- **No dependencies** — pure Python standard library.

## Notes

- Some devices (or routers) may not respond to ICMP pings even when connected, producing false negatives.
- For best results, wait a few seconds after disconnecting the device before pressing Enter — this ensures the OS/hardware fully registers the disconnection.
