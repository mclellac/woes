"""Provides the :class:`NmapScanner` for orchestrating Nmap scans.

This module includes the main :class:`NmapScanner` class, which handles
Nmap command construction, execution (including privilege escalation via
`pkexec` or `osascript`), and parsing of Nmap XML output. It also defines
supporting enumerations for scan options and status, a TypedDict for scan
parameters, and a custom exception for scan cancellations.
"""
from .utils import is_valid_ip, is_valid_domain
import yaml
from nmap import PortScannerError # Used as a base for custom exceptions and raised directly
import nmap # Used for nmap.PortScanner type hint and result processing
from typing import Any, Dict, List, Optional, TypedDict, Union
from enum import Enum
from concurrent.futures import ThreadPoolExecutor # For running scans in a separate thread
import time
import shutil # For shutil.which to find executables
import shlex # For shlex.quote for safe command string formatting
import subprocess # For running Nmap process
import platform # For OS-specific privilege escalation
import re # For input validation and parsing
import logging
logger = logging.getLogger(__name__)

# Import Gio for Cancellable type hint, actual object passed by NmapPage.
try:
    from gi.repository import Gio
except ImportError:
    # Fallback if gi.repository is not available (e.g., during CLI testing or headless environments).
    # The NmapPage, which uses this scanner, is responsible for passing an actual Cancellable object.
    Gio = None # type: ignore


class ScanOptions(Enum):
    """Enumeration of common Nmap command-line option fragments.

    These represent parts of Nmap commands that can be combined to form
    a full Nmap execution string.
    """
    DEFAULT = "-T4"  # Default timing template for Nmap scans.
    OS_FINGERPRINTING = "-O -A"  # Options for OS detection and aggressive scan features.
    ALL_PORTS = "-p-"  # Option to scan all 65535 TCP ports.
    SCRIPT = "--script="  # Base for specifying Nmap NSE scripts.


class ScanStatus(Enum):
    """Represents the various states of an Nmap scan operation.

    Each status includes a progress value (float, 0.0 to 1.0) and a descriptive
    message string that can be formatted with the scan target.
    """
    IN_PROGRESS = (0.0, "Scanning {target}...")
    COMPLETE = (1.0, "Scan complete")
    FAILED = (1.0, "Scan failed unexpectedly")
    IDLE = (0.0, "Idle")


class ScanCancelledError(PortScannerError):
    """Exception raised when an Nmap scan operation is cancelled by the user
    or an external signal.
    Inherits from :class:`nmap.PortScannerError` for consistency.
    """
    pass


class NmapScanParameters(TypedDict, total=False):
    """Defines the structure for parameters used in an Nmap scan.

    This TypedDict allows for specifying various Nmap options. All keys are optional.

    :ivar target: The target specification for Nmap (e.g., IP, hostname, CIDR).
    :vartype target: str
    :ivar os_fingerprinting: If ``True``, enable OS detection options (-O, -A).
    :vartype os_fingerprinting: bool
    :ivar scan_all_ports: If ``True``, scan all 65535 ports (-p-).
    :vartype scan_all_ports: bool
    :ivar selected_script: Name of an Nmap script to run (e.g., "vuln").
    :vartype selected_script: Optional[str]
    :ivar service_version: If ``True``, enable service version detection (-sV).
    :vartype service_version: bool
    :ivar no_ping: If ``True``, skip host discovery, assume host is up (-Pn).
    :vartype no_ping: bool
    :ivar timing_template: Nmap timing template (e.g., "T0" through "T5").
    :vartype timing_template: str
    :ivar custom_dns_server: IP address of a custom DNS server for Nmap to use.
    :vartype custom_dns_server: Optional[str]
    """
    target: str
    os_fingerprinting: bool
    scan_all_ports: bool
    selected_script: Optional[str]
    service_version: bool
    no_ping: bool
    timing_template: str
    custom_dns_server: Optional[str]


def _is_scan_root_required(nmap_args_list: List[str]) -> bool:
    """Checks if the given Nmap arguments likely require root privileges.

    Identifies common Nmap options (e.g., -sS, -O, -A) that typically
    need elevated permissions to run correctly.

    :param nmap_args_list: A list of Nmap command arguments.
    :type nmap_args_list: List[str]
    :return: ``True`` if known root-requiring options are found, ``False`` otherwise.
    :rtype: bool
    """
    logger.debug("Checking if root is required for Nmap args: %s", nmap_args_list)
    # Common Nmap options that require root privileges.
    root_options = ["-sS", "-O", "-A"] # -sS (TCP SYN scan), -O (OS detection), -A (Aggressive)
    is_required = any(opt in nmap_args_list for opt in root_options)
    logger.debug("Root required for Nmap scan: %s", is_required)
    return is_required


def get_escalated_command(command_parts: List[str]) -> List[str]:
    """Constructs a command list for privilege escalation based on the OS.

    Prepends platform-specific escalation tools (`pkexec` on Linux, `osascript`
    on macOS) to the given Nmap command parts.

    :param command_parts: The Nmap command and its arguments.
    :type command_parts: List[str]
    :raises FileNotFoundError: If the Nmap executable or the required escalation
                               tool (pkexec, osascript) is not found in PATH.
    :raises NotImplementedError: If privilege escalation is attempted on an
                                 unsupported platform.
    :return: A new list of command parts, prefixed with escalation commands.
    :rtype: List[str]
    """
    system = platform.system()
    logger.debug("Preparing escalated command for: %s on system: %s",
                 command_parts, system)
    if not command_parts:
        return []

    nmap_executable = command_parts[0]
    nmap_path = shutil.which(nmap_executable)
    logger.debug("Nmap executable path resolved to: %s", nmap_path)

    if not nmap_path:
        raise FileNotFoundError(
            f"Nmap executable '{nmap_executable}' not found in PATH.")

    resolved_command_parts = [nmap_path] + command_parts[1:]
    escalated_cmd: List[str] = []

    if system == "Linux":
        if not shutil.which("pkexec"):
            logger.error("pkexec not found, which is required for privilege "
                         "escalation on Linux.")  # noqa: E501
            raise FileNotFoundError(
                "pkexec not found. Needed for privilege escalation on Linux.")
        escalated_cmd = ["pkexec"] + resolved_command_parts
    elif system == "Darwin": # macOS
        if not shutil.which("osascript"):
            logger.error("osascript not found, which is required for "
                         "privilege escalation on macOS.")  # noqa: E501
            raise FileNotFoundError(
                "osascript not found. Needed for privilege escalation on macOS.")
        # Format for osascript: do shell script "command" with administrator privileges
        quoted_command = " ".join(shlex.quote(part) for part in resolved_command_parts)
        osascript_command = (f'do shell script "{quoted_command}" with '
                             f'administrator privileges')
        escalated_cmd = ["osascript", "-e", osascript_command]
    else:
        logger.warning(
            "Privilege escalation is not configured for this platform: %s.", system)
        raise NotImplementedError(
            f"Privilege escalation not supported on this platform: {system}")

    logger.debug("Constructed escalated command: %s", escalated_cmd)
    return escalated_cmd


class NmapScanner:
    """Orchestrates Nmap scans by building commands, executing them, and parsing results.

    This class uses the `python-nmap` library indirectly by calling the `nmap`
    command-line tool via `subprocess`. It handles:
    - Construction of Nmap command arguments based on :class:`NmapScanParameters`.
    - Validation of target input.
    - Privilege escalation using `pkexec` (Linux) or `osascript` (macOS) if required
      by scan options (e.g., SYN scans, OS detection).
    - Execution of the Nmap scan as a subprocess, with support for cancellation
      via a :class:`Gio.Cancellable` object.
    - Parsing of Nmap's XML output into a :class:`nmap.PortScanner` object.
    - Conversion of scan results to a YAML format.
    - Management of subprocesses and a thread pool for scan execution.

    :ivar executor: A :class:`concurrent.futures.ThreadPoolExecutor` for running scans,
                    though current implementation runs `subprocess.Popen` directly
                    within a `Gio.Task`'s thread, so this executor might be for other tasks
                    or future refactoring.
    :vartype executor: ThreadPoolExecutor
    :ivar nm: An instance of :class:`nmap.PortScanner` used to analyze XML output.
    :vartype nm: Optional[nmap.PortScanner]
    :ivar current_process: The current :class:`subprocess.Popen` object for the running Nmap scan.
    :vartype current_process: Optional[subprocess.Popen[str]]
    :ivar current_cancellable: The :class:`Gio.Cancellable` object for the current scan.
    :vartype current_cancellable: Optional[Gio.Cancellable]
    """

    def __init__(self) -> None:
        """Initializes the NmapScanner.

        Sets up a thread pool executor (though not directly used by `run_nmap_scan`
        which uses `Gio.Task`'s threading) and initializes attributes for managing
        scan processes and results.

        :return: None
        :rtype: None
        """
        logger.debug("NmapScanner initialized.")
        # This executor is initialized but not directly used by run_nmap_scan's Popen logic.
        # It might be intended for other async tasks or future refactoring.
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.nm: Optional[nmap.PortScanner] = None
        self.current_process: Optional[subprocess.Popen[str]] = None
        self.current_cancellable: Optional[Gio.Cancellable] = None

    def __del__(self) -> None:
        """Cleans up resources when the NmapScanner instance is deleted.

        Ensures that the :class:`ThreadPoolExecutor` is properly shut down and
        attempts to terminate any active Nmap subprocess that might still be running.

        :return: None
        :rtype: None
        """
        logger.debug("NmapScanner.__del__ called for resource cleanup.")
        if self.current_process and self.current_process.poll() is None:
            logger.info("Terminating active Nmap process during NmapScanner deletion.")
            try:
                self.current_process.terminate()
                self.current_process.wait(timeout=1) # Wait briefly for graceful exit
            except subprocess.TimeoutExpired:
                logger.warning("Nmap process did not terminate gracefully, killing.")
                self.current_process.kill()
            except Exception as e: # pylint: disable=broad-except
                logger.exception("Error terminating Nmap process during deletion: %s", e)
            self.current_process = None

        if hasattr(self, 'executor') and self.executor:
            self.executor.shutdown(wait=True)
        logger.debug("NmapScanner cleanup complete.")

    def validate_target_input(self, target: str) -> bool:
        """Validates the Nmap target string.

        Checks if the input target string is a valid IP address (IPv4/IPv6),
        CIDR notation, a resolvable hostname (basic check, "localhost" allowed),
        or a comma/space-separated list thereof.

        :param target: The target string to validate.
        :type target: str
        :return: ``True`` if the target input is valid, ``False`` otherwise.
        :rtype: bool
        """
        if not target or not isinstance(target, str):
            return False
        # Split by comma or space, allowing for multiple targets
        targets = re.split(r"[ ,]+", target.strip())
        if not targets or all(not t for t in targets): # Ensure not empty after split
            return False

        # Regex for an IPv4 segment (0-255)
        ipv4_segment_regex = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9]|0)"
        # Full IPv4 address regex string
        ipv4_address_regex_str = r"{s}\.{s}\.{s}\.{s}".format(s=ipv4_segment_regex)
        # CIDR regex for IPv4 (e.g., 192.168.1.0/24)
        cidr_regex = re.compile(
            rf"^{ipv4_address_regex_str}/(?:[0-9]|[12][0-9]|3[0-2])$")

        logger.debug("Validating Nmap target input: '%s' (split into: %s)", target, targets)

        for t_segment in targets:
            if not t_segment: # Skip empty segments if multiple separators were used
                continue
            if t_segment.lower() == "localhost":
                logger.debug("Target segment '%s' is 'localhost'. Valid.", t_segment)
                continue
            if cidr_regex.fullmatch(t_segment):
                logger.debug("Target segment '%s' matched CIDR pattern. Valid.", t_segment)
                continue
            if is_valid_ip(t_segment): # Checks both IPv4 and IPv6
                logger.debug("Target segment '%s' is a valid IP. Valid.", t_segment)
                continue
            if is_valid_domain(t_segment): # Basic domain name syntax check
                logger.debug("Target segment '%s' is a valid domain. Valid.", t_segment)
                continue

            # If none of the above, the segment is invalid
            logger.warning("Target segment '%s' is invalid (not localhost, CIDR, IP, or domain).", t_segment)
            return False

        logger.debug("All target segments validated successfully for input '%s'.", target)
        return True

    def _build_nmap_arguments(self, params: NmapScanParameters) -> List[str]:
        """Builds the list of Nmap command-line arguments from scan parameters.

        Constructs a list of strings representing the Nmap command and its options
        based on the provided :class:`NmapScanParameters`. Includes default options
        like `-sS` (TCP SYN scan, requires root) and `-oX -` (XML output to stdout).

        :param params: A dictionary of scan parameters conforming to :class:`NmapScanParameters`.
        :type params: NmapScanParameters
        :return: A list of command-line arguments for Nmap.
        :rtype: List[str]
        """
        nmap_args_list = ["nmap", "-sS"]  # Default to TCP SYN scan

        if params.get("os_fingerprinting"):
            nmap_args_list.extend(["-O", "-A"]) # OS detection and Aggressive scan
        if params.get("service_version"):
            nmap_args_list.append("-sV") # Service version detection
        if params.get("scan_all_ports"):
            nmap_args_list.append("-p-") # Scan all 65535 ports
        if params.get("selected_script") and params["selected_script"] != "None":
            nmap_args_list.append(f"--script={params['selected_script']}")
        if params.get("no_ping"):
            nmap_args_list.append("-Pn") # Skip host discovery, assume all hosts are up

        # Timing template (T0-T5)
        timing_template = params.get("timing_template", "T3") # Default to T3 (Normal)
        if timing_template and re.match(r"^T[0-5]$", timing_template):
            nmap_args_list.append(f"-{timing_template}")
        else:
            nmap_args_list.append("-T3") # Fallback to T3 if invalid format

        # Custom DNS server
        custom_dns_server = params.get("custom_dns_server")
        if custom_dns_server and custom_dns_server.strip():
            nmap_args_list.append(f"--dns-servers={custom_dns_server.strip()}")
            logger.info("Using custom DNS server for Nmap scan: %s", custom_dns_server.strip())

        # XML output to stdout, and target specification last
        nmap_args_list.extend(["-oX", "-", params['target']])
        logger.debug("Built Nmap arguments: %s", nmap_args_list)
        return nmap_args_list

    def _prepare_final_nmap_command(
            self, nmap_args_list: List[str],
            needs_escalation: bool) -> List[str]:
        """Prepares the final Nmap command list, including executable path and escalation.

        Resolves the path to the Nmap executable using `shutil.which`. If privilege
        escalation is needed (determined by `needs_escalation`), it prepends the
        command with platform-specific escalation tools (e.g., `pkexec`, `osascript`)
        by calling :func:`get_escalated_command`.

        :param nmap_args_list: The list of Nmap arguments (e.g., from `_build_nmap_arguments`).
                               The first element should be "nmap".
        :type nmap_args_list: List[str]
        :param needs_escalation: Whether the command requires root/administrator privileges.
        :type needs_escalation: bool
        :raises FileNotFoundError: If Nmap executable or required escalation tool is not found.
        :raises PortScannerError: If privilege escalation fails to be prepared (e.g., on an
                                  unsupported platform as determined by `get_escalated_command`).
        :return: The final list of command parts to be executed by `subprocess.Popen`.
        :rtype: List[str]
        """
        if needs_escalation:
            logger.info("Privilege escalation required for Nmap scan execution.")
            # get_escalated_command handles finding nmap path and prepending sudo tool
            final_command_parts = get_escalated_command(nmap_args_list)
            if not final_command_parts: # Should be caught by exceptions in get_escalated_command
                raise PortScannerError("Failed to prepare escalated command (empty result).")
        else:
            nmap_executable_name = nmap_args_list[0]
            nmap_path = shutil.which(nmap_executable_name)
            if not nmap_path:
                raise FileNotFoundError(f"Nmap executable '{nmap_executable_name}' "
                                        "not found in PATH for non-escalated command.")
            final_command_parts = [nmap_path] + nmap_args_list[1:]
        return final_command_parts

    def _parse_nmap_error_message(
        self, returncode: int, nmap_xml_output: str, nmap_stderr: str,
        needs_escalation: bool,
    ) -> str:
        """Constructs a detailed error message from Nmap's output when a scan fails.

        Analyzes the return code, XML output (stdout), and stderr from a failed Nmap
        scan to create a more informative error message. This includes specific messages
        for common Nmap errors (like requiring root privileges) and for user
        cancellation of privilege escalation prompts.

        :param returncode: The exit code from the Nmap process.
        :type returncode: int
        :param nmap_xml_output: The XML output from Nmap (captured from stdout).
        :type nmap_xml_output: str
        :param nmap_stderr: The standard error output from Nmap.
        :type nmap_stderr: str
        :param needs_escalation: Whether privilege escalation was attempted for this scan.
        :type needs_escalation: bool
        :return: A formatted error message string summarizing the failure.
        :rtype: str
        """
        error_message = f"Nmap scan failed with exit code {returncode}."

        # Check for common Nmap error messages in XML output or stderr
        if "QUITTING" in nmap_xml_output or "privileges" in nmap_xml_output:
            # Extract the specific Nmap error line
            output_lines = nmap_xml_output.strip().split("\n")
            for line in output_lines:
                if "QUITTING" in line or "privileges" in line:
                    error_message = line.strip() # Use Nmap's own error message
                    break
        elif nmap_stderr.strip(): # If no specific Nmap message, use stderr
            error_message += f" Stderr: {nmap_stderr.strip()}"

        # Check for errors related to privilege escalation cancellation by the user
        if needs_escalation:
            sys_plat = platform.system()
            # If escalation was used and there's no output, it might mean user cancelled the prompt
            no_substantive_output = not nmap_xml_output.strip() and not nmap_stderr.strip()

            if sys_plat == "Darwin" and returncode == 1 and no_substantive_output:
                error_message = ("User cancelled the request for administrator "
                                 "privileges on macOS.")
            # For pkexec, cancellation often results in exit codes 1, 126, or 127
            # with no stdout/stderr from nmap itself.
            elif (sys_plat == "Linux" and # Line 282
                  returncode in [1, 126, 127] and # Line 283 (E127 reported here previously)
                  no_substantive_output):  # Line 284 - Auth failed or cancelled
                error_message = "User cancelled the request for administrator " + \
                                "privileges or authentication failed on Linux." # Line 290 (E501 previously)
        return error_message

    def run_nmap_scan(  # pylint: disable=too-many-locals
        self, params: NmapScanParameters,
        cancellable: Optional[Gio.Cancellable] = None,
    ) -> nmap.PortScanner:
        """Executes an Nmap scan with the given parameters and cancellation support.

        This method orchestrates the Nmap scan:
        1. Builds Nmap arguments from `params`.
        2. Determines if privilege escalation is needed via :func:`_is_scan_root_required`.
        3. Prepares the final command (with escalation if needed) via :func:`_prepare_final_nmap_command`.
        4. Runs Nmap as a subprocess, capturing stdout (XML output) and stderr.
        5. Monitors the `cancellable` object to terminate the scan if requested.
        6. Parses the XML output using :meth:`nmap.PortScanner.analyse_nmap_xml_scan`.
        7. Handles various errors, including :class:`FileNotFoundError` (Nmap/escalation tool missing),
           :class:`NotImplementedError` (unsupported platform for escalation),
           :class:`ScanCancelledError`, and :class:`nmap.PortScannerError` (for Nmap execution/parsing issues).

        :param params: The parameters for the Nmap scan, conforming to :class:`NmapScanParameters`.
        :type params: NmapScanParameters
        :param cancellable: An optional :class:`Gio.Cancellable` object to allow
                            the scan to be cancelled.
        :type cancellable: Optional[Gio.Cancellable]
        :raises PortScannerError: If Nmap execution fails, XML parsing fails,
                                  a prerequisite is not found, or an unexpected error occurs.
        :raises ScanCancelledError: If the scan is cancelled via the `cancellable`.
        :raises FileNotFoundError: If nmap or an escalation tool (pkexec, osascript) is not found.
        :raises NotImplementedError: If privilege escalation is attempted on an unsupported platform.
        :return: An :class:`nmap.PortScanner` object populated with the scan results.
        :rtype: nmap.PortScanner
        """
        target = params['target'] # For logging and error messages
        logger.debug("run_nmap_scan initiated for target: %s, Params: %s, Cancellable: %s",
                     target, params, bool(cancellable))

        self.nm = nmap.PortScanner() # Initialize for parsing results later
        self.current_cancellable = cancellable # Store for access during polling

        nmap_args_list = self._build_nmap_arguments(params)
        needs_escalation = _is_scan_root_required(nmap_args_list)
        final_command_parts = self._prepare_final_nmap_command(nmap_args_list, needs_escalation)

        # Log only a few parts of the command for security (e.g., avoid logging full target list if sensitive)
        logger.info("Executing Nmap command (first few parts): %s...",
                    " ".join(shlex.quote(part) for part in final_command_parts[:4]))

        stdout_str, stderr_str, returncode = "", "", -1
        try:
            if self.current_cancellable and self.current_cancellable.is_cancelled():
                raise ScanCancelledError(f"Nmap scan for {target} cancelled before process start.")

            # Start the Nmap process
            self.current_process = subprocess.Popen(
                final_command_parts,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True, # Decode stdout/stderr as UTF-8
                encoding="utf-8"
            )

            # Poll for process completion or cancellation
            while self.current_process.poll() is None:
                if self.current_cancellable and self.current_cancellable.is_cancelled():
                    logger.info("Cancellation requested for Nmap scan of target: %s. Terminating process.", target)
                    self.current_process.terminate()
                    try:
                        self.current_process.wait(timeout=1) # Wait for graceful termination
                    except subprocess.TimeoutExpired:
                        logger.warning("Nmap process for %s did not terminate gracefully, killing.", target)
                        self.current_process.kill()
                    self.current_process = None # Clear process reference
                    raise ScanCancelledError(f"Nmap scan for {target} was cancelled by user.")
                time.sleep(0.2)  # Polling interval to check for cancellation/completion

            # Process has completed (either normally or was terminated/killed by cancellation logic)
            if self.current_process: # If not already set to None by cancellation
                stdout_bytes, stderr_bytes = self.current_process.communicate()
                stdout_str = stdout_bytes # Already decoded due to text=True
                stderr_str = stderr_bytes
                returncode = self.current_process.returncode
                self.current_process = None # Clear process reference after communication

            # Check Nmap process return code after it has finished
            if returncode != 0:
                logger.debug("Nmap process for %s finished with error code %s. Stdout: '%s', Stderr: '%s'",
                             target, returncode, stdout_str[:200], stderr_str[:200])
                error_message = self._parse_nmap_error_message(
                    returncode, stdout_str, stderr_str, needs_escalation)
                logger.error("Nmap scan error for %s: %s", target, error_message)
                raise PortScannerError(error_message) # Use nmap's base error for consistency

            # Check if Nmap produced any XML output
            if not stdout_str.strip():
                logger.warning("Nmap scan for %s completed successfully (RC=0) but produced no XML output.", target)
                raise PortScannerError(f"Nmap scan for {target} succeeded but produced no XML output.")

            # Parse the XML output
            try:
                logger.debug("Attempting to parse Nmap XML output for %s (first 500 chars): %s",
                             target, stdout_str[:500])
                self.nm.analyse_nmap_xml_scan(nmap_xml_output=stdout_str)
            except PortScannerError as e_parse: # Raised by python-nmap on parsing failure
                logger.exception("Failed to parse Nmap XML output for %s:", target)
                logger.debug("Problematic Nmap XML Output for %s (full, on parse error):\n%s", target, stdout_str)
                raise PortScannerError(f"Failed to parse Nmap XML output for {target}: {e_parse}. "
                                       f"Stderr from Nmap was: '{stderr_str.strip()}'") from e_parse
            return self.nm
        # Specific exceptions handled first
        except FileNotFoundError as e_fnf:
            logger.exception("Nmap execution prerequisite not found for target %s:", target)
            raise PortScannerError(f"Nmap execution prerequisite not found: {e_fnf}") from e_fnf
        except NotImplementedError as e_ni:
            logger.exception("Privilege escalation not implemented for this platform for target %s:", target)
            raise PortScannerError(f"Privilege escalation not implemented for this platform: {e_ni}") from e_ni
        except ScanCancelledError: # Re-raise if already caught and processed
            raise
        except PortScannerError: # Re-raise if already a PortScannerError
            raise
        except Exception as e_unexpected:  # Catch-all for other unexpected errors
            logger.exception("An unexpected error occurred during the Nmap scan process for %s:", target)
            raise PortScannerError(f"An unexpected error occurred during Nmap scan for {target}: {e_unexpected}") from e_unexpected
        finally:
            # Ensure these are cleared on any exit path from the try block
            self.current_process = None
            self.current_cancellable = None

    def convert_results_to_yaml(self, nm_results: nmap.PortScanner) -> Dict[str, str]:
        """Converts Nmap scan results (:class:`nmap.PortScanner` object) to YAML format for each host.

        Iterates through all hosts found in the scan results, converts their data
        to a plain dictionary format, and then serializes this dictionary to a YAML string.
        Includes pre-scan script results at the host level if available.

        :param nm_results: The :class:`nmap.PortScanner` object containing results from
                           :meth:`run_nmap_scan` or :meth:`nmap.PortScanner.analyse_nmap_xml_scan`.
        :type nm_results: nmap.PortScanner
        :return: A dictionary where keys are host IP addresses (or names) and values
                 are YAML strings representing the scan data for that host.
        :rtype: Dict[str, str]
        """
        logger.debug("Converting Nmap results to YAML for %d hosts.", len(nm_results.all_hosts()))
        all_results_yaml: Dict[str, str] = {}
        # Get pre-scan script results if present (e.g., from --script=broadcast-*)
        prescan_scripts_data = nm_results.scaninfo().get('prescript', [])
        if prescan_scripts_data:
            logger.debug("Found pre-scan script data: %s", prescan_scripts_data)

        for host in nm_results.all_hosts():
            logger.debug("Processing Nmap results for host: %s to convert to YAML.", host)
            host_data_dict = nm_results[host] # This is a PortScannerHostDict
            # Convert complex nmap objects to plain dicts/lists for YAML serialization
            plain_host_dict = self.to_plain_dict(host_data_dict)

            # Add pre-scan script results to each host's data if available
            if prescan_scripts_data:
                plain_host_dict["prescript_results"] = prescan_scripts_data

            try:
                yaml_output = yaml.safe_dump(plain_host_dict, default_flow_style=False, sort_keys=False)
                all_results_yaml[host] = yaml_output
            except yaml.YAMLError as e_yaml:
                logger.error("Error serializing Nmap data to YAML for host %s: %s", host, e_yaml)
                all_results_yaml[host] = f"Error: Could not generate YAML output for this host.\nDetails: {e_yaml}"
        return all_results_yaml

    def to_plain_dict(self, data: Any) -> Union[Dict[str, Any], List[Any], Any]:
        """Recursively converts data structures containing `nmap.PortScannerHostDict`
        or similar custom objects into plain Python dictionaries and lists.

        This is primarily used to prepare Nmap scan results for serialization formats
        like YAML or JSON that may not handle custom objects directly.

        :param data: The data to convert. Can be a dictionary, list, or other type.
        :type data: Any
        :return: The data converted to plain Python dicts and lists. Primitives and
                 unrecognized types are returned as is.
        :rtype: Union[Dict[str, Any], List[Any], Any]
        """
        # Check for nmap's specific host dictionary type first
        if isinstance(data, nmap.PortScannerHostDict): # type: ignore # nmap.PortScannerHostDict may not be recognized by static type checkers
            return {key: self.to_plain_dict(value) for key, value in data.items()}
        elif isinstance(data, list):
            return [self.to_plain_dict(item) for item in data]
        elif isinstance(data, dict): # Handles general dictionaries
            return {key: self.to_plain_dict(value) for key, value in data.items()}
        # Return primitive types or unrecognized objects as is
        return data
