import unittest
from unittest.mock import patch, MagicMock
import subprocess
from src.nmap_scanner import NmapScanner
import nmap  # For nmap.PortScannerError

# Minimal valid Nmap XML for a host up, no open ports
MOCK_NMAP_XML_OUTPUT = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE nmaprun>
<?xml-stylesheet href="file:///usr/bin/../share/nmap/nmap.xsl" type="text/xsl"?>
<nmaprun scanner="nmap" args="nmap -oX - 127.0.0.1" start="1678886400" startstr="Mon Mar 15 12:00:00 2023" version="7.92" xmloutputversion="1.05">
<scaninfo type="connect" protocol="tcp" numservices="1000" services="1-1000"/>
<verbose level="0"/>
<debugging level="0"/>
<host starttime="1678886400" endtime="1678886400"><status state="up" reason="localhost-response" reason_ttl="0"/>
<address addr="127.0.0.1" addrtype="ipv4"/>
<hostnames/>
<ports/>
<times srtt="100" rttvar="100" to="100000"/>
</host>
<runstats><finished time="1678886400" timestr="Mon Mar 15 12:00:00 2023" elapsed="0.01" summary="Nmap done at Mon Mar 15 12:00:00 2023; 1 IP address (1 host up) scanned in 0.01 seconds" exit="success"/><hosts up="1" down="0" total="1"/>
</runstats>
</nmaprun>
"""


class TestNmapScanner(unittest.TestCase):
    def _get_mock_subprocess_run(self, stdout_xml=MOCK_NMAP_XML_OUTPUT, stderr="", returncode=0):
        mock_proc = MagicMock(spec=subprocess.CompletedProcess)
        mock_proc.stdout = stdout_xml
        mock_proc.stderr = stderr
        mock_proc.returncode = returncode
        return mock_proc

    @patch("src.nmap_scanner.subprocess.run")
    @patch("src.nmap_scanner.shutil.which")
    def test_run_nmap_scan_service_version(self, mock_shutil_which, mock_subprocess_run):
        mock_shutil_which.return_value = "/usr/bin/nmap"  # Mock nmap path
        mock_subprocess_run.return_value = self._get_mock_subprocess_run()

        scanner = NmapScanner()
        # The actual Nmap execution is mocked, so we are testing argument construction
        # and if the parsing of mock XML works.
        nm = scanner.run_nmap_scan(
            target="127.0.0.1",
            os_fingerprinting=False,
            scan_all_ports=False,
            selected_script=None,
            service_version=True,
            no_ping=False,
            timing_template="T3",
        )

        # Check that subprocess.run was called
        mock_subprocess_run.assert_called_once()
        # Get the actual command list passed to subprocess.run
        actual_command_list = mock_subprocess_run.call_args[0][0]

        self.assertIn("-sV", actual_command_list)
        self.assertNotIn("-O", actual_command_list)
        self.assertIsNotNone(nm)  # Check if parsing the mock XML produced a result

    @patch("src.nmap_scanner.subprocess.run")
    @patch("src.nmap_scanner.shutil.which")
    def test_run_nmap_scan_no_ping(self, mock_shutil_which, mock_subprocess_run):
        mock_shutil_which.return_value = "/usr/bin/nmap"
        mock_subprocess_run.return_value = self._get_mock_subprocess_run()

        scanner = NmapScanner()
        scanner.run_nmap_scan(
            target="127.0.0.1",
            os_fingerprinting=False,
            scan_all_ports=False,
            selected_script=None,
            service_version=False,
            no_ping=True,
            timing_template="T3",
        )

        actual_command_list = mock_subprocess_run.call_args[0][0]
        self.assertIn("-Pn", actual_command_list)

    @patch("src.nmap_scanner.subprocess.run")
    @patch("src.nmap_scanner.shutil.which")
    def test_run_nmap_scan_timing_template(self, mock_shutil_which, mock_subprocess_run):
        mock_shutil_which.return_value = "/usr/bin/nmap"
        mock_subprocess_run.return_value = self._get_mock_subprocess_run()

        scanner = NmapScanner()
        scanner.run_nmap_scan(
            target="127.0.0.1",
            os_fingerprinting=False,
            scan_all_ports=False,
            selected_script=None,
            service_version=False,
            no_ping=False,
            timing_template="T4",
        )

        actual_command_list = mock_subprocess_run.call_args[0][0]
        self.assertIn("-T4", actual_command_list)

    @patch("src.nmap_scanner.subprocess.run")
    @patch("src.nmap_scanner.shutil.which")
    def test_run_nmap_scan_os_and_service_version(self, mock_shutil_which, mock_subprocess_run):
        mock_shutil_which.return_value = "/usr/bin/nmap"
        mock_subprocess_run.return_value = self._get_mock_subprocess_run()

        scanner = NmapScanner()
        scanner.run_nmap_scan(
            target="127.0.0.1",
            os_fingerprinting=True,
            scan_all_ports=False,
            selected_script=None,
            service_version=True,
            no_ping=False,
            timing_template="T3",
        )

        actual_command_list = mock_subprocess_run.call_args[0][0]
        self.assertIn("-O", actual_command_list)
        self.assertIn("-sV", actual_command_list)

    @patch("src.nmap_scanner.subprocess.run")
    @patch("src.nmap_scanner.shutil.which")
    def test_run_nmap_scan_defaults(self, mock_shutil_which, mock_subprocess_run):
        mock_shutil_which.return_value = "/usr/bin/nmap"  # Mock nmap path
        mock_subprocess_run.return_value = self._get_mock_subprocess_run()

        scanner = NmapScanner()
        scanner.run_nmap_scan(
            target="127.0.0.1",
            os_fingerprinting=False,
            scan_all_ports=False,
            selected_script=None,
            service_version=False,
            no_ping=False,
            timing_template="T3",
        )

        actual_command_list = mock_subprocess_run.call_args[0][0]
        self.assertIn("-sS", actual_command_list)  # Default scan type
        self.assertIn("-T3", actual_command_list)
        self.assertNotIn("-O", actual_command_list)
        self.assertNotIn("-sV", actual_command_list)
        self.assertNotIn("-p-", actual_command_list)
        self.assertNotIn("-Pn", actual_command_list)
        self.assertNotIn("--script", "".join(actual_command_list))

    @patch("src.nmap_scanner.shutil.which")
    def test_pkexec_not_found(self, mock_shutil_which):
        # Mock which to simulate pkexec not being found, but nmap is.
        def side_effect(cmd):
            if cmd == "nmap":
                return "/usr/bin/nmap"
            if cmd == "pkexec":
                return None
            return None

        mock_shutil_which.side_effect = side_effect

        scanner = NmapScanner()
        with self.assertRaises(nmap.nmap.PortScannerError) as cm:
            scanner.run_nmap_scan(
                target="127.0.0.1",
                os_fingerprinting=True,  # -O requires root
                scan_all_ports=False,  # Provide missing argument
                selected_script=None,
                service_version=False,
                no_ping=False,
                timing_template="T3",
            )

        self.assertIn("pkexec not found", str(cm.exception))
        self.assertIn("Needed for privilege escalation", str(cm.exception)) # More specific check

    @patch("src.nmap_scanner.platform.system", return_value="Linux") # Mock system
    @patch("src.nmap_scanner.shutil.which")
    def test_run_nmap_scan_nmap_not_found_no_escalation(self, mock_shutil_which, mock_platform_system):
        # Simulate nmap not being found, and no escalation needed initially
        mock_shutil_which.return_value = None # nmap not found

        scanner = NmapScanner()
        # Params that do not require escalation by default (e.g. no -O, -sS is added by default but _is_scan_root_required checks it)
        # To ensure no escalation, we can mock _is_scan_root_required or ensure params don't trigger it.
        # For this test, let's assume default -sS which would need escalation.
        # So, we need to test the case where nmap itself is not found for a non-escalated command.
        # This means we need to ensure _is_scan_root_required returns False.

        # Let's refine _build_nmap_arguments to not include -sS for this specific test,
        # or mock _is_scan_root_required. Mocking is cleaner.
        with patch("src.nmap_scanner._is_scan_root_required", return_value=False):
            with self.assertRaises(nmap.PortScannerError) as cm:
                scanner.run_nmap_scan(
                    target="127.0.0.1",
                    os_fingerprinting=False, # Does not require root
                    # service_version=False, # -sV does not require root
                    # no_ping=True # -Pn does not require root
                    # Ensure no other options requiring root are passed.
                    # The default _build_nmap_arguments adds -sS.
                    # To avoid -sS, we'd have to modify the core logic for the test,
                    # or accept that this test path (nmap not found *without* escalation)
                    # is hard to hit if -sS is always added by default.
                    # Alternative: patch _build_nmap_arguments to return a command list without -sS
                    custom_params = {
                        "target": "127.0.0.1",
                        "os_fingerprinting": False,
                        "service_version": False,
                        "scan_all_ports": False,
                        "selected_script": None,
                        "no_ping": True,
                        "timing_template": "T3",
                    }
                )
            # This assertion is tricky because _build_nmap_arguments adds -sS by default,
            # which makes _is_scan_root_required return True.
            # Let's assume the test for "pkexec not found" covers the escalation path's FileNotFoundError.
            # This test should focus on nmap not found when shutil.which(nmap_executable) fails in the *else* branch of _prepare_final_nmap_command
            # So, we need _is_scan_root_required to be false.

            # Re-evaluating: The critical part is shutil.which(nmap_args_list[0]) failing.
            # If -sS is *not* in args, then it's non-escalated.
            # The scanner._build_nmap_arguments by default adds "-sS".
            # Let's patch _build_nmap_arguments to control the command.

            with patch.object(NmapScanner, '_build_nmap_arguments', return_value=["nmap", "127.0.0.1"]): # No -sS, no -O
                 with self.assertRaises(nmap.PortScannerError) as cm:
                    scanner.run_nmap_scan(target="127.0.0.1")
                 self.assertIn("Nmap executable 'nmap' not found", str(cm.exception))

    @patch("src.nmap_scanner.platform.system", return_value="Linux")
    @patch("src.nmap_scanner.shutil.which")
    @patch("src.nmap_scanner.subprocess.Popen")
    def test_run_nmap_scan_escalation_pkexec_not_found(self, mock_subprocess_popen, mock_shutil_which, mock_platform_system):
        # Simulate nmap found, but pkexec not found when escalation is needed.
        def which_side_effect(cmd):
            if cmd == "nmap": return "/usr/bin/nmap"
            if cmd == "pkexec": return None
            return None
        mock_shutil_which.side_effect = which_side_effect

        # Mock Popen to prevent actual execution
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0 # Indicate process finished
        mock_proc.communicate.return_value = (MOCK_NMAP_XML_OUTPUT.encode("utf-8"), b"")
        mock_proc.returncode = 0
        mock_subprocess_popen.return_value = mock_proc

        scanner = NmapScanner()
        # Use parameters that require escalation (e.g., -O or the default -sS)
        params = {"target": "127.0.0.1", "os_fingerprinting": True} # -O needs root

        with self.assertRaises(nmap.PortScannerError) as cm:
            scanner.run_nmap_scan(params)

        self.assertIn("pkexec not found", str(cm.exception))
        self.assertIn("Needed for privilege escalation", str(cm.exception))

    @patch("src.nmap_scanner.platform.system", return_value="Linux")
    @patch("src.nmap_scanner.shutil.which", return_value="/usr/bin/nmap") # Assume nmap is found
    @patch("src.nmap_scanner.subprocess.Popen")
    def test_run_nmap_scan_subprocess_oserror(self, mock_subprocess_popen, mock_shutil_which, mock_platform_system):
        # Mock subprocess.Popen to raise an OSError
        mock_subprocess_popen.side_effect = OSError("Test OS error from Popen")

        scanner = NmapScanner()
        params = {"target": "127.0.0.1"} # Basic params

        with self.assertRaises(nmap.PortScannerError) as cm:
            scanner.run_nmap_scan(params)

        self.assertIn("OS error occurred", str(cm.exception))
        self.assertIn("Test OS error from Popen", str(cm.exception))

    # Comment for testing TypeError/ValueError from subprocess.Popen:
    #
    # To test scenarios where `subprocess.Popen` might raise a `TypeError` or `ValueError`
    # due to malformed `final_command_parts`, we would need to mock internal components
    # of `NmapScanner` more deeply, specifically the `_build_nmap_arguments` or
    # `_prepare_final_nmap_command` methods.
    #
    # Scenario:
    # If `_prepare_final_nmap_command` were to produce `final_command_parts` that is not a
    # list of strings (e.g., contains `None` or integers, or is `None` itself),
    # `subprocess.Popen(final_command_parts, ...)` could raise:
    #   - `TypeError`: If `final_command_parts` is not a sequence or if elements are not suitable.
    #   - `ValueError`: If `final_command_parts` is empty (less likely here as "nmap" is usually first).
    #
    # Example (conceptual, not runnable without modifying NmapScanner internals for the test):
    #
    # @patch("src.nmap_scanner.NmapScanner._prepare_final_nmap_command")
    # @patch("src.nmap_scanner.shutil.which", return_value="/usr/bin/nmap")
    # def test_run_nmap_scan_bad_command_type_error(self, mock_shutil_which, mock_prepare_command):
    #     # Simulate _prepare_final_nmap_command returning malformed command parts
    #     mock_prepare_command.return_value = [None, "127.0.0.1"] # None instead of "nmap" executable
    #
    #     scanner = NmapScanner()
    #     params = {"target": "127.0.0.1"}
    #
    #     with self.assertRaises(nmap.PortScannerError) as cm:
    #         scanner.run_nmap_scan(params)
    #
    #     # Check that the specific TypeError from Popen (if that's what it raises for [None,...])
    #     # is wrapped in PortScannerError, or that a ValueError for bad args is caught.
    #     self.assertTrue("Type error encountered" in str(cm.exception) or \
    #                     "Value error encountered" in str(cm.exception))
    #
    # This type of test is highly dependent on the internal implementation details of
    # argument construction and may be brittle. The existing specific exception handlers
    # for `TypeError` and `ValueError` in `run_nmap_scan` aim to catch such issues if they
    # were to occur during the Popen call.


if __name__ == "__main__":
    unittest.main()
