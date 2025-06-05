import logging
import re
import platform
import subprocess
import shlex
import shutil
import tempfile # Added as per subtask description
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Any, Dict, Union, List

import nmap
from nmap import PortScannerError # Added as per subtask description
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


# Helper function (module-level)
def _is_scan_root_required(nmap_args_list: list[str]) -> bool:
    # Check for options that typically require root privileges.
    # -sS is the default TCP SYN scan.
    # -O is OS detection.
    # -A (Aggressive) implies -sS and -O.
    # Other options like --traceroute (often part of -A) can also require root.
    root_options = ["-sS", "-O", "-A"] # Add others if known
    for opt in root_options:
        if opt in nmap_args_list:
            return True

    # If no specific scan type is given, nmap might default to -sS if it thinks it has root,
    # or -sT otherwise. Since we explicitly add -sS as our default, this check is primary.
    return False


def get_escalated_command(command_parts: List[str]) -> List[str]:
    system = platform.system()
    if not command_parts:
        return []

    nmap_executable = command_parts[0]
    nmap_path = shutil.which(nmap_executable)

    if not nmap_path:
        # If nmap is not found, escalation won't help.
        # This error should ideally be caught even before attempting escalation.
        raise FileNotFoundError(f"Nmap executable '{nmap_executable}' not found in PATH.")

    # Use the full path for the command
    resolved_command_parts = [nmap_path] + command_parts[1:]

    if system == "Linux":
        # pkexec by default does not inherit the user's PATH for security reasons.
        # It's crucial to use the full path to the executable.
        return ["pkexec"] + resolved_command_parts
    elif system == "Darwin": # macOS
        quoted_command = " ".join(shlex.quote(part) for part in resolved_command_parts)
        osascript_command = f'do shell script "{quoted_command}" with administrator privileges'
        return ["osascript", "-e", osascript_command]
    else:
        logging.warning(f"Privilege escalation not configured for system: {system}. Returning original command.")
        # It's better to raise an error if escalation is expected but not possible.
        raise NotImplementedError(f"Privilege escalation not supported on this platform: {system}")


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
        selected_script: str = None,
        service_version: bool = False,
        no_ping: bool = False,
        timing_template: str = "T3"
    ) -> nmap.PortScanner:
        """
        Executes an Nmap scan against the specified target with the given options,
        handling privilege escalation and using subprocess.

        Args:
            target: The target host(s) for the Nmap scan.
            os_fingerprinting: Whether to enable OS fingerprinting.
            scan_all_ports: Whether to scan all TCP ports.
            selected_script: Specific Nmap script to run.

        Returns:
            An nmap.PortScanner object containing the scan results.

        Raises:
            PortScannerError: If Nmap encounters an error or fails to execute/parse.
            FileNotFoundError: If Nmap executable is not found (via PortScannerError).
            NotImplementedError: If OS is not supported for escalation (via PortScannerError).
        """
        self.nm = nmap.PortScanner() # Still useful for parsing

        nmap_args_list = ["nmap"] # Start with 'nmap' as executable name

        # Base scan type: Default to TCP SYN scan (-sS).
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
            nmap_args_list.append("-T3") # Default timing

        nmap_args_list.append("-oX") # Output XML
        nmap_args_list.append("-")   # to stdout

        nmap_args_list.append(target) # Add target at the end

        logging.info(f"Nmap base command parts: {nmap_args_list}")

        final_command_parts = []
        needs_escalation = _is_scan_root_required(nmap_args_list)

        try:
            if needs_escalation:
                logging.info("Escalation required for Nmap scan.")
                final_command_parts = get_escalated_command(nmap_args_list)
                if not final_command_parts:
                    raise PortScannerError("Failed to prepare escalated command.")
            else:
                nmap_executable = nmap_args_list[0]
                nmap_path = shutil.which(nmap_executable)
                if not nmap_path:
                    raise FileNotFoundError(f"Nmap executable '{nmap_executable}' not found.")
                final_command_parts = [nmap_path] + nmap_args_list[1:]

            logging.info(f"Executing Nmap command: {' '.join(shlex.quote(part) for part in final_command_parts)}")

            process = subprocess.run(final_command_parts, capture_output=True, text=True, check=False, encoding='utf-8')

            nmap_xml_output = process.stdout
            nmap_stderr = process.stderr

            if process.returncode != 0:
                error_message = f"Nmap scan failed with exit code {process.returncode}."
                # Try to get a more specific error from Nmap's output
                if "QUITTING" in nmap_xml_output or "requires root privileges" in nmap_xml_output:
                    output_lines = nmap_xml_output.strip().split('\n')
                    for line in output_lines:
                        if "QUITTING" in line or "privileges" in line:
                            error_message = line.strip() # Use Nmap's direct message
                            break
                elif nmap_stderr: # If not an obvious Nmap stdout error, use stderr
                    error_message += f" Stderr: {nmap_stderr.strip()}"

                # Check for cancellation of privilege escalation prompts
                if needs_escalation:
                    if platform.system() == "Darwin" and process.returncode == 1 and not nmap_xml_output and not nmap_stderr.strip(): # osascript cancel
                         error_message = "User cancelled the request for administrator privileges."
                    elif platform.system() == "Linux" and process.returncode in [1, 126, 127] and not nmap_xml_output and not nmap_stderr.strip(): # pkexec common cancel/fail codes
                         error_message = "User cancelled the request for administrator privileges or authentication failed."

                logging.error(error_message)
                raise PortScannerError(error_message)

            if not nmap_xml_output.strip(): # Check if output is empty or just whitespace
                no_output_msg = "Nmap scan completed but produced no XML output."
                if nmap_stderr.strip(): # Add stderr if it has content
                    no_output_msg += f" Stderr: {nmap_stderr.strip()}"
                logging.warning(no_output_msg)
                # Allow parsing of empty string, analyse_nmap_xml_scan should handle it
                pass

            try:
                self.nm.analyse_nmap_xml_scan(nmap_xml_output=nmap_xml_output)
            except PortScannerError as e:
                logging.error(f"Failed to parse Nmap XML output: {e}")
                logging.debug(f"Problematic XML Output (first 1000 chars):\n{nmap_xml_output[:1000]}...")
                raise PortScannerError(f"Failed to parse Nmap XML output: {e}. Stderr: {nmap_stderr.strip()}")

            return self.nm

        except FileNotFoundError as e:
            logging.error(f"Nmap execution error: {e}")
            raise PortScannerError(str(e))
        except NotImplementedError as e:
            logging.error(f"Nmap execution error: {e}")
            raise PortScannerError(str(e))
        except PortScannerError: # Re-raise if it's already the correct type
            raise
        except Exception as e:
            logging.error(f"Unexpected error during Nmap scan process: {e}", exc_info=True)
            raise PortScannerError(f"An unexpected error occurred: {e}")

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
