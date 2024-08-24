# nmap_scanner.py
import re
import nmap
import yaml
import logging
from enum import Enum
from typing import Dict, Any, Union
from concurrent.futures import ThreadPoolExecutor


class ScanOptions(Enum):
    DEFAULT = "-T4"
    OS_FINGERPRINTING = "-O -A"
    ALL_PORTS = "-p-"
    SCRIPT = "--script="


class ScanStatus(Enum):
    IN_PROGRESS = (0.0, "Scanning {target}...")
    COMPLETE = (1.0, "Scan complete")
    FAILED = (1.0, "Scan failed unexpectedly")


class NmapScanner:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=4)

    def __del__(self):
        self.executor.shutdown(wait=True)

    def validate_target_input(self, target: str) -> bool:
        ipv4_segment = r"(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9]|0)"
        ipv4_address = r"(?:{}\.{}\.{}\.{})".format(
            ipv4_segment, ipv4_segment, ipv4_segment, ipv4_segment
        )
        fqdn = r"(?:[A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,}"
        cidr = r"{}\/[0-9]{{1,2}}".format(
            ipv4_address
        )  # Escaping curly braces for the CIDR notation

        addr_regex = r"^(localhost|" r"{}|" r"{}|" r"{})$".format(ipv4_address, fqdn, cidr)

        targets = re.split(r"[ ,]+", target.strip())

        for t in targets:
            if re.match(addr_regex, t):
                logging.debug(f"Target '{t}' matched the pattern.")
            else:
                logging.debug(f"Target '{t}' did NOT match the pattern.")

        return all(re.match(addr_regex, t) for t in targets)

    def build_nmap_options(
        self, os_fingerprinting: bool, scan_all_ports: bool, selected_script: str
    ) -> str:
        options = ScanOptions.DEFAULT.value
        if os_fingerprinting:
            options += f" {ScanOptions.OS_FINGERPRINTING.value}"
        if scan_all_ports:
            options += f" {ScanOptions.ALL_PORTS.value}"
        if selected_script and selected_script != "None":
            options += f" {ScanOptions.SCRIPT.value}{selected_script}"
        logging.debug(f"Nmap options constructed: {options}")
        return options

    def run_nmap_scan(
        self, target: str, os_fingerprinting: bool, scan_all_ports: bool, selected_script: str
    ):
        logging.debug(
            f"Running Nmap scan for target: {target} with options: {self.build_nmap_options(os_fingerprinting, scan_all_ports, selected_script)}"
        )
        options = self.build_nmap_options(os_fingerprinting, scan_all_ports, selected_script)
        try:
            nm = nmap.PortScanner()
            nm.scan(hosts=target, arguments=options)
            logging.debug(f"Nmap scan completed with results: {nm.all_hosts()}")
            return nm
        except nmap.PortScannerError as e:
            logging.error(f"Nmap scan failed: {e}")
            raise e
        except Exception as e:
            logging.error(f"Unexpected error during scan: {e}")
            raise e

    def convert_results_to_yaml(self, nm: nmap.PortScanner) -> Dict[str, str]:
        all_results = {}
        for host in nm.all_hosts():
            logging.debug(f"Processing results for host: {host}")
            host_data = nm[host]
            logging.debug(f"Raw host data: {host_data}")
            plain_dict = self.to_plain_dict(host_data)
            yaml_output = yaml.safe_dump(plain_dict, default_flow_style=False)
            all_results[host] = yaml_output
        return all_results

    def to_plain_dict(self, data: Any) -> Union[Dict[str, Any], Any]:
        if isinstance(data, nmap.PortScannerHostDict):
            return {k: self.to_plain_dict(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self.to_plain_dict(item) for item in data]
        elif isinstance(data, dict):
            return {k: self.to_plain_dict(v) for k, v in data.items()}
        else:
            return data
