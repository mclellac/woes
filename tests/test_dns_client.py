"""Tests for the dns_client module."""
import unittest
from unittest.mock import patch, MagicMock, PropertyMock
import logging

from src.dns_client import DnsResolverClient
# Assuming dns.exception.SyntaxError, dns.reversename might be needed for full simulation
from dns.exception import SyntaxError as DnsSyntaxError
import ipaddress # For creating IP address objects in tests

class TestDnsResolverClient(unittest.TestCase):
    """Tests for the DnsResolverClient class."""

    def setUp(self):
        """Set up test fixtures, if any."""
        # Basic logger setup to catch log messages for assertions if needed
        self.logger = logging.getLogger('src.dns_client')
        # self.logger.setLevel(logging.DEBUG) # Or any level you want to test
        # self.ch = logging.StreamHandler()
        # self.ch.setLevel(logging.DEBUG)
        # self.formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        # self.ch.setFormatter(self.formatter)
        # self.logger.addHandler(self.ch)
        # self.initial_handler_count = len(self.logger.handlers)

    def tearDown(self):
        """Tear down test fixtures, if any."""
        # Remove the handler after each test
        # self.logger.removeHandler(self.ch)
        # Ensure no handlers are left if added by tests, to avoid duplicate messages in subsequent tests
        # current_handlers = len(self.logger.handlers)
        # if current_handlers > self.initial_handler_count: # Be careful if other tests add handlers globally
            # for _ in range(current_handlers - self.initial_handler_count):
                # self.logger.removeHandler(self.logger.handlers[-1]) # Remove last added
        pass

    @patch('dns.reversename.from_address')
    @patch('src.dns_client.DnsResolverClient._lookup_record_internal')
    def test_resolve_ptr_type_error_in_from_address(self, mock_lookup_internal, mock_from_address):
        """
        Test TypeError from dns.reversename.from_address during PTR conversion.

        Ensures the error is caught, logged, and resolve proceeds with the original target.
        """
        mock_from_address.side_effect = TypeError("Test TypeError from from_address")
        # _lookup_record_internal should return some valid result for the assertion
        # to show that the original target was used after the error.
        mock_lookup_internal.return_value = [{"name": "1.2.3.4", "type": "PTR", "target": "example.com"}]

        client = DnsResolverClient()
        with self.assertLogs('src.dns_client', level='ERROR') as cm:
            # target_ip is used to trigger the from_address call
            results = client.resolve("1.2.3.4", "PTR")

        self.assertTrue(any("TypeError converting IP for PTR" in log_msg for log_msg in cm.output))
        # Ensure _lookup_record_internal was called with the original, unconverted target
        # because from_address failed.
        mock_lookup_internal.assert_called_once_with("1.2.3.4", "PTR")
        self.assertEqual(results, [{"name": "1.2.3.4", "type": "PTR", "target": "example.com"}])


    @patch('dns.reversename.from_address')
    @patch('src.dns_client.DnsResolverClient._lookup_record_internal')
    def test_resolve_ptr_value_error_in_from_address(self, mock_lookup_internal, mock_from_address):
        """
        Test ValueError from dns.reversename.from_address during PTR conversion.

        Ensures the error is caught, logged, and resolve proceeds with the original target.
        """
        mock_from_address.side_effect = ValueError("Test ValueError from from_address")
        mock_lookup_internal.return_value = [{"name": "4.3.2.1", "type": "PTR", "target": "another.com"}]

        client = DnsResolverClient()
        with self.assertLogs('src.dns_client', level='ERROR') as cm:
            results = client.resolve("4.3.2.1", "PTR") # Use a different IP to avoid test interference

        self.assertTrue(any("ValueError converting IP for PTR" in log_msg for log_msg in cm.output))
        mock_lookup_internal.assert_called_once_with("4.3.2.1", "PTR")
        self.assertEqual(results, [{"name": "4.3.2.1", "type": "PTR", "target": "another.com"}])


    @patch('dns.reversename.from_address')
    @patch('src.dns_client.DnsResolverClient._lookup_record_internal')
    def test_resolve_ptr_dns_syntax_error_in_from_address(self, mock_lookup_internal, mock_from_address):
        """
        Test dns.exception.SyntaxError from dns.reversename.from_address.

        Ensures the error is caught, logged, and resolve proceeds with the original target.
        """
        mock_from_address.side_effect = DnsSyntaxError("Test SyntaxError from from_address")
        mock_lookup_internal.return_value = [] # Simulate a case where lookup then finds nothing

        client = DnsResolverClient()
        # Log level for DnsSyntaxError is WARNING
        with self.assertLogs('src.dns_client', level='WARNING') as cm:
            results = client.resolve("127.0.0.1", "PTR")

        self.assertTrue(any("DnsSyntaxError converting IP for PTR" in log_msg for log_msg in cm.output))
        # Verify _lookup_record_internal was still called with the original IP
        mock_lookup_internal.assert_called_once_with("127.0.0.1", "PTR")
        self.assertEqual(results, []) # Expecting empty list as per mock_lookup_internal


    @patch('dns.reversename.from_address', new_callable=PropertyMock) # Mock the from_address method
    @patch('src.dns_client.DnsResolverClient._lookup_record_internal')
    def test_resolve_ptr_valid_ip(self, mock_lookup_internal):
        """
        Test PTR resolution for a valid IP.

        Ensures from_address is called and _lookup_record_internal is called with the reversed name.
        This also implicitly tests that ipaddress.ip_address works as expected for valid IPs.
        """
        # This also implicitly tests that ipaddress.ip_address works as expected for valid IPs.
        reversed_ip_expected = "1.0.0.127.in-addr.arpa" # Example for 127.0.0.1
        # We need to mock dns.reversename.from_address to return something that has a to_text attribute
        mock_reversed_name_obj = MagicMock()
        mock_reversed_name_obj.to_text.return_value = reversed_ip_expected

        # Patch dns.reversename.from_address to return our mock object
        with patch('dns.reversename.from_address', return_value=mock_reversed_name_obj) as mock_from_address_func:
            client = DnsResolverClient()
            client.resolve("127.0.0.1", "PTR")

            mock_from_address_func.assert_called_once_with(ipaddress.ip_address("127.0.0.1"))
            # Verify _lookup_record_internal was called with the text form of the reversed name
            mock_lookup_internal.assert_called_once_with(reversed_ip_expected, "PTR")


    @patch('src.dns_client.DnsResolverClient._lookup_record_internal')
    def test_resolve_ptr_non_ip_target(self, mock_lookup_internal):
        """
        Test PTR resolution for a non-IP target.

        Ensures ipaddress.ip_address fails and _lookup_record_internal is called with the original target.
        """
        mock_lookup_internal.return_value = [] # Expect no PTR for a hostname typically

        client = DnsResolverClient()
        # ipaddress.ip_address will raise ValueError for "example.com"
        # We expect this to be caught and the original target used.
        results = client.resolve("example.com", "PTR")

        # Check that _lookup_record_internal was called with the original non-IP target
        mock_lookup_internal.assert_called_once_with("example.com", "PTR")
        self.assertEqual(results, []) # Based on mock_lookup_internal's return

if __name__ == '__main__':
    unittest.main()
