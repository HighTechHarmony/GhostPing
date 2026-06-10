import subprocess
import ipaddress
import concurrent.futures
import platform
import time

def ping_host(ip):
    """Pings a single IP address. Returns the IP if active, otherwise None."""
    os_name = platform.system().lower()
    
    if os_name == 'windows':
        # -n 1: 1 ping, -w 1000: 1000ms timeout
        command = ['ping', '-n', '1', '-w', '1000', str(ip)]
    else:
        # -c 1: 1 ping, -W 1: 1 sec timeout (works on mostly Linux/macOS)
        command = ['ping', '-c', '1', '-W', '1', str(ip)]
        
    try:
        # Suppress output to keep the console clean
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            return str(ip)
    except Exception:
        pass
    return None

def scan_network(subnet):
    """Scans the subnet using multiple threads."""
    print(f"Scanning {subnet}...")
    network = ipaddress.ip_network(subnet, strict=False)
    active_hosts = set()
    
    # Using 50 threads. Adjust if your system chokes.
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        results = executor.map(ping_host, network.hosts())
        for result in results:
            if result:
                active_hosts.add(result)
                
    return active_hosts

def main():
    subnet = input("Enter your subnet (e.g., 192.168.1.0/24): ").strip()
    
    try:
        ipaddress.ip_network(subnet, strict=False)
    except ValueError:
        print("Invalid subnet format. Exiting.")
        return

    print("\n--- Phase 1: Baseline Scan ---")
    initial_hosts = scan_network(subnet)
    print(f"Found {len(initial_hosts)} active devices.")
    
    input("\nPhysically disconnect the target device, wait a few seconds, and press Enter to continue...")
    
    print("\n--- Phase 2: Secondary Scan ---")
    subsequent_hosts = scan_network(subnet)
    print(f"Found {len(subsequent_hosts)} active devices.")
    
    print("\n--- Results ---")
    missing_hosts = initial_hosts - subsequent_hosts
    new_hosts = subsequent_hosts - initial_hosts
    
    if missing_hosts:
        print("Devices that dropped off the network:")
        for ip in missing_hosts:
            print(f"  [-] {ip}")
    else:
        print("No devices dropped off. The device either doesn't respond to pings, or it's still connected.")
        
    if new_hosts:
        print("\nNote: The following new devices appeared during the test:")
        for ip in new_hosts:
            print(f"  [+] {ip}")

if __name__ == "__main__":
    main()

