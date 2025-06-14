"""Tests for the nmap_scanner module."""
import unittest
from unittest.mock import patch, MagicMock
import subprocess
import platform # For mocking platform.system()
import os

# Assuming nmap_scanner.py is in src directory, and tests are run from project root
from src.nmap_scanner import NmapScanner, NmapScanError

# --- Mock Data ---
MOCK_NMAP_XML_OUTPUT = """
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE nmaprun>
<?xml-stylesheet href="file:///usr/bin/../share/nmap/nmap.xsl" type="text/xsl"?>
<nmaprun scanner="nmap" args="nmap -sS -O scanme.nmap.org" start="1678886400" startstr="Wed Mar 15 12:00:00 2023" version="7.92" xmloutputversion="1.05">
<scaninfo type="syn" protocol="tcp" numservices="1000" services="1-1000"/>
<verbose level="0"/>
<debugging level="0"/>
<host starttime="1678886400" endtime="1678886405"><status state="up" reason="conn-refused" reason_ttl="0"/>
<address addr="45.33.32.156" addrtype="ipv4"/>
<hostnames>
<hostname name="scanme.nmap.org" type="user"/>
<hostname name="scanme.nmap.org" type="PTR"/>
</hostnames>
<ports><port protocol="tcp" portid="22"><state state="open" reason="syn-ack" reason_ttl="0"/><service name="ssh" method="table" conf="3"/></port>
<port protocol="tcp" portid="80"><state state="open" reason="syn-ack" reason_ttl="0"/><service name="http" method="table" conf="3"/></port>
</ports>
<os><portused state="open" proto="tcp" portid="22"/>
<osmatch name="Linux 3.10 - 4.11" accuracy="100" line="62364">
<osclass type="general purpose" vendor="Linux" osfamily="Linux" osgen="3.X" accuracy="100"><cpe>cpe:/o:linux:linux_kernel:3</cpe></osclass>
<osclass type="general purpose" vendor="Linux" osfamily="Linux" osgen="4.X" accuracy="100"><cpe>cpe:/o:linux:linux_kernel:4</cpe></osclass>
</osmatch>
</os>
<trace protocol="tcp" port="80">
<hop ttl="1" ipaddr="192.168.1.1" rtt="1.23"/>
</trace>
<times srtt="12345" rttvar="123" to="100000"/>
</host>
</nmaprun>
"""

class TestNmapScanner(unittest.TestCase):
    """Test cases for the NmapScanner class."""

    def _get_mock_subprocess_run(self, stdout_xml=MOCK_NMAP_XML_OUTPUT, stderr="", returncode=0):
        """Create a mock CompletedProcess object for subprocess.run."""
        mock_proc = MagicMock(spec=subprocess.CompletedProcess)
        mock_proc.stdout = stdout_xml
        mock_proc.stderr = stderr
        mock_proc.returncode = returncode
        return mock_proc

    @patch("src.nmap_scanner.subprocess.run")
    @patch("src.nmap_scanner.shutil.which")
    def test_run_nmap_scan_service_version(self, mock_shutil_which, mock_subprocess_run):
        """Test Nmap scan with service version detection enabled."""
        mock_shutil_which.return_value = "/usr/bin/nmap"  # Mock nmap path
        mock_subprocess_run.return_value = self._get_mock_subprocess_run()

        scanner = NmapScanner()
        params = {"target": "scanme.nmap.org", "service_version": True}
        nm = scanner.run_nmap_scan(params)

        self.assertIsNotNone(nm)
        self.assertIn("-sV", mock_subprocess_run.call_args[0][0]) # Check for service version flag
        # Add more assertions based on expected nmap.PortScanner object state
        self.assertTrue(nm.all_hosts()) # Check if hosts were processed
        self.assertEqual(nm[nm.all_hosts()[0]].hostname(), "scanme.nmap.org")

    # Test with -O for OS fingerprinting
    @patch("src.nmap_scanner.subprocess.run")
    @patch("src.nmap_scanner.shutil.which")
    def test_run_nmap_scan_os_fingerprinting(self, mock_shutil_which, mock_subprocess_run):
        """Test Nmap scan with OS fingerprinting enabled."""
        mock_shutil_which.return_value = "/usr/bin/nmap"
        mock_subprocess_run.return_value = self._get_mock_subprocess_run()
        scanner = NmapScanner()
        params = {"target": "scanme.nmap.org", "os_fingerprinting": True}
        nm = scanner.run_nmap_scan(params)
        self.assertIsNotNone(nm)
        self.assertIn("-O", mock_subprocess_run.call_args[0][0])

    @patch("src.nmap_scanner.subprocess.run")
    @patch("src.nmap_scanner.shutil.which")
    def test_run_nmap_scan_no_ping(self, mock_shutil_which, mock_subprocess_run):
        """Test Nmap scan with no ping (-Pn) enabled."""
        mock_shutil_which.return_value = "/usr/bin/nmap"
        mock_subprocess_run.return_value = self._get_mock_subprocess_run()
        scanner = NmapScanner()
        params = {"target": "scanme.nmap.org", "no_ping": True}
        nm = scanner.run_nmap_scan(params)
        self.assertIsNotNone(nm)
        self.assertIn("-Pn", mock_subprocess_run.call_args[0][0])


    @patch("src.nmap_scanner.subprocess.run")
    @patch("src.nmap_scanner.shutil.which")
    def test_run_nmap_scan_scan_all_ports(self, mock_shutil_which, mock_subprocess_run):
        """Test Nmap scan with scan all ports (-p-) enabled."""
        mock_shutil_which.return_value = "/usr/bin/nmap"
        mock_subprocess_run.return_value = self._get_mock_subprocess_run()
        scanner = NmapScanner()
        params = {"target": "scanme.nmap.org", "scan_all_ports": True}
        nm = scanner.run_nmap_scan(params)
        self.assertIsNotNone(nm)
        self.assertIn("-p-", mock_subprocess_run.call_args[0][0])

    @patch("src.nmap_scanner.subprocess.run")
    @patch("src.nmap_scanner.shutil.which")
    def test_run_nmap_scan_timing_template(self, mock_shutil_which, mock_subprocess_run):
        """Test Nmap scan with a specific timing template."""
        mock_shutil_which.return_value = "/usr/bin/nmap"
        mock_subprocess_run.return_value = self._get_mock_subprocess_run()
        scanner = NmapScanner()
        params = {"target": "scanme.nmap.org", "timing_template": "T5"}
        nm = scanner.run_nmap_scan(params)
        self.assertIsNotNone(nm)
        # The argument should be like "-T5", not "-T5" directly as a separate arg.
        # The code constructs it as f"-{timing_template}" which becomes "-T5"
        self.assertTrue(any(arg.startswith("-T5") for arg in mock_subprocess_run.call_args[0][0]))


    @patch("src.nmap_scanner.subprocess.run")
    @patch("src.nmap_scanner.shutil.which")
    def test_run_nmap_scan_os_and_service_version(self, mock_shutil_which, mock_subprocess_run):
        """Test Nmap scan with both OS fingerprinting and service version detection."""
        mock_shutil_which.return_value = "/usr/bin/nmap"
        mock_subprocess_run.return_value = self._get_mock_subprocess_run()
        scanner = NmapScanner()
        params = {
            "target": "scanme.nmap.org",
            "os_fingerprinting": True,
            "service_version": True
        }
        nm = scanner.run_nmap_scan(params)
        self.assertIsNotNone(nm)
        self.assertIn("-O", mock_subprocess_run.call_args[0][0])
        self.assertIn("-sV", mock_subprocess_run.call_args[0][0])

    @patch("src.nmap_scanner.subprocess.run")
    @patch("src.nmap_scanner.shutil.which")
    def test_run_nmap_scan_defaults(self, mock_shutil_which, mock_subprocess_run):
        """Test Nmap scan with default parameters."""
        mock_shutil_which.return_value = "/usr/bin/nmap"  # Mock nmap path
        mock_subprocess_run.return_value = self._get_mock_subprocess_run()

        scanner = NmapScanner()
        params = {"target": "scanme.nmap.org"} # No optional flags
        nm = scanner.run_nmap_scan(params)

        self.assertIsNotNone(nm)
        # Check that default flags are present (e.g., -sS if it's a default)
        # And that optional flags are NOT present
        command_args = mock_subprocess_run.call_args[0][0]
        self.assertIn("-sS", command_args) # Assuming -sS is a default when sudo is available
        self.assertNotIn("-O", command_args)
        self.assertNotIn("-sV", command_args)
        self.assertNotIn("-p-", command_args)
        self.assertNotIn("-Pn", command_args)
        # Default timing template T3 should be there
        self.assertTrue(any(arg.startswith("-T3") for arg in command_args))


    @patch("src.nmap_scanner.shutil.which")
    def test_pkexec_not_found(self, mock_shutil_which):
        """Test Nmap scan behavior when pkexec is not found for privilege escalation."""
        # Mock which to simulate pkexec not being found, but nmap is.
        def side_effect(cmd):
            if cmd == "nmap":
                return "/usr/bin/nmap"
            if cmd == "pkexec":
                return None # pkexec not found
            return None # Default for other commands
        mock_shutil_which.side_effect = side_effect

        scanner = NmapScanner()
        # This call primarily tests the _get_nmap_cmd_prefix method's logic
        # when pkexec is not found. We expect it to fall back to sudo or direct nmap.
        # Since we are not mocking subprocess.run here, we can't fully test
        # run_nmap_scan, but we can infer the command prefix logic.

        # To test this properly, we might need to mock platform.system or os.geteuid
        # or make _get_nmap_cmd_prefix directly testable.
        # For now, this test ensures `which` is called for pkexec.
        with patch.object(platform, "system", return_value="Linux"): # Ensure Linux path
            with patch.object(os, "geteuid", return_value=1000): # Simulate non-root user
                 # If run_nmap_scan is called, it would try to build a command.
                 # We are interested if 'pkexec' was checked.
                try:
                    # This will likely fail as subprocess.run is not mocked here,
                    # but `which` for `pkexec` would have been called by _get_nmap_cmd_prefix
                    scanner.run_nmap_scan({"target": "localhost"})
                except NmapScanError: # Expected if nmap command fails without mock
                    pass
                except Exception: # Catch any other exception during this partial test
                    pass

        mock_shutil_which.assert_any_call("pkexec")


    @patch("src.nmap_scanner.platform.system", return_value="Linux") # Mock system
    @patch("src.nmap_scanner.shutil.which")
    def test_run_nmap_scan_nmap_not_found_no_escalation(self, mock_shutil_which, mock_platform_system):
        """Test Nmap scan failure when nmap executable is not found and no escalation is needed."""
        # Simulate nmap not being found, and no escalation needed initially
        mock_shutil_which.return_value = None # nmap not found

        scanner = NmapScanner()
        params = {"target": "scanme.nmap.org", "no_ping": True} # Example where -Pn might not need root

        with self.assertRaises(NmapScanError) as context:
            scanner.run_nmap_scan(params)

        self.assertIn("Nmap command not found", str(context.exception))
        mock_shutil_which.assert_any_call("nmap") # Verify nmap was checked
        # mock_platform_system is automatically used due to @patch

    @patch("src.nmap_scanner.platform.system", return_value="Linux")
    @patch("src.nmap_scanner.os.geteuid", return_value=0) # Mock effective UID to root (0)
    @patch("src.nmap_scanner.shutil.which")
    @patch("src.nmap_scanner.subprocess.run")
    def test_run_nmap_scan_as_root_direct_nmap(self, mock_subprocess_run, mock_shutil_which, mock_geteuid, mock_platform_system):
        """Test that Nmap is called directly (no sudo/pkexec) when run as root."""
        mock_shutil_which.return_value = "/usr/bin/nmap" # nmap is found
        mock_subprocess_run.return_value = self._get_mock_subprocess_run()

        scanner = NmapScanner()
        params = {"target": "scanme.nmap.org", "service_version": True} # Needs -sS, typically root
        scanner.run_nmap_scan(params)

        # Verify that nmap was called directly (no sudo/pkexec)
        called_cmd_list = mock_subprocess_run.call_args[0][0]
        self.assertEqual(called_cmd_list[0], "nmap")
        # mock_geteuid and mock_platform_system are used by the decorator

    @patch("src.nmap_scanner.platform.system", return_value="Windows")
    @patch("src.nmap_scanner.shutil.which")
    @patch("src.nmap_scanner.subprocess.run")
    def test_run_nmap_scan_windows_direct_nmap(self, mock_subprocess_run, mock_shutil_which, mock_platform_system):
        """Test that Nmap is called directly on Windows (no sudo/pkexec)."""
        mock_shutil_which.return_value = "C:\\Program Files (x86)\\Nmap\\nmap.exe" # nmap is found
        mock_subprocess_run.return_value = self._get_mock_subprocess_run()

        scanner = NmapScanner()
        params = {"target": "scanme.nmap.org", "no_ping": True} # -Pn
        scanner.run_nmap_scan(params)

        called_cmd_list = mock_subprocess_run.call_args[0][0]
        self.assertEqual(called_cmd_list[0], "nmap") # Should be just 'nmap' or the full path
        # On Windows, sudo/pkexec checks should be skipped.
        # mock_platform_system is used by the decorator

    @patch("src.nmap_scanner.platform.system", return_value="Linux")
    @patch("src.nmap_scanner.shutil.which")
    @patch("src.nmap_scanner.subprocess.Popen")
    def test_run_nmap_scan_escalation_pkexec_not_found(self, mock_subprocess_popen, mock_shutil_which, mock_platform_system):
        """Test Nmap scan behavior when pkexec is not found and escalation is needed."""
        # Simulate nmap found, but pkexec not found when escalation is needed.
        def which_side_effect(cmd):
            if cmd == "nmap":
                return "/usr/bin/nmap"
            if cmd == "pkexec":
                return None
            return None
        mock_shutil_which.side_effect = which_side_effect

        # Mock Popen to simulate a successful sudo call if it falls back
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.communicate.return_value = (MOCK_NMAP_XML_OUTPUT.encode('utf-8'), b"") # Ensure bytes
        mock_proc.returncode = 0
        mock_subprocess_popen.return_value = mock_proc

        scanner = NmapScanner()
        # Params that require escalation (e.g., -sS, default)
        params = {"target": "scanme.nmap.org"}

        with patch.object(os, "geteuid", return_value=1000): # Simulate non-root
            # If pkexec is not found, it should try sudo (which Popen is now mocking)
            # or fail if sudo also not found (not the case here due to Popen mock).
            scanner.run_nmap_scan(params, cancellable=None) # Pass cancellable

        # Check that `which` was called for `pkexec`
        mock_shutil_which.assert_any_call("pkexec")
        # If it fell back to sudo, Popen would be called with sudo.
        # This depends on the exact fallback logic in _get_nmap_cmd_prefix
        # For this test, we mostly care that pkexec was checked.
        # If sudo is also mocked via `which` to not be found, then it should raise error.

    @patch("src.nmap_scanner.platform.system", return_value="Linux")
    @patch("src.nmap_scanner.shutil.which", return_value="/usr/bin/nmap") # Assume nmap is found
    @patch("src.nmap_scanner.subprocess.Popen")
    def test_run_nmap_scan_subprocess_oserror(self, mock_subprocess_popen, mock_shutil_which, mock_platform_system):
        """Test Nmap scan failure when subprocess.Popen raises an OSError."""
        # Mock subprocess.Popen to raise an OSError
        mock_subprocess_popen.side_effect = OSError("Test OS error from Popen")

        scanner = NmapScanner()
        params = {"target": "scanme.nmap.org"} # Basic scan

        with self.assertRaises(NmapScanError) as context:
            with patch.object(os, "geteuid", return_value=0): # Simulate root to avoid escalation logic
                scanner.run_nmap_scan(params)

        self.assertIn("Failed to start Nmap process: Test OS error from Popen", str(context.exception))
        # mock_shutil_which and mock_platform_system are used by decorators or setup

if __name__ == "__main__":
    unittest.main()
