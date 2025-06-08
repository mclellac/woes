import logging
import re
import platform
import subprocess
import shlex
import shutil
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Any, Dict, Union, List, Optional

import nmap
from nmap import PortScannerError
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
    IDLE = (0.0, "Idle")


def _is_scan_root_required(nmap_args_list: list[str]) -> bool:
    logging.debug("Checking if root is required for args: %s", nmap_args_list)
    root_options = ["-sS", "-O", "-A"]
    is_required = any(opt in nmap_args_list for opt in root_options)
    logging.debug("Root required: %s", is_required)
    return is_required


def get_escalated_command(command_parts: List[str]) -> List[str]:
    system = platform.system()
    logging.debug("Getting escalated command for: %s on system: %s", command_parts, system)
    if not command_parts:
        return []

    nmap_executable = command_parts[0]
    nmap_path = shutil.which(nmap_executable)
    logging.debug("Nmap path resolved to: %s", nmap_path)

    if not nmap_path:
        raise FileNotFoundError(
            f"Nmap executable '{nmap_executable}' not found in PATH."
            )

    resolved_command_parts = [nmap_path] + command_parts[1:]

    escalated_cmd = []
    if system == "Linux":
        if not shutil.which("pkexec"):
            logging.error(
                "pkexec not found, but it is required for privilege escalation on Linux."
                )
            raise FileNotFoundError(
                "pkexec not found. Needed for privilege escalation."
                )
        escalated_cmd = ["pkexec"] + resolved_command_parts
    elif system == "Darwin":
        if not shutil.which("osascript"):
            logging.error(
                "osascript not found, but it is required for privilege escalation on macOS."
                )
            raise FileNotFoundError(
                "osascript not found. Needed for privilege escalation."
                )
        quoted_command = " ".join(shlex.quote(part) for part in resolved_command_parts)
        osascript_command = (
            f'do shell script "{quoted_command}" with administrator privileges'
            )
        escalated_cmd = ["osascript", "-e", osascript_command]
    else:
        logging.warning(
            "Privilege escalation not configured for system: %s. Returning original command.", system
            )
        raise NotImplementedError(
            f"Privilege escalation not supported on this platform: {system}"
            )

    logging.debug("Escalated command: %s", escalated_cmd)
    return escalated_cmd


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
        self.nm = None # Initialize nm attribute

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
        cidr = rf"{ipv4_address}/[0-9]{{1,2}}"

        addr_regex = rf"^(localhost|{ipv4_address}|{fqdn}|{cidr})$"

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

    def _build_nmap_arguments( # pylint: disable=too-many-arguments
            self,
            target: str,
            os_fingerprinting: bool,
            scan_all_ports: bool,
            selected_script: Optional[str],
            service_version: bool,
            no_ping: bool,
            timing_template: str,
            custom_dns_server: Optional[str],
            ) -> List[str]:
        """Builds the list of arguments for the Nmap command."""
        nmap_args_list = ["nmap"]

        # Basic scan type, -sS requires root, consider if non-root default is needed if -sS fails.
        nmap_args_list.append("-sS")

        if os_fingerprinting:
            nmap_args_list.append("-O")
        if service_version:
            nmap_args_list.append("-sV")
        if scan_all_ports:
            nmap_args_list.append("-p-")
        if selected_script and selected_script != "None":
            nmap_args_list.append(f"--script={selected_script}")
        if no_ping:
            nmap_args_list.append("-Pn")

        if timing_template and re.match(r"^T[0-5]$", timing_template):
            nmap_args_list.append(f"-{timing_template}")
        else:
            nmap_args_list.append("-T3")  # Default timing

        if custom_dns_server and custom_dns_server.strip():
            nmap_args_list.append(f"--dns-servers={custom_dns_server.strip()}")
            logging.info(
                "Using custom DNS server for Nmap scan: %s", custom_dns_server.strip()
                )

        nmap_args_list.extend(["-oX", "-", target])  # XML output to stdout, target last
        logging.debug("Built Nmap arguments: %s", nmap_args_list)
        return nmap_args_list

    def _execute_nmap_command(
            self, nmap_args_list: List[str], needs_escalation: bool
            ) -> tuple[str, str, int]:
        """
        Executes the Nmap command, handling escalation, and returns raw output.
        Returns (stdout, stderr, returncode).
        Raises FileNotFoundError if nmap/pkexec/osascript is not found.
        Raises PortScannerError for other execution setup issues.
        """
        final_command_parts: List[str] = []

        if needs_escalation:
            logging.info("Escalation required for Nmap scan execution.")
            final_command_parts = get_escalated_command(
                nmap_args_list
                )
            if not final_command_parts:
                raise PortScannerError(
                    "Failed to prepare escalated command (empty result)."
                    )
        else:
            nmap_executable = nmap_args_list[0]
            nmap_path = shutil.which(nmap_executable)
            if not nmap_path:
                raise FileNotFoundError(
                    f"Nmap executable '{nmap_executable}' not found for non-escalated command."
                    )
            final_command_parts = [nmap_path] + nmap_args_list[1:]

        logging.info(
            "Executing Nmap command (first few parts): %s...",
            ' '.join(shlex.quote(part) for part in final_command_parts[:4])
            )

        try:
            process = subprocess.run(
                final_command_parts,
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                )
            return process.stdout, process.stderr, process.returncode
        except Exception as e_subproc:
            logging.error(
                "Subprocess execution failed for Nmap: %s", e_subproc, exc_info=True
                )
            raise PortScannerError(f"Nmap subprocess execution failed: {e_subproc}") from e_subproc

    def _parse_nmap_error_message(
            self,
            returncode: int,
            nmap_xml_output: str,
            nmap_stderr: str,
            needs_escalation: bool,
            ) -> str:
        """Constructs a detailed error message from Nmap's output when a scan fails."""
        error_message = f"Nmap scan failed with exit code {returncode}."

        if (
                "QUITTING" in nmap_xml_output
                or "requires root privileges" in nmap_xml_output
                ):
            output_lines = nmap_xml_output.strip().split("\n")
            for line in output_lines:
                if "QUITTING" in line or "privileges" in line:
                    error_message = line.strip()
                    break
        elif nmap_stderr.strip():
            error_message += f" Stderr: {nmap_stderr.strip()}"

        if needs_escalation:
            if (
                    platform.system() == "Darwin"
                    and returncode == 1
                    and not nmap_xml_output.strip()
                    and not nmap_stderr.strip()
                    ):
                error_message = (
                    "User cancelled the request for administrator privileges."
                    )
            elif (
                    platform.system() == "Linux"
                    and returncode in [1, 126, 127]
                    and not nmap_xml_output.strip()
                    and not nmap_stderr.strip()
                    ):
                error_message = "User cancelled the request for administrator privileges or authentication failed."
        return error_message

    def run_nmap_scan( # pylint: disable=too-many-arguments,too-many-locals
            self,
            target: str,
            os_fingerprinting: bool,
            scan_all_ports: bool,
            selected_script: Optional[str] = None,
            service_version: bool = False,
            no_ping: bool = False,
            timing_template: str = "T3",
            custom_dns_server: Optional[str] = None,
            ) -> nmap.PortScanner:
        """
        Executes an Nmap scan against the specified target with the given options.
        Handles argument building, command execution (including privilege escalation),
        and result parsing.
        """
        self.nm = nmap.PortScanner()

        nmap_args_list = self._build_nmap_arguments(
            target,
            os_fingerprinting,
            scan_all_ports,
            selected_script,
            service_version,
            no_ping,
            timing_template,
            custom_dns_server,
            )

        try:
            needs_escalation = _is_scan_root_required(nmap_args_list)
            nmap_xml_output, nmap_stderr, returncode = self._execute_nmap_command(
                nmap_args_list, needs_escalation
                )

            if returncode: # C1805
                logging.debug(
                    "Nmap process stdout (on error code %s): %s", returncode, nmap_xml_output
                    )
                logging.debug(
                    "Nmap process stderr (on error code %s): %s", returncode, nmap_stderr
                    )

                error_message = self._parse_nmap_error_message(
                    returncode, nmap_xml_output, nmap_stderr, needs_escalation
                    )
                logging.error(error_message)
                raise PortScannerError(error_message)

            if not nmap_xml_output.strip():
                logging.warning(
                    "Nmap scan completed successfully (RC=0) but produced no XML output."
                    )
                raise PortScannerError(
                    "Nmap scan succeeded but produced no XML output."
                    )

            try:
                logging.debug(
                    "Attempting to parse Nmap XML output (first 500 chars): %s", nmap_xml_output[:500]
                    )
                self.nm.analyse_nmap_xml_scan(nmap_xml_output=nmap_xml_output)
            except PortScannerError as e_parse:
                logging.error("Failed to parse Nmap XML output: %s", e_parse)
                logging.debug(
                    "Problematic Nmap XML Output (full, on parse error):\n%s", nmap_xml_output
                    )
                raise PortScannerError(
                    f"Failed to parse Nmap XML output: {e_parse}. Stderr was: '{nmap_stderr.strip()}'"
                    ) from e_parse

            return self.nm

        except FileNotFoundError as e_fnf:
            logging.error("Nmap execution prerequisite not found: %s", e_fnf)
            raise PortScannerError(f"Nmap execution prerequisite not found: {e_fnf}") from e_fnf
        except NotImplementedError as e_ni:
            logging.error(
                "Privilege escalation not implemented for this platform: %s", e_ni
                )
            raise PortScannerError(
                f"Privilege escalation not implemented for this platform: {e_ni}"
                ) from e_ni
        except PortScannerError:
            raise
        except Exception as e_unexpected:
            logging.error(
                "An unexpected error occurred during the Nmap scan process: %s", e_unexpected,
                exc_info=True,
                )
            raise PortScannerError(f"An unexpected error occurred: {e_unexpected}") from e_unexpected

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
        if isinstance(data, list): # R1705 (no-else-return makes this an if)
            return [self.to_plain_dict(item) for item in data]
        if isinstance(data, dict): # R1705 (no-else-return makes this an if)
            return {k: self.to_plain_dict(v) for k, v in data.items()}
        return data # R1705
