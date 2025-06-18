import unittest
from unittest.mock import patch

from src.nmap_scanner import NmapScanner
# Assuming PortScannerError might be raised or checked by type, though not explicitly tested here
# from nmap import PortScannerError

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
            '192.168.1.1-100',
            '192.168.1.1,192.168.1.2',
            '2001:0db8:85a3:0000:0000:8a2e:0370:7334', # IPv6
            '[2001:db8::1]', # IPv6 with brackets
            'domain-with-hyphens.com',
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
            'double--hyphens.com', # Technically valid by some RFCs but often problematic
                                   # and Nmap might reject it. Let's assume it as less common/problematic.
                                   # For this test, we'll focus on more clearly invalid cases.
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
            returncode=1,
            xml_output="<nmaprun>blah QUITTING! blah</nmaprun>",
            stderr="",
            needs_escalation_if_failed=False
        )
        self.assertIn("Nmap quit unexpectedly. The target specification might be invalid or too complex.", msg)

    def test_parse_nmap_error_message_failed_to_open(self):
        """Test _parse_nmap_error_message for 'Failed to open' in stderr."""
        msg = self.scanner._parse_nmap_error_message(
            returncode=13,
            xml_output="",
            stderr="Failed to open /dev/random for example.",
            needs_escalation_if_failed=False
        )
        self.assertIn("Nmap failed to open a required resource. This might be a permissions issue.", msg)
        self.assertIn("Stderr: Failed to open /dev/random for example.", msg)

    @patch('platform.system', return_value='Linux')
    def test_parse_nmap_error_message_escalation_cancelled_linux(self, _mock_platform_system):
        """Test _parse_nmap_error_message for user cancelling pkexec on Linux."""
        msg = self.scanner._parse_nmap_error_message(
            returncode=1,
            xml_output="",
            stderr="User cancelled authentication.", # Example, actual message varies
            needs_escalation_if_failed=True
        )
        # This specific string might change if the NmapScanner's message changes
        self.assertIn("Root privileges are required for some scan options (e.g., -O, -sS). Scan was likely cancelled by user at password prompt.", msg)

    @patch('platform.system', return_value='Windows')
    def test_parse_nmap_error_message_escalation_failed_windows(self, _mock_platform_system):
        """Test _parse_nmap_error_message for failed escalation on Windows (generic message)."""
        msg = self.scanner._parse_nmap_error_message(
            returncode=1,
            xml_output="",
            stderr="Some error related to permissions.",
            needs_escalation_if_failed=True
        )
        self.assertIn("Failed to obtain necessary privileges for the scan.", msg)

    def test_parse_nmap_error_message_unknown_error(self):
        """Test _parse_nmap_error_message for an unknown error."""
        msg = self.scanner._parse_nmap_error_message(
            returncode=25,
            xml_output="<nmaprun></nmaprun>",
            stderr="An unknown error occurred.",
            needs_escalation_if_failed=False
        )
        self.assertIn("Nmap exited with an unknown error (Code 25).", msg)
        self.assertIn("Stderr: An unknown error occurred.", msg)

    def test_parse_nmap_error_message_no_xml_output(self):
        """Test _parse_nmap_error_message when XML output is missing."""
        msg = self.scanner._parse_nmap_error_message(
            returncode=1,
            xml_output=None, # Simulate missing XML output
            stderr="Some error.",
            needs_escalation_if_failed=False
        )
        self.assertIn("Nmap did not produce valid XML output.", msg)
        self.assertIn("Stderr: Some error.", msg)

    def test_parse_nmap_error_message_target_not_responding(self):
        """Test _parse_nmap_error_message for target not responding."""
        # This often results in a non-zero return code but no specific "error" in output,
        # rather, it's an empty scan result. The _parse_nmap_error_message might not be directly
        # hit in this case if Nmap considers it a "successful" scan with no results.
        # However, if Nmap itself reports issues like "Note: Host seems down." in stderr.
        stderr_msg = "Note: Host seems down. If it is really up, but blocking our ping probes, try -Pn"
        msg = self.scanner._parse_nmap_error_message(
            returncode=0, # Nmap might return 0 if -Pn is used and host is down. Or 1 if not.
            xml_output="<nmaprun><scaninfo type=\"connect\" protocol=\"tcp\" numservices=\"1\" services=\"80\"/><host><status state=\"down\" reason=\"user-set\" reason_ttl=\"0\"/></host></nmaprun>",
            stderr=stderr_msg,
            needs_escalation_if_failed=False
        )
        # Depending on how NmapScanner handles this, the message might vary.
        # If returncode is 0, _parse_nmap_error_message might not even be called.
        # This test is more about what message _parse_nmap_error_message would construct
        # if it *were* called with such stderr.
        self.assertIn("Target may be down or unresponsive, or options used might be too restrictive (e.g., -Pn with a down host).", msg)
        self.assertIn(f"Stderr: {stderr_msg}", msg)


if __name__ == '__main__':
    unittest.main()
