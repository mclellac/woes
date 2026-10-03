import unittest
from unittest.mock import patch, MagicMock

# Assuming src is in PYTHONPATH or project root is configured for imports
from src.dns_client import (
    DnsResolverClient,
    DnsNxDomainError,
    DnsNoAnswerError,
    DnsResolutionTimeoutError,
    DnsGenericError,
    DnsClientError, # Base class, though not directly tested for raising
)

# Import dnspython exceptions that will be mocked
import dns.resolver
import dns.exception

class TestDnsResolverClient(unittest.TestCase):

    def setUp(self):
        self.client = DnsResolverClient()
        # Default mock behavior for answer, can be overridden in specific tests
        self.mock_answer = MagicMock(spec=dns.resolver.Answer)
        self.mock_answer.qname = dns.name.from_text("example.com")
        self.mock_answer.rrset = MagicMock(spec=dns.rrset.RRset)
        self.mock_answer.rrset.ttl = 60


    @patch('dns.resolver.Resolver.resolve')
    def test_resolve_nxdomain(self, mock_resolve: MagicMock):
        """Test that DnsNxDomainError is raised for dns.resolver.NXDOMAIN."""
        mock_resolve.side_effect = dns.resolver.NXDOMAIN("Test NXDOMAIN")
        with self.assertRaises(DnsNxDomainError):
            self.client.resolve("nonexistent.example.com", "A")

    @patch('dns.resolver.Resolver.resolve')
    def test_resolve_no_answer(self, mock_resolve: MagicMock):
        """Test that DnsNoAnswerError is raised for dns.resolver.NoAnswer."""
        mock_resolve.side_effect = dns.resolver.NoAnswer("Test NoAnswer")
        with self.assertRaises(DnsNoAnswerError):
            self.client.resolve("example.com", "NonExistentType")

    @patch('dns.resolver.Resolver.resolve')
    def test_resolve_timeout(self, mock_resolve: MagicMock):
        """Test that DnsResolutionTimeoutError is raised for dns.resolver.Timeout."""
        mock_resolve.side_effect = dns.resolver.Timeout("Test Timeout")
        with self.assertRaises(DnsResolutionTimeoutError):
            self.client.resolve("example.com", "A")

    @patch('dns.resolver.Resolver.resolve')
    def test_resolve_generic_dns_exception(self, mock_resolve: MagicMock):
        """Test that DnsGenericError is raised for a generic dns.exception.DNSException."""
        mock_resolve.side_effect = dns.exception.DNSException("Test Generic DNSException")
        with self.assertRaises(DnsGenericError):
            self.client.resolve("example.com", "A")

    @patch('dns.resolver.Resolver.resolve')
    def test_resolve_successful_lookup(self, mock_resolve: MagicMock):
        """Test a successful lookup scenario (no exception raised)."""
        # Configure the mock to return a mock answer object
        # For this test, we only care that no DnsClientError is raised.
        # The actual parsing logic of _lookup_record_internal is complex;
        # here we just ensure the happy path doesn't raise client errors.

        # A minimal Rdata object for A record
        mock_rdata_a = MagicMock()
        mock_rdata_a.rdtype = dns.rdatatype.A
        mock_rdata_a.rdclass = dns.rdataclass.IN
        mock_rdata_a.address = "192.0.2.1"

        self.mock_answer.__iter__.return_value = [mock_rdata_a] # Make answer iterable
        mock_resolve.return_value = self.mock_answer

        try:
            results = self.client.resolve("example.com", "A")
            self.assertIsInstance(results, list) # Basic check for successful return
            # Further checks on 'results' content could be added if needed
            # but this test focuses on absence of exceptions for a valid mock return
        except DnsClientError as e:
            self.fail(f"resolve() raised DnsClientError unexpectedly: {e}")

    @patch('dns.query.https')
    def test_resolve_doh_success(self, mock_https: MagicMock):
        """Test successful DNS-over-HTTPS lookup."""
        client = DnsResolverClient(resolver_mode="doh", endpoint="https://cloudflare-dns.com/dns-query")
        mock_response = MagicMock(spec=dns.message.Message)
        mock_response.rcode.return_value = dns.rcode.NOERROR

        mock_rrset = MagicMock()
        mock_rrset.name.to_text.return_value = "example.com."
        mock_rrset.ttl = 300
        mock_rrset.rdclass = dns.rdataclass.IN

        mock_rdata = MagicMock()
        mock_rdata.rdtype = dns.rdatatype.A
        mock_rdata.address = "93.184.216.34"
        mock_rdata.to_text.return_value = "93.184.216.34"

        mock_rrset.__iter__.return_value = [mock_rdata]
        mock_response.answer = [mock_rrset]
        mock_https.return_value = mock_response

        results = client.resolve("example.com", "A")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["address"], "93.184.216.34")
        self.assertEqual(results[0]["type"], "A")

    @patch('dns.query.tls')
    def test_resolve_dot_success(self, mock_tls: MagicMock):
        """Test successful DNS-over-TLS lookup."""
        client = DnsResolverClient(resolver_mode="dot", endpoint="1.1.1.1")
        mock_response = MagicMock(spec=dns.message.Message)
        mock_response.rcode.return_value = dns.rcode.NOERROR

        mock_rrset = MagicMock()
        mock_rrset.name.to_text.return_value = "example.com."
        mock_rrset.ttl = 300
        mock_rrset.rdclass = dns.rdataclass.IN

        mock_rdata = MagicMock()
        mock_rdata.rdtype = dns.rdatatype.HTTPS
        mock_rdata.priority = 1
        mock_rdata.target.to_text.return_value = "."
        mock_rdata.params = 'alpn="h3,h2"'
        mock_rdata.to_text.return_value = '1 . alpn="h3,h2"'

        mock_rrset.__iter__.return_value = [mock_rdata]
        mock_response.answer = [mock_rrset]
        mock_tls.return_value = mock_response

        results = client.resolve("example.com", "HTTPS")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["type"], "HTTPS")
        self.assertEqual(results[0]["priority"], 1)

    @patch('dns.query.https')
    def test_resolve_doh_nxdomain(self, mock_https: MagicMock):
        """Test DoH lookup raises DnsNxDomainError on NXDOMAIN."""
        client = DnsResolverClient(resolver_mode="doh")
        mock_response = MagicMock(spec=dns.message.Message)
        mock_response.rcode.return_value = dns.rcode.NXDOMAIN
        mock_https.return_value = mock_response

        with self.assertRaises(DnsNxDomainError):
            client.resolve("nonexistent.example.com", "A")

    def test_custom_dns_server_prefix_parsing(self):
        """Test that custom_dns_server with https:// or tls:// sets mode appropriately."""
        client_doh = DnsResolverClient(custom_dns_server="https://custom-doh.com/dns-query")
        self.assertEqual(client_doh.resolver_mode, "doh")
        self.assertEqual(client_doh.endpoint, "https://custom-doh.com/dns-query")

        client_dot = DnsResolverClient(custom_dns_server="tls://1.0.0.1")
        self.assertEqual(client_dot.resolver_mode, "dot")
        self.assertEqual(client_dot.endpoint, "1.0.0.1")


if __name__ == '__main__':
    unittest.main()
