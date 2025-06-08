import unittest
from unittest.mock import patch, MagicMock
from src.nmap_scanner import NmapScanner


class TestNmapScanner(unittest.TestCase):

    @patch('nmap.PortScanner')
    def test_run_nmap_scan_service_version(self, mock_port_scanner_class):
        mock_nm_instance = MagicMock()
        mock_port_scanner_class.return_value = mock_nm_instance

        scanner = NmapScanner()
        scanner.run_nmap_scan(
            target="127.0.0.1",
            os_fingerprinting=False,
            scan_all_ports=False,
            selected_script=None,
            service_version=True,
            no_ping=False,
            timing_template="T3"
            )

        called_arguments = mock_nm_instance.scan.call_args[1]['arguments']
        self.assertIn("-sV", called_arguments)
        self.assertNotIn("-O", called_arguments)

    @patch('nmap.PortScanner')
    def test_run_nmap_scan_no_ping(self, mock_port_scanner_class):
        mock_nm_instance = MagicMock()
        mock_port_scanner_class.return_value = mock_nm_instance

        scanner = NmapScanner()
        scanner.run_nmap_scan(
            target="127.0.0.1",
            os_fingerprinting=False,
            scan_all_ports=False,
            selected_script=None,
            service_version=False,
            no_ping=True,
            timing_template="T3"
            )

        called_arguments = mock_nm_instance.scan.call_args[1]['arguments']
        self.assertIn("-Pn", called_arguments)

    @patch('nmap.PortScanner')
    def test_run_nmap_scan_timing_template(self, mock_port_scanner_class):
        mock_nm_instance = MagicMock()
        mock_port_scanner_class.return_value = mock_nm_instance

        scanner = NmapScanner()
        scanner.run_nmap_scan(
            target="127.0.0.1",
            os_fingerprinting=False,
            scan_all_ports=False,
            selected_script=None,
            service_version=False,
            no_ping=False,
            timing_template="T4"
            )

        called_arguments = mock_nm_instance.scan.call_args[1]['arguments']
        self.assertIn("-T4", called_arguments)

    @patch('nmap.PortScanner')
    def test_run_nmap_scan_os_and_service_version(self, mock_port_scanner_class):
        mock_nm_instance = MagicMock()
        mock_port_scanner_class.return_value = mock_nm_instance

        scanner = NmapScanner()
        scanner.run_nmap_scan(
            target="127.0.0.1",
            os_fingerprint=True,
            scan_all_ports=False,
            selected_script=None,
            service_version=True,
            no_ping=False,
            timing_template="T3"
            )

        called_arguments = mock_nm_instance.scan.call_args[1]['arguments']
        self.assertIn("-O", called_arguments)
        self.assertIn("-sV", called_arguments)

    @patch('nmap.PortScanner')
    def test_run_nmap_scan_defaults(self, mock_port_scanner_class):
        mock_nm_instance = MagicMock()
        mock_port_scanner_class.return_value = mock_nm_instance

        scanner = NmapScanner()
        scanner.run_nmap_scan(
            target="127.0.0.1",
            os_fingerprinting=False,
            scan_all_ports=False,
            selected_script=None,
            service_version=False,
            no_ping=False,
            timing_template="T3"
            )
        called_arguments = mock_nm_instance.scan.call_args[1]['arguments']
        self.assertIn("-sS", called_arguments)
        self.assertIn("-T3", called_arguments)
        self.assertNotIn("-O", called_arguments)
        self.assertNotIn("-sV", called_arguments)
        self.assertNotIn("-p-", called_arguments)
        self.assertNotIn("-Pn", called_arguments)
        self.assertNotIn("--script", called_arguments)


if __name__ == '__main__':
    unittest.main()
