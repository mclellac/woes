"""Module for performing DNS lookups."""
import logging
from typing import Optional, List, Dict, Any
import ipaddress

import dns.resolver
import dns.reversename
import dns.rdatatype
import dns.rdataclass
import dns.exception

logger = logging.getLogger(__name__)

# Custom Exceptions
class DnsClientError(Exception):
    """Base exception for DnsResolverClient errors."""
    pass

class DnsResolutionTimeoutError(DnsClientError):
    """Exception for DNS resolution timeouts."""
    pass

class DnsNxDomainError(DnsClientError):
    """Exception for NXDOMAIN errors (non-existent domain)."""
    pass

class DnsNoAnswerError(DnsClientError):
    """Exception for NoAnswer errors (query name exists, but not for specified type)."""
    pass

class DnsGenericError(DnsClientError):
    """Exception for other DNS resolution errors."""
    pass


class DnsResolverClient:
    """Encapsulates logic for performing DNS lookups."""

    def __init__(self, custom_dns_server: Optional[str] = None):
        """Initialize DnsResolverClient.

        :param custom_dns_server: Optional IP address of a custom DNS server.
        :type custom_dns_server: Optional[str]
        """
        self.resolver = dns.resolver.Resolver()
        if custom_dns_server:
            self.resolver.nameservers = [custom_dns_server]
        # Configure resolver properties if needed, e.g., timeout
        self.resolver.timeout = 2.0
        self.resolver.lifetime = 2.0 # Total time for resolution attempt

    def _lookup_record_internal(self, query_name_str: str, record_type_str: str) -> List[Dict[str, Any]]:
        """Internal method to look up DNS records and parse them.

        Adapted from DNSPage._lookup_record.

        :param query_name_str: The domain name or reverse IP to query.
        :type query_name_str: str
        :param record_type_str: The string representation of the DNS record type.
        :type record_type_str: str
        :raises DnsResolutionTimeoutError: If the DNS query times out.
        :raises DnsNxDomainError: If the domain does not exist.
        :raises DnsNoAnswerError: If the query name is valid but no records of the requested type exist.
        :raises DnsGenericError: For other DNS lookup failures.
        :return: A list of dictionaries, where each dictionary represents a parsed DNS record.
        :rtype: List[Dict[str, Any]]
        """
        try:
            answer = self.resolver.resolve(query_name_str, record_type_str)
            parsed_records: List[Dict[str, Any]] = []
            for rdata in answer:
                record: Dict[str, Any] = {
                    'name': answer.qname.to_text(),
                    'ttl': rdata.ttl if hasattr(rdata, 'ttl') else answer.response.answer[0].ttl,
                    'class': dns.rdataclass.to_text(rdata.rdclass),
                    'type': dns.rdatatype.to_text(rdata.rdtype)
                }
                # Populate record-specific fields
                if rdata.rdtype == dns.rdatatype.A: record['address'] = rdata.address
                elif rdata.rdtype == dns.rdatatype.AAAA: record['address'] = rdata.address
                elif rdata.rdtype == dns.rdatatype.CNAME: record['target'] = rdata.target.to_text()
                elif rdata.rdtype == dns.rdatatype.MX:
                    record['preference'] = rdata.preference
                    record['exchange'] = rdata.exchange.to_text()
                elif rdata.rdtype == dns.rdatatype.TXT: record['texts'] = [s.decode('utf-8', 'replace') for s in rdata.strings]
                elif rdata.rdtype == dns.rdatatype.NS: record['target'] = rdata.target.to_text()
                elif rdata.rdtype == dns.rdatatype.PTR: record['target'] = rdata.target.to_text()
                elif rdata.rdtype == dns.rdatatype.SOA:
                    record['mname'] = rdata.mname.to_text()
                    record['rname'] = rdata.rname.to_text()
                    record['serial'] = rdata.serial
                    record['refresh'] = rdata.refresh
                    record['retry'] = rdata.retry
                    record['expire'] = rdata.expire
                    record['minimum'] = rdata.minimum
                else: record['data'] = rdata.to_text()
                parsed_records.append(record)
            return parsed_records
        except dns.resolver.NXDOMAIN as e:
            logger.info("DnsResolverClient: NXDOMAIN for %s (%s): %s", query_name_str, record_type_str, e)
            raise DnsNxDomainError(f"Domain not found: {query_name_str}") from e
        except dns.resolver.NoAnswer as e:
            logger.info("DnsResolverClient: NoAnswer for %s (%s): %s", query_name_str, record_type_str, e)
            raise DnsNoAnswerError(f"No {record_type_str} records found for {query_name_str}, though the name exists.") from e
        except dns.resolver.Timeout as e:
            logger.warning("DnsResolverClient: Timeout for %s (%s): %s", query_name_str, record_type_str, e)
            raise DnsResolutionTimeoutError(f"DNS query timed out for {query_name_str}") from e
        except dns.exception.DNSException as e: # Catch other dnspython specific exceptions
            logger.warning("DnsResolverClient: DNSException for %s (%s): %s", query_name_str, record_type_str, e)
            raise DnsGenericError(f"DNS error for {query_name_str}: {e}") from e

    def resolve(self, domain_or_ip: str, record_type: str) -> List[Dict[str, Any]]:
        """Resolve DNS records for the given domain/IP and record type.

        :param domain_or_ip: The domain name or IP address to query.
        :type domain_or_ip: str
        :param record_type: The DNS record type string (e.g., "A", "MX", "PTR").
                            If "PTR" is requested for an IP, it handles reverse DNS.
        :type record_type: str
        :raises DnsClientError: and its subclasses for various DNS resolution issues.
        :return: A list of dictionaries, each representing a parsed DNS record.
        :rtype: List[Dict[str, Any]]
        """
        logger.debug("DnsResolverClient: resolve called for %s, type %s", domain_or_ip, record_type)

        query_target = domain_or_ip
        # For PTR queries of IP addresses, convert to reverse DNS name.
        # The `is_valid_ip` check should ideally be done by the caller to ensure
        # `domain_or_ip` is appropriate for the `record_type`.
        # However, `dns.reversename.from_address` will raise if it's not an IP.
        if record_type.upper() == "PTR":
            try:
                # Attempt to convert to reverse name only if it looks like an IP.
                # A simple check here can prevent `dns.reversename.from_address` from raising
                # an exception if `domain_or_ip` is a hostname.
                # A more robust IP validation might be needed depending on expected inputs.
                is_ip = False
                try:
                    ipaddress.ip_address(domain_or_ip) # Use a proper IP validation library
                    is_ip = True
                except ValueError:
                    is_ip = False

                if is_ip:
                    query_target = dns.reversename.from_address(domain_or_ip)
                    logger.debug("DnsResolverClient: Converted IP %s to reverse name %s for PTR lookup", domain_or_ip, query_target)
                # If it's not an IP, and PTR is requested, it will likely fail or return empty,
                # which is the correct behavior for a non-IP PTR query.
            except dns.exception.SyntaxError as e: # Raised by from_address if not a valid IP string
                 logger.warning("DnsResolverClient: Syntax error converting '%s' for PTR query: %s. Proceeding with original target.", domain_or_ip, e)
                 # Proceed with domain_or_ip as query_target, which might be intentional for non-IP PTRs
            except TypeError as e_type:
                 logger.error("DnsResolverClient: Type error during IP to reverse name conversion for PTR query on '%s': %s. Proceeding with original target.", domain_or_ip, e_type)
            except ValueError as e_value: # e.g. from dns.ipv6.aton for bad scope ID
                 logger.error("DnsResolverClient: Value error during IP to reverse name conversion for PTR query on '%s': %s. Proceeding with original target.", domain_or_ip, e_value)
            except Exception as e: # Catch any other unexpected error during conversion
                 logger.error("DnsResolverClient: Unexpected error converting '%s' for PTR query: %s. Proceeding with original target.", domain_or_ip, e)

        return self._lookup_record_internal(query_target, record_type.upper())
