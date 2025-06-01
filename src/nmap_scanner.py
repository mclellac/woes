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
        logger.debug("NmapScanner.__init__: Starting.")
        self.executor = ThreadPoolExecutor(max_workers=4)
        logger.debug(f"NmapScanner.__init__: ThreadPoolExecutor created: {self.executor}")
        logger.debug("NmapScanner.__init__: Finished.")

    def __del__(self):
        logger.debug("NmapScanner.__del__: Starting.")
        logger.debug("NmapScanner.__del__: Before self.executor.shutdown(wait=True)")
        self.executor.shutdown(wait=True)
        logger.debug("NmapScanner.__del__: After self.executor.shutdown(wait=True)")
        logger.debug("NmapScanner.__del__: Finished.")

    def validate_target_input(self, target: str) -> bool:
        logger.debug(f"NmapScanner.validate_target_input: Starting with target: '{target}'")
        ipv4_segment = r"(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9]|0)"
        # Use named argument for repeated segment to avoid W1308
        ipv4_address = r"(?:{seg}\.{seg}\.{seg}\.{seg})".format(seg=ipv4_segment)
        fqdn = r"(?:[A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,}"
        cidr = fr"{ipv4_address}\/[0-9]{{1,2}}"  # Escaping curly braces for the CIDR notation
        addr_regex = fr"^(localhost|{ipv4_address}|{fqdn}|{cidr})$"

        targets = re.split(r"[ ,]+", target.strip())
        logger.debug(f"NmapScanner.validate_target_input: Split targets: {targets}")

        validation_results = []
        for t in targets:
            is_match = bool(re.match(addr_regex, t))
            validation_results.append(is_match)
            if is_match:
                logger.debug(f"NmapScanner.validate_target_input: Target '{t}' matched the pattern.") # Existing
            else:
                logger.debug(f"NmapScanner.validate_target_input: Target '{t}' did NOT match the pattern.") # Existing

        result = all(validation_results)
        logger.debug(f"NmapScanner.validate_target_input: Returning: {result}")
        return result

    def build_nmap_options(
        self, os_fingerprinting: bool, scan_all_ports: bool, selected_script: str
    ) -> str:
        logger.debug(f"NmapScanner.build_nmap_options: Starting with os_fingerprinting: {os_fingerprinting}, scan_all_ports: {scan_all_ports}, selected_script: '{selected_script}'")
        options = ScanOptions.DEFAULT.value
        if os_fingerprinting:
            options += f" {ScanOptions.OS_FINGERPRINTING.value}"
        if scan_all_ports:
            options += f" {ScanOptions.ALL_PORTS.value}"
        if selected_script and selected_script != "None":
            options += f" {ScanOptions.SCRIPT.value}{selected_script}"
        logger.debug(f"NmapScanner.build_nmap_options: Nmap options constructed: {options}") # Existing, made f-string
        logger.debug(f"NmapScanner.build_nmap_options: Returning: '{options}'")
        return options

    def run_nmap_scan(
        self,
        target: str,
        os_fingerprinting: bool,
        scan_all_ports: bool,
        selected_script: str,
    ):
        logger.debug(f"NmapScanner.run_nmap_scan: Starting with target: '{target}', os_fingerprinting: {os_fingerprinting}, scan_all_ports: {scan_all_ports}, selected_script: '{selected_script}'")
        nmap_options = self.build_nmap_options(
            os_fingerprinting, scan_all_ports, selected_script
        ) # build_nmap_options already logs itself
        logger.debug(
            f"NmapScanner.run_nmap_scan: Running Nmap scan for target: {target} with options: {nmap_options}" # Existing, made f-string
        )
        try:
            nm = nmap.PortScanner()
            logger.debug(f"NmapScanner.run_nmap_scan: nmap.PortScanner() created: {nm}")
            logger.debug(f"NmapScanner.run_nmap_scan: Before nm.scan(hosts='{target}', arguments='{nmap_options}')")
            nm.scan(hosts=target, arguments=nmap_options)  # Use nmap_options
            logger.debug(f"NmapScanner.run_nmap_scan: After nm.scan(). Scan completed with results for hosts: {nm.all_hosts()}") # Existing, made f-string
            logger.debug(f"NmapScanner.run_nmap_scan: Returning nm object: {nm}")
            return nm
        except nmap.PortScannerError as e:
            logger.error(f"NmapScanner.run_nmap_scan: Nmap scan failed for target {target} with options '{nmap_options}': {e}", exc_info=True) # Existing, made f-string
            raise
        except Exception as e:
            logger.error(f"NmapScanner.run_nmap_scan: Unexpected error for target {target}, options '{nmap_options}': {e}", exc_info=True) # Existing, made f-string
            raise
        # No explicit "Finished" log here as it either returns or raises.

    def convert_results_to_yaml(self, nm: nmap.PortScanner) -> Dict[str, str]:
        logger.debug(f"NmapScanner.convert_results_to_yaml: Starting with nmap.PortScanner object: {nm}")
        all_results = {}
        hosts = nm.all_hosts()
        logger.debug(f"NmapScanner.convert_results_to_yaml: Processing hosts: {hosts}")
        for host in hosts:
            logger.debug(f"NmapScanner.convert_results_to_yaml: Processing results for host: {host}")
            host_data = nm[host]
            logger.debug(f"NmapScanner.convert_results_to_yaml: Raw host data for {host} (type {type(host_data)}): {list(host_data.keys()) if isinstance(host_data, dict) else 'Not a dict'}") # Log keys or type
            plain_dict = self.to_plain_dict(host_data) # Logs itself
            logger.debug(f"NmapScanner.convert_results_to_yaml: Converted host data for {host} to plain_dict.")
            yaml_output = yaml.safe_dump(plain_dict, default_flow_style=False)
            logger.debug(f"NmapScanner.convert_results_to_yaml: YAML output for {host} (len {len(yaml_output)}): '{yaml_output[:100]}...'")
            all_results[host] = yaml_output
        logger.debug(f"NmapScanner.convert_results_to_yaml: Returning all_results with keys: {list(all_results.keys())}")
        return all_results

    def to_plain_dict(self, data: Any) -> Union[Dict[str, Any], Any]:
        # Adding a check to prevent excessive logging for deep recursion if data is already plain
        if not isinstance(data, (nmap.PortScannerHostDict, list, dict)):
            return data # No logging for already plain data types to reduce noise

        logger.debug(f"NmapScanner.to_plain_dict: Starting with data type: {type(data)}")
        if isinstance(data, nmap.PortScannerHostDict):
            logger.debug(f"NmapScanner.to_plain_dict: Processing PortScannerHostDict with keys: {list(data.keys())}")
            res = {k: self.to_plain_dict(v) for k, v in data.items()}
            logger.debug(f"NmapScanner.to_plain_dict: Returning dict from PortScannerHostDict with keys: {list(res.keys())}")
            return res
        if isinstance(data, list):
            logger.debug(f"NmapScanner.to_plain_dict: Processing list with {len(data)} items.")
            res = [self.to_plain_dict(item) for item in data]
            logger.debug(f"NmapScanner.to_plain_dict: Returning list with {len(res)} items.")
            return res
        if isinstance(data, dict): # Standard dict
            logger.debug(f"NmapScanner.to_plain_dict: Processing dict with keys: {list(data.keys())}")
            res = {k: self.to_plain_dict(v) for k, v in data.items()}
            logger.debug(f"NmapScanner.to_plain_dict: Returning dict with keys: {list(res.keys())}")
            return res
        # This part should ideally not be reached if the initial check is comprehensive
        logger.debug(f"NmapScanner.to_plain_dict: Data is of type {type(data)}, returning as is.")
        return data
