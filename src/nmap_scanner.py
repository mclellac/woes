import logging
import re
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Any, Dict, Union

import nmap
import yaml

# Initialize logger - AFTER all imports
logger = logging.getLogger(__name__)


class ScanOptions(Enum):
    DEFAULT = "-T4"
    OS_FINGERPRINTING = "-O -A"
    ALL_PORTS = "-p-"
    SCRIPT = "--script="


class ScanStatus(Enum):
    IN_PROGRESS = (0.0, "Scanning {target}...")
    COMPLETE = (1.0, "Scan complete")
    FAILED = (1.0, "Scan failed unexpectedly")
    IDLE = (0.0, "Idle")


class NmapScanner:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=4)

    def __del__(self):
        self.executor.shutdown(wait=True)

    def validate_target_input(self, target: str) -> bool:
        ipv4_segment = r"(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9]|0)"
        # Use named argument for repeated segment to avoid W1308
        ipv4_address = r"(?:{seg}\.{seg}\.{seg}\.{seg})".format(seg=ipv4_segment)
        fqdn = r"(?:[A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,}"
        cidr = fr"{ipv4_address}\/[0-9]{{1,2}}"  # Escaping curly braces for the CIDR notation
        addr_regex = fr"^(localhost|{ipv4_address}|{fqdn}|{cidr})$"

        targets = re.split(r"[ ,]+", target.strip())

        for t in targets:
            if re.match(addr_regex, t):
                logger.debug("Target '%s' matched the pattern.", t)
            else:
                logger.debug("Target '%s' did NOT match the pattern.", t)

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
        logger.debug("Nmap options constructed: %s", options)
        return options

    def run_nmap_scan(
        self,
        target: str,
        os_fingerprinting: bool,
        scan_all_ports: bool,
        selected_script: str,
    ):
        nmap_options = self.build_nmap_options(
            os_fingerprinting, scan_all_ports, selected_script
        )
        logger.debug(
            "Running Nmap scan for target: %s with options: %s",
            target,
            nmap_options
        )
        try:
            nm = nmap.PortScanner()
            nm.scan(hosts=target, arguments=nmap_options)  # Use nmap_options
            logger.debug("Nmap scan completed with results: %s", nm.all_hosts())
            return nm
        except nmap.PortScannerError as e:
            logger.error("Nmap scan failed for target %s with options '%s': %s", target, nmap_options, e, exc_info=True)
            raise e
        except Exception as e:
            # Shorten log message for E501
            logger.error("Unexpected error for target %s, options '%s': %s", target, nmap_options, e, exc_info=True)
            raise e

    def convert_results_to_yaml(self, nm: nmap.PortScanner) -> Dict[str, str]:
        all_results = {}
        for host in nm.all_hosts():
            logger.debug("Processing results for host: %s", host)
            host_data = nm[host]
            logger.debug("Raw host data: %s", host_data)  # Potentially very verbose
            plain_dict = self.to_plain_dict(host_data)
            yaml_output = yaml.safe_dump(plain_dict, default_flow_style=False)
            all_results[host] = yaml_output
        return all_results

    def to_plain_dict(self, data: Any) -> Union[Dict[str, Any], Any]:
        if isinstance(data, nmap.PortScannerHostDict):
            return {k: self.to_plain_dict(v) for k, v in data.items()}
        if isinstance(data, list):  # Changed from elif to if
            return [self.to_plain_dict(item) for item in data]
        if isinstance(data, dict):  # Changed from elif to if
            return {k: self.to_plain_dict(v) for k, v in data.items()}
        # else: # No else needed if all prior if/elifs return
        return data
