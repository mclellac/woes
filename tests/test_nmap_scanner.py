import unittest
from unittest.mock import patch

from src.nmap_scanner import NmapScanner

class TestNmapScanner(unittest.TestCase):

    def setUp(self):
        self.scanner = NmapScanner()

    def test_validate_target_input_valid(self):
        """Test validate_target_input with valid inputs."""
        valid_targets = [
            'localhost',
            '192.168.1.1',
            'example.com',
            '10.0.0.0/24',
            '192.168.1.1,192.168.1.2',
            '2001:0db8:85a3:0000:0000:8a2e:0370:7334', # IPv6 without brackets
            'domain-with-hyphens.com',
            'double--hyphens.com',
            'scanme.nmap.org',
        ]
        for target in valid_targets:
            with self.subTest(target=target):
                self.assertTrue(self.scanner.validate_target_input(target))

    def test_validate_target_input_invalid(self):
        """Test validate_target_input with invalid inputs."""
        invalid_targets = [
            '',                     # Empty string
            ' ',                    # Just whitespace
            'invalid-domain!',      # Invalid characters for hostname
            '192.168.1.256',        # Invalid IPv4 octet
            '127.0.0.1/33',         # Invalid CIDR mask
            'target/with/slashes',
            '-leadinghyphen.com',
            'trailinghyphen-.com',
            'test;',                # Semicolon, potential command injection if not handled
            '|pipe',
            '&&ampersand'
        ]
        for target in invalid_targets:
            with self.subTest(target=target):
                self.assertFalse(self.scanner.validate_target_input(target))

        # Test for very long input
        long_target = 'a' * 2000 # Exceeds typical hostname/IP length limits
        self.assertFalse(self.scanner.validate_target_input(long_target), "Long target string should be invalid")


    def test_parse_nmap_error_message_quitting(self):
        """Test _parse_nmap_error_message for 'QUITTING' in XML."""
        msg = self.scanner._parse_nmap_error_message(
            1,
            "<nmaprun>blah QUITTING! blah</nmaprun>",
            "",
            False
        )
        self.assertIn("QUITTING", msg)

    def test_parse_nmap_error_message_failed_to_open(self):
        """Test _parse_nmap_error_message for 'Failed to open' in stderr."""
        msg = self.scanner._parse_nmap_error_message(
            13,
            "",
            "Failed to open /dev/random for example.",
            False
        )
        self.assertIn("Stderr: Failed to open /dev/random for example.", msg)

    @patch('platform.system', return_value='Linux')
    def test_parse_nmap_error_message_escalation_cancelled_linux(self, _mock_platform_system):
        """Test _parse_nmap_error_message for user cancelling pkexec on Linux."""
        msg = self.scanner._parse_nmap_error_message(
            1,
            "",
            "",
            True
        )
        self.assertIn("User cancelled the request for administrator privileges or authentication failed.", msg)

    @patch('platform.system', return_value='Windows')
    def test_parse_nmap_error_message_escalation_failed_windows(self, _mock_platform_system):
        """Test _parse_nmap_error_message for failed escalation on Windows (generic message)."""
        msg = self.scanner._parse_nmap_error_message(
            1,
            "",
            "Some error related to permissions.",
            True
        )
        self.assertIn("Nmap scan failed with exit code 1. Stderr: Some error related to permissions.", msg)

    def test_parse_nmap_error_message_unknown_error(self):
        """Test _parse_nmap_error_message for an unknown error."""
        msg = self.scanner._parse_nmap_error_message(
            25,
            "<nmaprun></nmaprun>",
            "An unknown error occurred.",
            False
        )
        self.assertIn("Nmap scan failed with exit code 25. Stderr: An unknown error occurred.", msg)

    def test_parse_nmap_error_message_no_xml_output(self):
        """Test _parse_nmap_error_message when XML output is missing."""
        msg = self.scanner._parse_nmap_error_message(
            1,
            "",
            "Some error.",
            False
        )
        self.assertIn("Nmap scan failed with exit code 1. Stderr: Some error.", msg)

    def test_parse_nmap_error_message_target_not_responding(self):
        """Test _parse_nmap_error_message for target not responding."""
        stderr_msg = "Note: Host seems down. If it is really up, but blocking our ping probes, try -Pn"
        msg = self.scanner._parse_nmap_error_message(
            0,
            "<nmaprun><scaninfo type=\"connect\" protocol=\"tcp\" numservices=\"1\" services=\"80\"/><host><status state=\"down\" reason=\"user-set\" reason_ttl=\"0\"/></host></nmaprun>",
            stderr_msg,
            False
        )
        self.assertIn("Stderr: Note: Host seems down. If it is really up, but blocking our ping probes, try -Pn", msg)

    def test_export_and_import_profile_yaml(self):
        """Test round-trip export and import of scan profile YAML."""
        original_params = {
            "target": "192.168.1.0/24",
            "os_fingerprinting": True,
            "scan_all_ports": True,
            "tcp_syn_scan": True,
            "service_version": True,
            "no_ping": True,
            "timing_template": "T4",
            "selected_script": "vuln",
            "output_format": "Normal (.txt)",
        }
        yaml_str = NmapScanner.export_profile_yaml(
            original_params, name="Security Audit", description="Full vulnerability scan"
        )
        self.assertIn("Security Audit", yaml_str)
        self.assertIn("192.168.1.0/24", yaml_str)
        self.assertIn("vuln", yaml_str)

        imported = NmapScanner.import_profile_yaml(yaml_str)
        self.assertEqual(imported.get("name"), "Security Audit")
        self.assertEqual(imported.get("description"), "Full vulnerability scan")
        self.assertEqual(imported.get("target"), "192.168.1.0/24")
        self.assertTrue(imported.get("os_fingerprinting"))
        self.assertTrue(imported.get("scan_all_ports"))
        self.assertTrue(imported.get("tcp_syn_scan"))
        self.assertTrue(imported.get("service_version"))
        self.assertTrue(imported.get("no_ping"))
        self.assertEqual(imported.get("timing_template"), "T4")
        self.assertEqual(imported.get("selected_script"), "vuln")
        self.assertEqual(imported.get("output_format"), "Normal (.txt)")

    def test_import_profile_yaml_invalid(self):
        """Test import_profile_yaml with invalid YAML data."""
        with self.assertRaises(ValueError):
            NmapScanner.import_profile_yaml("not: a: valid: yaml: [")

        with self.assertRaises(ValueError):
            NmapScanner.import_profile_yaml("- item 1\n- item 2")


if __name__ == '__main__':
    unittest.main()

