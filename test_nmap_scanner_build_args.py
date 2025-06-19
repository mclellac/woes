import logging
import sys

# Ensure src directory is in path to import nmap_scanner
# Ensure /app is in path for src package imports
sys.path.insert(0, '/app')
# Attempt to add system site-packages for 'gi'
import platform
if platform.system() == "Linux":
    # Common path for system-installed python3-gi
    # This may vary by distribution/version, but it's a common one for Debian/Ubuntu.
    # For Python 3.12, it might be /usr/lib/python3.12/dist-packages
    # Let's try a more generic approach first.
    py_version_major_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    dist_packages_path = f"/usr/lib/python{py_version_major_minor}/dist-packages"
    if dist_packages_path not in sys.path:
        sys.path.append(dist_packages_path)
    # Also common for older versions or different setups
    if "/usr/lib/python3/dist-packages" not in sys.path:
        sys.path.append("/usr/lib/python3/dist-packages")

try:
    from src.nmap_scanner import NmapScanner, NmapScanParameters
except ModuleNotFoundError as e:
    # Log the current sys.path for debugging if import fails
    print(f"DEBUG: sys.path is {sys.path}", file=sys.stderr)
    print(f"DEBUG: Error importing NmapScanner: {e}", file=sys.stderr)
    # Fallback for script_logger if it's not yet defined due to early exit
    logging.getLogger(__name__).error("Failed to import NmapScanner, check PYTHONPATH or script location and src package structure.", exc_info=True)
    sys.exit(1)


# Configure logging to see the output from NmapScanner and this script
# The NmapScanner module already configures its own logger.
# We want to see the INFO level logs from it.
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
script_logger = logging.getLogger(__name__) # Logger for this script

# Get the NmapScanner's own logger to ensure its level is also INFO
nmap_scanner_logger = logging.getLogger("nmap_scanner")
nmap_scanner_logger.setLevel(logging.INFO)


scanner = NmapScanner()

def run_test(test_name: str, params: NmapScanParameters):
    script_logger.info(f"--- Running Test: {test_name} ---")
    # The NmapScanner._build_nmap_arguments method has a logger.info call
    # that prints "FINAL NMAP COMMAND ARGS: ..."
    # So we just need to call it.
    args = scanner._build_nmap_arguments(params)
    # script_logger.info(f"Generated args for {test_name}: {' '.join(args)}") # Optionally log here too


# Test 1: Default Scan
params_test_1: NmapScanParameters = {
    "target": "scanme.nmap.org",
    "tcp_syn_scan": False,
    "os_fingerprinting": False,
    "service_version": False, # Explicitly ensure it's off for default
    "scan_all_ports": False, # Explicitly ensure it's off
    # No output_format or output_filename
}
run_test("Test 1: Default Scan (TCP SYN OFF, OS Fingerprint OFF, No File Output)", params_test_1)

# Test 2: TCP SYN Scan Option
params_test_2: NmapScanParameters = {
    "target": "scanme.nmap.org",
    "tcp_syn_scan": True,
    "os_fingerprinting": False,
    "service_version": False,
    "scan_all_ports": False,
}
run_test("Test 2: TCP SYN Scan ON", params_test_2)

# Test 3: OS Detection Option
params_test_3: NmapScanParameters = {
    "target": "scanme.nmap.org",
    "tcp_syn_scan": False,
    "os_fingerprinting": True,
    "service_version": False,
    "scan_all_ports": False,
}
run_test("Test 3: OS Detection ON", params_test_3)

# Test 4: File Output - Normal (.txt)
params_test_4: NmapScanParameters = {
    "target": "scanme.nmap.org",
    "tcp_syn_scan": False,
    "os_fingerprinting": False,
    "service_version": False,
    "scan_all_ports": False,
    "output_format": "Normal (.txt)",
    "output_filename": "/tmp/nmap_normal_output.txt",
}
run_test("Test 4: File Output - Normal (.txt)", params_test_4)

# Test 5: File Output - Grepable (.gnmap)
params_test_5: NmapScanParameters = {
    "target": "scanme.nmap.org",
    "tcp_syn_scan": False,
    "os_fingerprinting": False,
    "service_version": False,
    "scan_all_ports": False,
    "output_format": "Grepable (.gnmap)",
    "output_filename": "/tmp/nmap_grepable_output.gnmap",
}
run_test("Test 5: File Output - Grepable (.gnmap)", params_test_5)

# Test 6: File Output - XML (.xml)
params_test_6: NmapScanParameters = {
    "target": "scanme.nmap.org",
    "tcp_syn_scan": False,
    "os_fingerprinting": False,
    "service_version": False,
    "scan_all_ports": False,
    "output_format": "XML (.xml)",
    "output_filename": "/tmp/nmap_xml_output.xml",
}
run_test("Test 6: File Output - XML (.xml)", params_test_6)

# Test 7: File Output - "None" Format
params_test_7: NmapScanParameters = {
    "target": "scanme.nmap.org",
    "tcp_syn_scan": False,
    "os_fingerprinting": False,
    "service_version": False,
    "scan_all_ports": False,
    "output_format": "None",
    "output_filename": "", # Should be ignored
}
run_test("Test 7: File Output - None Format", params_test_7)

# Test with service version detection (example of another option)
params_test_sv: NmapScanParameters = {
    "target": "scanme.nmap.org",
    "tcp_syn_scan": False,
    "os_fingerprinting": False,
    "service_version": True,
    "scan_all_ports": False,
}
run_test("Test SV: Service Version Detection ON", params_test_sv)

# Test with scan all ports (example of another option)
params_test_all_ports: NmapScanParameters = {
    "target": "scanme.nmap.org",
    "tcp_syn_scan": False,
    "os_fingerprinting": False,
    "service_version": False,
    "scan_all_ports": True,
}
run_test("Test ALLPORTS: Scan All Ports ON", params_test_all_ports)

# Test with No Ping (example of another option)
params_test_no_ping: NmapScanParameters = {
    "target": "scanme.nmap.org",
    "tcp_syn_scan": False,
    "os_fingerprinting": False,
    "service_version": False,
    "scan_all_ports": False,
    "no_ping": True,
}
run_test("Test NOPING: No Ping ON", params_test_no_ping)

# Test with Timing Template (example of another option)
params_test_timing: NmapScanParameters = {
    "target": "scanme.nmap.org",
    "tcp_syn_scan": False,
    "os_fingerprinting": False,
    "service_version": False,
    "scan_all_ports": False,
    "timing_template": "T5", # Aggressive
}
run_test("Test TIMING: Timing Template T5", params_test_timing)

# Test with a script (example of another option)
params_test_script: NmapScanParameters = {
    "target": "scanme.nmap.org",
    "tcp_syn_scan": False,
    "os_fingerprinting": False,
    "service_version": False,
    "scan_all_ports": False,
    "selected_script": "vuln",
}
run_test("Test SCRIPT: NSE Script 'vuln'", params_test_script)

# Test combining multiple options
params_test_combined: NmapScanParameters = {
    "target": "scanme.nmap.org",
    "tcp_syn_scan": True,
    "os_fingerprinting": True,
    "service_version": True,
    "scan_all_ports": True,
    "no_ping": True,
    "timing_template": "T1", # Sneaky
    "selected_script": "default",
    "output_format": "Normal (.txt)",
    "output_filename": "/tmp/nmap_combined_output.txt",
}
run_test("Test COMBINED: Multiple Options Together", params_test_combined)

script_logger.info("--- All Nmap command generation tests complete ---")
