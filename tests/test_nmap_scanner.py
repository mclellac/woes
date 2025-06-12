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


if __name__ == "__main__":
    unittest.main()
