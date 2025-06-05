import logging
import re
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Any, Dict, Union

import nmap
import yaml


class ScanOptions(Enum):
    DEFAULT = "-T4"
    OS_FINGERPRINTING = "-O -A"
    ALL_PORTS = "-p-"
    SCRIPT = "--script="


class ScanStatus(Enum):
    IN_PROGRESS = (0.0, "Scanning {target}...")
    COMPLETE = (1.0, "Scan complete")
    FAILED = (1.0, "Scan failed unexpectedly")
    IDLE = (0.0, "Idle")  # Added IDLE state


class NmapScanner:
    """
    A wrapper around the python-nmap library to perform network scans.

    This class provides methods to validate target inputs, build Nmap command options,
    run scans asynchronously using a ThreadPoolExecutor, and process the results
    into a YAML format.
    """
    def __init__(self):
        """Initializes the NmapScanner with a ThreadPoolExecutor for concurrent scans."""
        self.executor = ThreadPoolExecutor(max_workers=4)

    def __del__(self):
        """Ensures the ThreadPoolExecutor is shut down when the NmapScanner instance is deleted."""
        self.executor.shutdown(wait=True)

    def validate_target_input(self, target: str) -> bool:
        """
        Validates the target string for Nmap scanning.

        The target can be a single IP address, a hostname, a CIDR block,
        or multiple targets separated by commas or spaces.

        Args:
            target: The target string to validate.

        Returns:
            True if the target string is valid, False otherwise.
        """
        ipv4_segment = r"(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9]|0)"
        ipv4_address = r"(?:{seg}\.{seg}\.{seg}\.{seg})".format(seg=ipv4_segment)
        fqdn = r"(?:[A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,}"
        cidr = fr"{ipv4_address}/[0-9]{{1,2}}"

        addr_regex = fr"^(localhost|{ipv4_address}|{fqdn}|{cidr})$"

        targets = re.split(r"[ ,]+", target.strip())

        for t in targets:
            if re.match(addr_regex, t):
                logging.debug("Target '%s' matched the pattern.", t)
            else:
                logging.debug("Target '%s' did NOT match the pattern.", t)

        return all(re.match(addr_regex, t) for t in targets)

    def build_nmap_options(
        self, os_fingerprinting: bool, scan_all_ports: bool, selected_script: str
    ) -> str:
        """
        Constructs the Nmap command-line options string based on boolean flags and a script name.

        Args:
            os_fingerprinting: If True, adds OS detection options (-O -A).
            scan_all_ports: If True, adds all ports scan option (-p-).
            selected_script: The name of the Nmap script to run (e.g., "vuln").
                             If "None" or empty, no script option is added.

        Returns:
            A string of Nmap command-line arguments.
        """
        options = ScanOptions.DEFAULT.value
        if os_fingerprinting:
            options += f" {ScanOptions.OS_FINGERPRINTING.value}"
        if scan_all_ports:
            options += f" {ScanOptions.ALL_PORTS.value}"
        if selected_script and selected_script != "None":
            options += f" {ScanOptions.SCRIPT.value}{selected_script}"
        logging.debug("Nmap options constructed: %s", options)
        return options

    def run_nmap_scan(
        self,
        target: str,
        os_fingerprinting: bool,
        scan_all_ports: bool,
        selected_script: str = None, # Modified to match nmap_page.py call
        service_version: bool = False,
        no_ping: bool = False,
        timing_template: str = "T3"
    ) -> nmap.PortScanner:
        """
        Executes an Nmap scan against the specified target with the given options.

        Args:
            target: The target host(s) for the Nmap scan.
            os_fingerprinting: Whether to enable OS fingerprinting.
            scan_all_ports: Whether to scan all TCP ports.
            selected_script: Specific Nmap script to run.

        Returns:
            An nmap.PortScanner object containing the scan results.

        Raises:
            nmap.PortScannerError: If Nmap encounters an error during the scan.
            Exception: For other unexpected errors during the scan process.
        """
        arguments = []
        # Base scan type: Default to TCP SYN scan (-sS) if user has root privileges,
        # otherwise nmap falls back to TCP Connect scan (-sT).
        # We can explicitly set -sS, nmap handles privilege check.
        arguments.append("-sS")

        if os_fingerprinting:
            arguments.append("-O") # OS Detection
        if service_version:
            arguments.append("-sV") # Service Version Detection
        if scan_all_ports: # Renamed from all_ports to match existing param name
            arguments.append("-p-")
        if selected_script and selected_script != "None": # Ensure "None" from dropdown isn't treated as a script
            arguments.append(f"--script={selected_script}")
        if no_ping:
            arguments.append("-Pn")

        if timing_template and re.match(r"^T[0-5]$", timing_template):
            arguments.append(f"-{timing_template}") # Nmap expects -T4
        else:
            arguments.append("-T3") # Default timing

        arguments_str = " ".join(arguments)
        logging.info("Nmap arguments for target '%s': %s", target, arguments_str)

        try:
            nm = nmap.PortScanner() # Local instance, not self.nm
            nm.scan(hosts=target, arguments=arguments_str)
            logging.debug("Nmap scan completed with results: %s", nm.all_hosts())
            return nm
        except nmap.PortScannerError as e:
            logging.error("Nmap scan failed for target %s: %s", target, e)
            raise e
        except Exception as e:
            logging.error("Unexpected error during scan for target %s (%s): %s", target, type(e).__name__, e)
            raise e

    def convert_results_to_yaml(self, nm: nmap.PortScanner) -> Dict[str, str]:
        """
        Converts Nmap scan results from an nmap.PortScanner object into a YAML formatted string for each host.

        Args:
            nm: The nmap.PortScanner object containing the scan results.

        Returns:
            A dictionary where keys are host IP addresses or names, and values are
            YAML strings representing the scan results for that host.
        """
        all_results = {}
        for host in nm.all_hosts():
            logging.debug("Processing results for host: %s", host)
            host_data = nm[host]
            logging.debug("Raw host data: %s", host_data)
            plain_dict = self.to_plain_dict(host_data)
            yaml_output = yaml.safe_dump(plain_dict, default_flow_style=False)
            all_results[host] = yaml_output
        return all_results

    def to_plain_dict(self, data: Any) -> Union[Dict[str, Any], Any]:
        """
        Recursively converts complex Nmap data structures (like PortScannerHostDict)
        into plain Python dictionaries and lists, suitable for YAML serialization.

        Args:
            data: The Nmap data to convert (can be PortScannerHostDict, dict, list, or other types).

        Returns:
            A plain dictionary or list representation of the input data.
        """
        if isinstance(data, nmap.PortScannerHostDict):
            return {k: self.to_plain_dict(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self.to_plain_dict(item) for item in data]
        elif isinstance(data, dict):
            return {k: self.to_plain_dict(v) for k, v in data.items()}
        else:
            return data
