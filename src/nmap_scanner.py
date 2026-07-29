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
import time
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Any, Optional, TypedDict, Union, Callable

try:
    from gi.repository import Gio
except ImportError:
    Gio = None  # type: ignore

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


class ScanCancelledError(PortScannerError):
    """Exception raised when an Nmap scan is cancelled."""

    pass


class NmapScanParameters(TypedDict, total=False):
    """TypedDict for Nmap scan parameters.

    :param target: The target for the Nmap scan.
    :param os_fingerprinting: Whether to enable OS fingerprinting.
    :param scan_all_ports: Whether to scan all ports.
    :param selected_script: The Nmap script to use.
    :param service_version: Whether to detect service versions.
    :param no_ping: Whether to disable host discovery ping.
    :param timing_template: The timing template to use (e.g., "T3", "T4").
    :param custom_dns_server: Custom DNS server to use for the scan.
    :param tcp_syn_scan: Whether to perform a TCP SYN scan.
    :param output_format: The desired output format for the scan results (e.g., "Normal", "XML", "Grepable").
    :param output_filename: The filename to save the scan results to.
    """

    target: str
    os_fingerprinting: bool
    scan_all_ports: bool
    selected_script: Optional[str]
    service_version: bool
    no_ping: bool
    timing_template: str
    custom_dns_server: Optional[str]
    tcp_syn_scan: bool
    output_format: Optional[str]
    output_filename: Optional[str]


def _is_scan_root_required(nmap_args_list: list[str]) -> bool:
    """Check if the given Nmap arguments require root privileges.

    :param nmap_args_list: A list of Nmap command arguments.
    :return: ``True`` if root privileges are required, ``False`` otherwise.
    """
    logger.debug("Checking if root is required for args: %s", nmap_args_list)
    root_options = ["-sS", "-O", "-A"]
    is_required = any(opt in nmap_args_list for opt in root_options)
    logger.debug("Root required: %s", is_required)
    return is_required


def get_escalated_command(command_parts: list[str]) -> list[str]:
    """Construct a command list for privilege escalation based on the OS.

    :param command_parts: The command parts to escalate.
    :raises FileNotFoundError: If ``nmap`` or a required escalation tool (``pkexec``, ``osascript``) is not found.
    :raises NotImplementedError: If privilege escalation is not supported on the current platform.
    :return: The command list with privilege escalation.
    """
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
    if system == "Linux" or "BSD" in system or system in ("FreeBSD", "OpenBSD", "NetBSD", "DragonFly"):
        if shutil.which("pkexec"):
            escalated_cmd = ["pkexec"] + resolved_command_parts
        elif shutil.which("doas"):
            escalated_cmd = ["doas"] + resolved_command_parts
        elif shutil.which("sudo"):
            escalated_cmd = ["sudo"] + resolved_command_parts
        else:
            logger.error("No privilege escalation tool (pkexec, doas, sudo) found.")
            raise FileNotFoundError("No privilege escalation tool (pkexec, doas, sudo) found.")
    elif system == "Darwin":
        if not shutil.which("osascript"):
            logger.error("osascript not found, but it is required for privilege escalation on macOS.")
            raise FileNotFoundError("osascript not found. Needed for privilege escalation.")
        quoted_command = " ".join(shlex.quote(part) for part in resolved_command_parts)
        escaped_command = quoted_command.replace("\\", "\\\\").replace('"', '\\"')
        osascript_command = f'do shell script "{escaped_command}" with administrator privileges'
        escalated_cmd = ["osascript", "-e", osascript_command]
    else:
        logger.warning("Privilege escalation not configured for system: %s.", system)
        raise NotImplementedError(f"Privilege escalation not supported on this platform: {system}")

    logger.debug("Escalated command: %s", escalated_cmd)
    return escalated_cmd


class NmapScanner:
    """A wrapper around the python-nmap library to perform network scans.

    This class manages the execution of Nmap scans, including building
    command arguments, handling privilege escalation if needed, running
    the scan process, and parsing the results.
    """

    def __init__(self):
        """Initialize the NmapScanner."""
        logger.debug("NmapScanner initialized.")
        self.executor: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=4)
        self.nm: Optional[nmap.PortScanner] = None
        self.current_process: Optional[subprocess.Popen[str]] = None
        self.current_cancellable: Optional[Gio.Cancellable] = None

    def __del__(self) -> None:
        """Ensure the ThreadPoolExecutor is shut down and any running Nmap process is terminated."""
        logger.debug("NmapScanner.__del__ called.")
        if self.current_process and self.current_process.poll() is None:
            logger.info("Terminating active Nmap process during NmapScanner deletion.")
            try:
                self.current_process.terminate()
                self.current_process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                logger.warning("Nmap process did not terminate gracefully, killing.")
                self.current_process.kill()
            except OSError as e_os:
                logger.exception("OS error terminating Nmap process during deletion: %s", e_os)
            except Exception as e:
                logger.exception("Unexpected error terminating Nmap process during deletion: %s", e)
            self.current_process = None

        if hasattr(self, "executor") and self.executor is not None:
            self.executor.shutdown(wait=True)
        logger.debug("NmapScanner cleanup complete.")

    def validate_target_input(self, target: str) -> bool:
        """Validate the target string for Nmap scanning.

        The target can be an IP address, a domain name, a CIDR block,
        or 'localhost'. Multiple targets can be specified, separated by
        commas or spaces.

        :param target: The target string to validate.
        :return: ``True`` if the target string is valid, ``False`` otherwise.
        """
        if not target or not isinstance(target, str):
            return False

        targets: list[str] = re.split(r"[ ,]+", target.strip())
        if not targets or all(not t for t in targets):
            return False

        ipv4_segment_regex = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9]|0)"
        ipv4_address_regex_str = r"{s}\.{s}\.{s}\.{s}".format(s=ipv4_segment_regex)
        cidr_regex = re.compile(rf"^{ipv4_address_regex_str}/(?:[0-9]|[12][0-9]|3[0-2])$")

        logger.debug(f"Validating Nmap target input: '{target}' (split into: {targets})")

        for t in targets:
            if not t:
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

    def build_nmap_options(self, os_fingerprinting: bool, scan_all_ports: bool, selected_script: str) -> str:
        """Construct Nmap command-line options string based on boolean flags.

        :param os_fingerprinting: If ``True``, add OS fingerprinting options.
        :param scan_all_ports: If ``True``, add all ports scan option.
        :param selected_script: Name of the Nmap script to use (or "None").
        :return: A string of Nmap command-line options.
        """
        options = ScanOptions.DEFAULT.value
        if os_fingerprinting:
            options += f" {ScanOptions.OS_FINGERPRINTING.value}"
        if scan_all_ports:
            options += f" {ScanOptions.ALL_PORTS.value}"
        if selected_script and selected_script != "None":
            options += f" {ScanOptions.SCRIPT.value}{selected_script}"
        logger.debug("Nmap options constructed: %s", options)
        return options

    def _build_nmap_arguments(self, params: NmapScanParameters) -> list[str]:
        """Build the list of arguments for the Nmap command.

        :param params: A dictionary of Nmap scan parameters, conforming to :class:`.NmapScanParameters`.
        :return: A list of arguments for the Nmap command.
        """
        nmap_args_list: list[str] = ["nmap"]

        if params.get("tcp_syn_scan"):
            nmap_args_list.append("-sS")  # -sS (TCP SYN scan) requires root
        if params.get("os_fingerprinting"):
            nmap_args_list.append("-O")
        if params.get("service_version"):
            nmap_args_list.append("-sV")
        if params.get("scan_all_ports"):
            nmap_args_list.append("-p-")
        if params.get("selected_script") and params["selected_script"] != "None":  # type: ignore[comparison-overlap]
            nmap_args_list.append(f"--script={params['selected_script']}")  # type: ignore[literal-required]
        if params.get("no_ping"):
            nmap_args_list.append("-Pn")
        if params.get("fast_scan"):
            nmap_args_list.append("-F")
        if params.get("ping_scan"):
            nmap_args_list.append("-sn")
        if params.get("intense_scan"):
            nmap_args_list.append("-A")

        timing_template = params.get("timing_template", "T3")
        if timing_template and re.match(r"^T[0-5]$", timing_template):
            nmap_args_list.append(f"-{timing_template}")
        else:
            nmap_args_list.append("-T3")

        custom_dns_server = params.get("custom_dns_server")
        if custom_dns_server and custom_dns_server.strip():
            nmap_args_list.append(f"--dns-servers={custom_dns_server.strip()}")
            logger.info("Using custom DNS server for Nmap scan: %s", custom_dns_server.strip())

        # Always add -oX - for existing UI functionality to parse XML output from stdout
        nmap_args_list.extend(["-oX", "-"])
        nmap_args_list.extend(["--stats-every", "1s"])

        output_format = params.get("output_format")
        output_filename = params.get("output_filename")

        if output_format and output_filename:
            if output_format == "Normal (.txt)":
                nmap_args_list.extend(["-oN", output_filename])
            elif output_format == "Grepable (.gnmap)":
                nmap_args_list.extend(["-oG", output_filename])
            elif output_format == "XML (.xml)":
                # This will be a separate file from the stdout XML used by analyze_nmap_xml_scan
                nmap_args_list.extend(["-oX", output_filename])
            # else: Consider adding a log message for unhandled formats if necessary

        nmap_args_list.append(params["target"])
        logger.debug("Built Nmap arguments: %s", nmap_args_list)
        return nmap_args_list

    def _prepare_final_nmap_command(self, nmap_args_list: list[str], needs_escalation: bool) -> list[str]:
        """Prepare the final Nmap command list, including path resolution and escalation.

        :param nmap_args_list: The base list of Nmap arguments.
        :param needs_escalation: Whether privilege escalation is required.
        :raises PortScannerError: If escalation fails or ``nmap`` executable is not found.
        :raises FileNotFoundError: If ``nmap`` executable is not found for non-escalated command.
        :return: The final list of command parts for execution.
        """
        final_command_parts: list[str] = []
        if needs_escalation:
            logger.info("Escalation required for Nmap scan execution.")
            final_command_parts = get_escalated_command(nmap_args_list)
            if not final_command_parts:
                raise PortScannerError("Failed to prepare escalated command (empty result from get_escalated_command).")
        else:
            nmap_executable = nmap_args_list[0]
            nmap_path = shutil.which(nmap_executable)
            if not nmap_path:
                raise FileNotFoundError(f"Nmap executable '{nmap_executable}' not found for non-escalated command.")
            final_command_parts = [nmap_path] + nmap_args_list[1:]
        return final_command_parts

    def _parse_nmap_error_message(
        self,
        returncode: int,
        nmap_xml_output: str,
        nmap_stderr: str,
        needs_escalation: bool,
    ) -> str:
        """Construct a detailed error message from Nmap's output when a scan fails.

        :param returncode: The exit code from the Nmap process.
        :param nmap_xml_output: The stdout (XML output) from Nmap.
        :param nmap_stderr: The stderr output from Nmap.
        :param needs_escalation: Whether the scan attempted privilege escalation.
        :return: A detailed error message string.
        """
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
            if (
                platform.system() == "Darwin"
                and returncode == 1
                and not nmap_xml_output.strip()
                and not nmap_stderr.strip()
            ):
                error_message = "User cancelled the request for administrator privileges."
            elif (
                (platform.system() == "Linux" or "BSD" in platform.system() or platform.system() in ("FreeBSD", "OpenBSD", "NetBSD", "DragonFly"))
                and returncode in [1, 126, 127]
                and not nmap_xml_output.strip()
                and not nmap_stderr.strip()
            ):
                error_message = "User cancelled the request for administrator privileges or authentication failed."
        return error_message

    def run_nmap_scan(
        self,
        params: NmapScanParameters,
        cancellable: Optional[Gio.Cancellable] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> nmap.PortScanner:
        """Run an Nmap scan with the given parameters and handle cancellation.

        This method constructs the Nmap command, executes it as a subprocess,
        monitors for cancellation, and parses the XML output.

        :param params: The parameters for the Nmap scan, conforming to :class:`.NmapScanParameters`.
        :param cancellable: An optional :class:`Gio.Cancellable` object to monitor for cancellation requests.
        :param progress_callback: An optional callback to report real-time scanning progress (0.0 to 1.0) and description.
        :raises .ScanCancelledError: If the scan is cancelled.
        :raises nmap.PortScannerError: If the Nmap scan fails, prerequisites are missing,
                                  or output parsing fails.
        :return: An :class:`nmap.PortScanner` object containing the scan results.
        """
        logger.debug("run_nmap_scan called with params: %s, Cancellable: %s", params, bool(cancellable))
        self.nm = nmap.PortScanner()
        self.current_cancellable = cancellable

        nmap_args_list: list[str] = self._build_nmap_arguments(params)
        needs_escalation: bool = _is_scan_root_required(nmap_args_list)

        final_command_parts: list[str] = self._prepare_final_nmap_command(nmap_args_list, needs_escalation)

        logger.info(
            "Executing Nmap command (first few parts): %s...",
            " ".join(shlex.quote(part) for part in final_command_parts[:4]),
        )

        stdout_str: str = ""
        stderr_str: str = ""
        returncode: int = -1

        try:
            if self.current_cancellable and self.current_cancellable.is_cancelled():
                raise ScanCancelledError("Scan cancelled before process start.")

            self.current_process = subprocess.Popen(
                final_command_parts, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8"
            )

            collected_stderr_lines = []
            import threading
            from gi.repository import GLib

            def read_stderr():
                try:
                    for line in iter(self.current_process.stderr.readline, ""):
                        collected_stderr_lines.append(line)
                        if "Timing: About" in line:
                            match = re.search(r"About\s+(\d+(?:\.\d+)?)\%\s+done", line)
                            if match and progress_callback:
                                percentage = float(match.group(1))
                                GLib.idle_add(progress_callback, percentage / 100.0, line.strip())
                        elif "undergoing" in line:
                            if progress_callback:
                                GLib.idle_add(progress_callback, 0.0, line.strip())
                except Exception as e_read:
                    logger.debug("Error reading Nmap stderr: %s", e_read)

            stderr_thread = threading.Thread(target=read_stderr, daemon=True)
            stderr_thread.start()

            while self.current_process.poll() is None:
                if self.current_cancellable and self.current_cancellable.is_cancelled():
                    logger.info("Cancellation requested for Nmap scan of target: %s", params["target"])  # type: ignore[literal-required]
                    self.current_process.terminate()
                    try:
                        self.current_process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        logger.warning("Nmap process did not terminate gracefully, killing.")
                        self.current_process.kill()
                    self.current_process = None
                    raise ScanCancelledError(f"Nmap scan for {params['target']} was cancelled.")  # type: ignore[literal-required]
                time.sleep(0.2)

            if self.current_process:
                stdout_str = self.current_process.stdout.read()
                returncode = self.current_process.returncode
                self.current_process = None
                stderr_thread.join(timeout=1.0)
                stderr_str = "".join(collected_stderr_lines)

            if returncode != 0:
                logger.debug("Nmap process stdout (on error code %s): %s", returncode, stdout_str)
                logger.debug("Nmap process stderr (on error code %s): %s", returncode, stderr_str)
                error_message = self._parse_nmap_error_message(returncode, stdout_str, stderr_str, needs_escalation)
                logger.error(error_message)
                raise PortScannerError(error_message)

            if not stdout_str.strip():
                logger.warning("Nmap scan completed successfully (RC=0) but produced no XML output.")
                raise PortScannerError("Nmap scan succeeded but produced no XML output.")

            try:
                logger.debug("Attempting to parse Nmap XML output (first 500 chars): %s", stdout_str[:500])
                self.nm.analyse_nmap_xml_scan(nmap_xml_output=stdout_str)  # type: ignore[no-untyped-call]
            except PortScannerError as e_parse:
                logger.exception("Failed to parse Nmap XML output:")
                logger.debug("Problematic Nmap XML Output (full, on parse error):\n%s", stdout_str)
                raise PortScannerError(
                    f"Failed to parse Nmap XML output: {e_parse}. Stderr was: '{stderr_str.strip()}'"
                ) from e_parse

            return self.nm

        except FileNotFoundError as e_fnf:
            logger.exception("Nmap execution prerequisite not found:")
            raise PortScannerError(f"Nmap execution prerequisite not found: {e_fnf}") from e_fnf
        except NotImplementedError as e_ni:
            logger.exception("Privilege escalation not implemented for this platform:")
            raise PortScannerError(f"Privilege escalation not implemented for this platform: {e_ni}") from e_ni
        except ScanCancelledError:
            raise
        except PortScannerError:
            raise
        except TypeError as e_type:
            logger.exception("Type error during Nmap scan setup or execution:")
            raise PortScannerError(f"Type error encountered: {e_type}") from e_type
        except ValueError as e_value:
            logger.exception("Value error during Nmap scan setup or execution (e.g., invalid arguments):")
            raise PortScannerError(f"Value error encountered: {e_value}") from e_value
        except OSError as e_os:
            logger.exception("OS error occurred during Nmap scan process (excluding FileNotFoundError):")
            raise PortScannerError(f"OS error occurred: {e_os}") from e_os
        except Exception as e_unexpected:
            logger.exception("An unexpected error occurred during the Nmap scan process:")
            raise PortScannerError(f"An unexpected error occurred: {e_unexpected}") from e_unexpected
        finally:
            self.current_process = None
            self.current_cancellable = None

    def convert_results_to_yaml(self, nm: nmap.PortScanner) -> dict[str, str]:
        """Convert Nmap scan results to YAML for each host.

        :param nm: The :class:`nmap.PortScanner` object containing the scan results.
        :return: A dictionary where keys are host IPs and values are YAML strings
                 representing the scan results for that host.
        """
        logger.debug("Converting Nmap results to YAML for %s hosts.", len(nm.all_hosts()))
        all_results: dict[str, str] = {}
        prescan_scripts_data: list[Any] = nm.scaninfo().get("prescript", [])  # type: ignore[no-untyped-call]
        logger.debug("Pre-scan script data: %s", prescan_scripts_data)
        for host in nm.all_hosts():
            logger.debug("Processing results for host: %s", host)
            host_data = nm[host]
            plain_dict = self.to_plain_dict(host_data)
            if prescan_scripts_data:
                plain_dict["prescript_results"] = prescan_scripts_data  # type: ignore
            yaml_output = yaml.safe_dump(plain_dict, default_flow_style=False)
            all_results[host] = yaml_output
        return all_results

    def to_plain_dict(self, data: Any) -> Union[dict[str, Any], Any]:
        """Recursively convert Nmap data (potentially custom nmap types) to plain dicts/lists.

        This is necessary for proper serialization to formats like YAML or JSON.

        :param data: The Nmap data to convert.
        :return: The data converted to plain Python dicts, lists, and primitive types.
        """
        if isinstance(data, nmap.PortScannerHostDict):
            return {k: self.to_plain_dict(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self.to_plain_dict(item) for item in data]
        if isinstance(data, dict):
            return {k: self.to_plain_dict(v) for k, v in data.items()}
        return data
