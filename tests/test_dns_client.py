import unittest
from unittest.mock import patch, MagicMock
import logging

from src.dns_client import DnsResolverClient, DnsNoAnswerError, DnsGenericError
# Assuming dns.exception.SyntaxError, dns.reversename might be needed for full simulation
from dns.exception import SyntaxError as DnsSyntaxError
from dns.exception import DNSException


class TestDnsResolverClient(unittest.TestCase):

    def setUp(self):
        # Basic logger setup to catch log messages for assertions if needed
        self.logger = logging.getLogger('src.dns_client')
        self.logger.setLevel(logging.DEBUG) # Ensure all levels are processed by handlers
        # self.log_capture_string = io.StringIO()
        # self.ch = logging.StreamHandler(self.log_capture_string)
        # self.ch.setLevel(logging.DEBUG)
        # self.logger.addHandler(self.ch)
        # self.initial_handler_count = len(self.logger.handlers)

    def tearDown(self):
        # Remove the handler after each test
        # self.logger.removeHandler(self.ch)
        # self.assertEqual(len(self.logger.handlers), self.initial_handler_count -1) # Ensure only our handler was removed
        pass


    @patch('src.dns_client.dns.reversename.from_address')
    @patch('src.dns_client.DnsResolverClient._lookup_record_internal')
    def test_resolve_ptr_type_error_in_from_address(self, mock_lookup_internal, mock_from_address):
        """
        Test that a TypeError from dns.reversename.from_address during PTR
        conversion is caught, logged, and resolve proceeds with original target.
        """
        mock_from_address.side_effect = TypeError("Test TypeError from from_address")
        # _lookup_record_internal should return some valid result for the assertion
        mock_lookup_internal.return_value = [{"name": "1.2.3.4", "type": "PTR", "target": "example.com"}]

        client = DnsResolverClient()
        test_ip = "1.2.3.4"

        with self.assertLogs(logger='src.dns_client', level='ERROR') as cm:
            result = client.resolve(domain_or_ip=test_ip, record_type="PTR")

        self.assertTrue(any("Type error during IP to reverse name conversion" in log_msg for log_msg in cm.output))
        self.assertTrue(any(f"'{test_ip}'" in log_msg for log_msg in cm.output)) # Check if IP is in log
        self.assertTrue(any("Test TypeError from from_address" in log_msg for log_msg in cm.output))

        # Assert that _lookup_record_internal was called with the original IP, not a reversed name
        mock_lookup_internal.assert_called_once_with(test_ip, "PTR")
        self.assertIsNotNone(result) # Ensure a result is still returned

    @patch('src.dns_client.dns.reversename.from_address')
    @patch('src.dns_client.DnsResolverClient._lookup_record_internal')
    def test_resolve_ptr_value_error_in_from_address(self, mock_lookup_internal, mock_from_address):
        """
        Test that a ValueError from dns.reversename.from_address during PTR
        conversion is caught, logged, and resolve proceeds with original target.
        """
        mock_from_address.side_effect = ValueError("Test ValueError from from_address")
        mock_lookup_internal.return_value = [{"name": "4.3.2.1", "type": "PTR", "target": "another.com"}]


        client = DnsResolverClient()
        test_ip = "4.3.2.1"

        with self.assertLogs(logger='src.dns_client', level='ERROR') as cm:
            result = client.resolve(domain_or_ip=test_ip, record_type="PTR")

        self.assertTrue(any("Value error during IP to reverse name conversion" in log_msg for log_msg in cm.output))
        self.assertTrue(any(f"'{test_ip}'" in log_msg for log_msg in cm.output))
        self.assertTrue(any("Test ValueError from from_address" in log_msg for log_msg in cm.output))

        mock_lookup_internal.assert_called_once_with(test_ip, "PTR")
        self.assertIsNotNone(result)

    @patch('src.dns_client.dns.reversename.from_address')
    @patch('src.dns_client.DnsResolverClient._lookup_record_internal')
    def test_resolve_ptr_dns_syntax_error_in_from_address(self, mock_lookup_internal, mock_from_address):
        """
        Test that a dns.exception.SyntaxError from dns.reversename.from_address
        is caught, logged (as WARNING), and resolve proceeds with original target.
        """
        mock_from_address.side_effect = DnsSyntaxError("Test SyntaxError from from_address")
        mock_lookup_internal.return_value = [] # Simulate a case where lookup then finds nothing

        client = DnsResolverClient()
        test_ip = "bad-ip-format" # This would normally be caught by ipaddress.ip_address first

        # Patch ipaddress.ip_address to not raise ValueError for this specific test input,
        # so that dns.reversename.from_address is actually called with "bad-ip-format".
        with patch('src.dns_client.ipaddress.ip_address', return_value=MagicMock()):
            with self.assertLogs(logger='src.dns_client', level='WARNING') as cm:
                result = client.resolve(domain_or_ip=test_ip, record_type="PTR")

        self.assertTrue(any("Syntax error converting" in log_msg for log_msg in cm.output))
        self.assertTrue(any(f"'{test_ip}'" in log_msg for log_msg in cm.output))
        self.assertTrue(any("Test SyntaxError from from_address" in log_msg for log_msg in cm.output))

        mock_lookup_internal.assert_called_once_with(test_ip, "PTR")
        self.assertEqual(result, [])


    @patch('src.dns_client.DnsResolverClient._lookup_record_internal')
    def test_resolve_ptr_valid_ip(self, mock_lookup_internal):
        """
        Test PTR resolution for a valid IP, ensuring from_address is called
        and _lookup_record_internal is called with the reversed name.
        """
        # This also implicitly tests that ipaddress.ip_address works as expected for valid IPs.
        reversed_ip_expected = "1.0.0.127.in-addr.arpa" # Example for 127.0.0.1
        mock_lookup_internal.return_value = [{"name": reversed_ip_expected, "type": "PTR", "target": "localhost"}]

        client = DnsResolverClient()
        test_ip = "127.0.0.1"

        # No specific exception expected here
        result = client.resolve(domain_or_ip=test_ip, record_type="PTR")

        # dns.reversename.from_address should have been called by the client.resolve method.
        # We can't directly assert its call without another patch, but we can check
        # that _lookup_record_internal was called with the correctly reversed name.
        mock_lookup_internal.assert_called_once_with(reversed_ip_expected, "PTR")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]['target'], "localhost")

    @patch('src.dns_client.DnsResolverClient._lookup_record_internal')
    def test_resolve_ptr_non_ip_target(self, mock_lookup_internal):
        """
        Test PTR resolution for a non-IP target. ipaddress.ip_address should fail,
        and _lookup_record_internal should be called with the original (non-IP) target.
        """
        mock_lookup_internal.return_value = [] # Expect no PTR for a hostname typically

        client = DnsResolverClient()
        test_hostname = "example.com"

        result = client.resolve(domain_or_ip=test_hostname, record_type="PTR")

        # ipaddress.ip_address(test_hostname) would raise ValueError, so is_ip becomes False.
        # dns.reversename.from_address should NOT be called.
        # _lookup_record_internal should be called with the original hostname.
        mock_lookup_internal.assert_called_once_with(test_hostname, "PTR")
        self.assertEqual(result, [])

if __name__ == '__main__':
    unittest.main()
