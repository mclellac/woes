"""Provides the NmapScanner class and related utilities for performing Nmap scans.

This module includes functionality for building Nmap commands, handling
privilege escalation, executing scans, and processing results.
"""
import logging
logger = logging.getLogger(__name__)
import re
import platform
import subprocess
import shlex
import shutil
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Any, Dict, Union, List, Optional, Tuple

import nmap
from nmap import PortScannerError
import yaml

from .utils import is_valid_ip, is_valid_domain


class ScanOptions(Enum):
    """Enumeration of common Nmap command-line option fragments."""
    DEFAULT = "-T4"
    OS_FINGERPRINTING = "-O -A"
    ALL_PORTS = "-p-"
    SCRIPT = "--script="


class ScanStatus(Enum):
    """Enumeration representing the status of an Nmap scan."""
    IN_PROGRESS = (0.0, "Scanning {target}...")
    COMPLETE = (1.0, "Scan complete")
    FAILED = (1.0, "Scan failed unexpectedly")
    IDLE = (0.0, "Idle")


def _is_scan_root_required(nmap_args_list: List[str]) -> bool:
    """Check if the given Nmap arguments require root privileges."""
    logger.debug("Checking if root is required for args: %s", nmap_args_list)
    # Common options requiring root: -sS (TCP SYN scan), -O (OS detection), -A (Aggressive scan)
    root_options = ["-sS", "-O", "-A"]
    is_required = any(opt in nmap_args_list for opt in root_options)
    logger.debug("Root required: %s", is_required)
    return is_required


def get_escalated_command(command_parts: List[str]) -> List[str]:
    """Construct a command list for privilege escalation based on the OS."""
    system = platform.system()
    logger.debug("Getting escalated command for: %s on system: %s", command_parts, system)
    if not command_parts:
        return []

    nmap_executable = command_parts[0]
    nmap_path = shutil.which(nmap_executable)
    logger.debug("Nmap path resolved to: %s", nmap_path)

    if not nmap_path:
        raise FileNotFoundError(f"Nmap executable '{nmap_executable}' not found in PATH.")

    resolved_command_parts = [nmap_path] + command_parts[1:]

    escalated_cmd = []
    if system == "Linux":
        if not shutil.which("pkexec"):
            logger.error("pkexec not found, but it is required for privilege escalation on Linux.")
            raise FileNotFoundError("pkexec not found. Needed for privilege escalation.")
        escalated_cmd = ["pkexec"] + resolved_command_parts
    elif system == "Darwin":
        if not shutil.which("osascript"):
            logger.error("osascript not found, but it is required for privilege escalation on macOS.")
            raise FileNotFoundError("osascript not found. Needed for privilege escalation.")
        quoted_command = " ".join(shlex.quote(part) for part in resolved_command_parts)
        osascript_command = f'do shell script "{quoted_command}" with administrator privileges'
        escalated_cmd = ["osascript", "-e", osascript_command]
    else:
        logger.warning("Privilege escalation not configured for system: %s.", system)
        raise NotImplementedError(f"Privilege escalation not supported on this platform: {system}")

    logger.debug("Escalated command: %s", escalated_cmd)
    return escalated_cmd


class NmapScanner:
    """A wrapper around the python-nmap library to perform network scans."""

    def __init__(self):
        """Initialize the NmapScanner with a ThreadPoolExecutor for concurrent scans."""
        logger.debug("NmapScanner initialized.")
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.nm = None

    def __del__(self):
        """Ensure the ThreadPoolExecutor is shut down when the NmapScanner instance is deleted."""
        self.executor.shutdown(wait=True)

    def validate_target_input(self, target: str) -> bool:
        """Validate the target string for Nmap scanning."""
        if not target or not isinstance(target, str): # Handle empty or non-string input early
            return False

        targets = re.split(r"[ ,]+", target.strip())
        if not targets or all(not t for t in targets): # Handle if split results in empty list or list of empty strings
            return False

        ipv4_segment_regex = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9]|0)"
        ipv4_address_regex_str = r"{s}\.{s}\.{s}\.{s}".format(s=ipv4_segment_regex)
        # CIDR regex for IPv4.
        cidr_regex = re.compile(rf"^{ipv4_address_regex_str}/(?:[0-9]|[12][0-9]|3[0-2])$")

        logger.debug(f"Validating Nmap target input: '{target}' (split into: {targets})")

        for t in targets:
            if not t: # Skip empty strings from multiple separators
                continue
            if t.lower() == "localhost":
                logger.debug("Target segment '%s' is 'localhost'. Valid.", t)
                continue
            if cidr_regex.fullmatch(t):
                logger.debug("Target segment '%s' matched CIDR pattern. Valid.", t)
                continue
            if is_valid_ip(t):
                logger.debug("Target segment '%s' is a valid IP. Valid.", t)
                continue
            if is_valid_domain(t):
                logger.debug("Target segment '%s' is a valid domain. Valid.", t)
                continue

            logger.warning("Target segment '%s' is invalid (not localhost, CIDR, IP, or domain).", t)
            return False

        logger.debug(f"All target segments validated successfully for input '{target}'.")
        return True

    def build_nmap_options(
        self, os_fingerprinting: bool, scan_all_ports: bool, selected_script: str
    ) -> str:
        """Construct Nmap command-line options string."""
        options = ScanOptions.DEFAULT.value
        if os_fingerprinting:
            options += f" {ScanOptions.OS_FINGERPRINTING.value}"
        if scan_all_ports:
            options += f" {ScanOptions.ALL_PORTS.value}"
        if selected_script and selected_script != "None":
            options += f" {ScanOptions.SCRIPT.value}{selected_script}"
        logger.debug("Nmap options constructed: %s", options)
        return options

    def _build_nmap_arguments(  # pylint: disable=too-many-arguments
        self, target: str, os_fingerprinting: bool, scan_all_ports: bool,
        selected_script: Optional[str], service_version: bool, no_ping: bool,
        timing_template: str, custom_dns_server: Optional[str],
    ) -> List[str]:
        """Build the list of arguments for the Nmap command."""
        nmap_args_list = ["nmap", "-sS"] # -sS (TCP SYN scan) requires root.

        if os_fingerprinting: nmap_args_list.append("-O")
        if service_version: nmap_args_list.append("-sV")
        if scan_all_ports: nmap_args_list.append("-p-")
        if selected_script and selected_script != "None":
            nmap_args_list.append(f"--script={selected_script}")
        if no_ping: nmap_args_list.append("-Pn")

        if timing_template and re.match(r"^T[0-5]$", timing_template):
            nmap_args_list.append(f"-{timing_template}")
        else:
            nmap_args_list.append("-T3")  # Default timing

        if custom_dns_server and custom_dns_server.strip():
            nmap_args_list.append(f"--dns-servers={custom_dns_server.strip()}")
            logger.info("Using custom DNS server for Nmap scan: %s", custom_dns_server.strip())

        nmap_args_list.extend(["-oX", "-", target])  # XML output to stdout, target last
        logger.debug("Built Nmap arguments: %s", nmap_args_list)
        return nmap_args_list

    def _execute_nmap_command(
        self, nmap_args_list: List[str], needs_escalation: bool
    ) -> Tuple[str, str, int]:
        """Execute the Nmap command, handling privilege escalation if needed."""
        final_command_parts: List[str] = []
        if needs_escalation:
            logger.info("Escalation required for Nmap scan execution.")
            final_command_parts = get_escalated_command(nmap_args_list)
            if not final_command_parts:
                raise PortScannerError("Failed to prepare escalated command (empty result).")
        else:
            nmap_executable = nmap_args_list[0]
            nmap_path = shutil.which(nmap_executable)
            if not nmap_path:
                raise FileNotFoundError(f"Nmap executable '{nmap_executable}' not found for non-escalated command.")
            final_command_parts = [nmap_path] + nmap_args_list[1:]

        logger.info("Executing Nmap command (first few parts): %s...", " ".join(shlex.quote(part) for part in final_command_parts[:4]))
        try:
            process = subprocess.run(final_command_parts, capture_output=True, text=True, check=False, encoding="utf-8")
            return process.stdout, process.stderr, process.returncode
        except Exception as e_subproc:
            logger.exception("Subprocess execution failed for Nmap:")
            raise PortScannerError(f"Nmap subprocess execution failed: {e_subproc}") from e_subproc

    def _parse_nmap_error_message(
        self, returncode: int, nmap_xml_output: str, nmap_stderr: str, needs_escalation: bool,
    ) -> str:
        """Construct a detailed error message from Nmap's output when a scan fails."""
        error_message = f"Nmap scan failed with exit code {returncode}."
        if "QUITTING" in nmap_xml_output or "requires root privileges" in nmap_xml_output:
            output_lines = nmap_xml_output.strip().split("\n")
            for line in output_lines:
                if "QUITTING" in line or "privileges" in line:
                    error_message = line.strip()
                    break
        elif nmap_stderr.strip():
            error_message += f" Stderr: {nmap_stderr.strip()}"

        if needs_escalation:
            if (platform.system() == "Darwin" and returncode == 1 and not nmap_xml_output.strip() and not nmap_stderr.strip()):
                error_message = "User cancelled the request for administrator privileges."
            elif (platform.system() == "Linux" and returncode in [1, 126, 127] and not nmap_xml_output.strip() and not nmap_stderr.strip()):
                error_message = "User cancelled the request for administrator privileges or authentication failed."
        return error_message

    def run_nmap_scan(  # pylint: disable=too-many-arguments,too-many-locals
        self, target: str, os_fingerprinting: bool, scan_all_ports: bool,
        selected_script: Optional[str] = None, service_version: bool = False,
        no_ping: bool = False, timing_template: str = "T3",
        custom_dns_server: Optional[str] = None,
    ) -> nmap.PortScanner:
        logger.debug(
            "run_nmap_scan called with target: %s, OS:%s, AllPorts:%s, Script:%s, Ver:%s, NoPing:%s, Time:%s, DNS:%s",
            target, os_fingerprinting, scan_all_ports, selected_script, service_version, no_ping, timing_template, custom_dns_server
        )
        """Execute an Nmap scan with specified options."""
        self.nm = nmap.PortScanner()
        nmap_args_list = self._build_nmap_arguments(
            target, os_fingerprinting, scan_all_ports, selected_script,
            service_version, no_ping, timing_template, custom_dns_server,
        )
        try:
            needs_escalation = _is_scan_root_required(nmap_args_list)
            nmap_xml_output, nmap_stderr, returncode = self._execute_nmap_command(nmap_args_list, needs_escalation)
            if returncode:
                logger.debug("Nmap process stdout (on error code %s): %s", returncode, nmap_xml_output)
                logger.debug("Nmap process stderr (on error code %s): %s", returncode, nmap_stderr)
                error_message = self._parse_nmap_error_message(returncode, nmap_xml_output, nmap_stderr, needs_escalation)
                logger.error(error_message)
                raise PortScannerError(error_message)
            if not nmap_xml_output.strip():
                logger.warning("Nmap scan completed successfully (RC=0) but produced no XML output.")
                raise PortScannerError("Nmap scan succeeded but produced no XML output.")
            try:
                logger.debug("Attempting to parse Nmap XML output (first 500 chars): %s", nmap_xml_output[:500])
                self.nm.analyse_nmap_xml_scan(nmap_xml_output=nmap_xml_output)
            except PortScannerError as e_parse:
                logger.exception("Failed to parse Nmap XML output:")
                logger.debug("Problematic Nmap XML Output (full, on parse error):\n%s", nmap_xml_output)
                raise PortScannerError(f"Failed to parse Nmap XML output: {e_parse}. Stderr was: '{nmap_stderr.strip()}'") from e_parse
            return self.nm
        except FileNotFoundError as e_fnf:
            logger.exception("Nmap execution prerequisite not found:")
            raise PortScannerError(f"Nmap execution prerequisite not found: {e_fnf}") from e_fnf
        except NotImplementedError as e_ni:
            logger.exception("Privilege escalation not implemented for this platform:")
            raise PortScannerError(f"Privilege escalation not implemented for this platform: {e_ni}") from e_ni
        except PortScannerError: # Specific re-raise
            raise
        except Exception as e_unexpected: # General catch-all
            logger.exception("An unexpected error occurred during the Nmap scan process:")
            raise PortScannerError(f"An unexpected error occurred: {e_unexpected}") from e_unexpected

    def convert_results_to_yaml(self, nm: nmap.PortScanner) -> Dict[str, str]:
        """Convert Nmap scan results to YAML for each host."""
        logger.debug(f"Converting Nmap results to YAML for {len(nm.all_hosts())} hosts.")
        all_results = {}
        prescan_scripts_data = nm.scaninfo().get('prescript', [])
        logger.debug("Pre-scan script data: %s", prescan_scripts_data)
        for host in nm.all_hosts():
            logger.debug("Processing results for host: %s", host)
            host_data = nm[host]
            plain_dict = self.to_plain_dict(host_data)
            if prescan_scripts_data: # Only add if there's actual pre-scan data
                plain_dict["prescript_results"] = prescan_scripts_data
            yaml_output = yaml.safe_dump(plain_dict, default_flow_style=False)
            all_results[host] = yaml_output
        return all_results

    def to_plain_dict(self, data: Any) -> Union[Dict[str, Any], Any]:
        """Recursively convert Nmap data to plain dicts/lists for serialization."""
        # logger.debug(f"to_plain_dict called with data of type: {type(data)}") # Can be very verbose
        if isinstance(data, nmap.PortScannerHostDict):
            return {k: self.to_plain_dict(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self.to_plain_dict(item) for item in data]
        if isinstance(data, dict):
            return {k: self.to_plain_dict(v) for k, v in data.items()}
        return data
