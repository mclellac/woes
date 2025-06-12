# -*- coding: utf-8 -*-
"""Provides a client for performing DNS lookups with custom server support.

This module defines a `DnsResolverClient` class that encapsulates DNS
resolution logic using the `dnspython` library. It allows for specifying
a custom DNS server and includes custom exceptions for various DNS errors.
"""
import logging
from typing import Optional, List, Dict, Any
import ipaddress # Used in resolve() for IP validation

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
    """Exception raised when a DNS query times out."""
    pass


class DnsNxDomainError(DnsClientError):
    """Exception raised when a DNS query results in an NXDOMAIN error (non-existent domain)."""
    pass


class DnsNoAnswerError(DnsClientError):
    """Exception raised when a DNS query name is valid but no records of the
    requested type exist (NoAnswer).
    """
    pass


class DnsGenericError(DnsClientError):
    """Exception for other DNS resolution errors not covered by specific exceptions."""
    pass


class DnsResolverClient:
    """A client for performing DNS queries, with optional custom DNS server support.

    This class uses the `dnspython` library to resolve various DNS record types.
    It can be configured to use a specific DNS server for its queries, or it
    will use the system's default resolver if no custom server is specified.
    It also defines a set of custom exceptions to provide more granular error
    information for DNS resolution failures.
    """

    def __init__(self, custom_dns_server: Optional[str] = None):
        """Initialize DnsResolverClient.

        Args:
            custom_dns_server (Optional[str]): IP address of a custom DNS server.
                                               If None, system default resolver is used.
        """
        self.resolver = dns.resolver.Resolver()
        if custom_dns_server:
            self.resolver.nameservers = [custom_dns_server]
        # Configure resolver properties for robust querying.
        self.resolver.timeout = 2.0  # Timeout for each individual DNS query.
        self.resolver.lifetime = 2.0  # Total time allowed for the entire resolution attempt.

    def _lookup_record_internal(self, query_name_str: str, record_type_str: str) -> List[Dict[str, Any]]:
        """Internal method to look up DNS records and parse common record types.

        This method handles the actual DNS resolution and formats the results
        into a list of dictionaries.

        Args:
            query_name_str (str): The domain name or reverse IP address to query.
            record_type_str (str): The string representation of the DNS record
                                   type (e.g., "A", "MX", "TXT").

        Raises:
            DnsResolutionTimeoutError: If the DNS query times out.
            DnsNxDomainError: If the domain does not exist.
            DnsNoAnswerError: If the query name is valid but no records of the
                              requested type exist.
            DnsGenericError: For other DNS lookup failures.

        Returns:
            List[Dict[str, Any]]: A list of dictionaries, where each dictionary
                                  represents a parsed DNS record with common fields
                                  like 'name', 'ttl', 'class', 'type', and
                                  record-specific data.
        """
        try:
            answer = self.resolver.resolve(query_name_str, record_type_str)
            parsed_records: List[Dict[str, Any]] = []
            for rdata in answer:
                record: Dict[str, Any] = {
                    'name': answer.qname.to_text(),
                    'ttl': (rdata.ttl if hasattr(rdata, 'ttl')
                            else answer.response.answer[0].ttl), # type: ignore
                    'class': dns.rdataclass.to_text(rdata.rdclass),
                    'type': dns.rdatatype.to_text(rdata.rdtype)
                }
                # Populate record-specific fields based on record type
                if rdata.rdtype == dns.rdatatype.A:
                    record['address'] = rdata.address # type: ignore
                elif rdata.rdtype == dns.rdatatype.AAAA:
                    record['address'] = rdata.address # type: ignore
                elif rdata.rdtype == dns.rdatatype.CNAME:
                    record['target'] = rdata.target.to_text() # type: ignore
                elif rdata.rdtype == dns.rdatatype.MX:
                    record['preference'] = rdata.preference # type: ignore
                    record['exchange'] = rdata.exchange.to_text() # type: ignore
                elif rdata.rdtype == dns.rdatatype.TXT:
                    # Decode TXT record strings, replacing errors.
                    record['texts'] = [
                        s.decode('utf-8', 'replace') for s in rdata.strings] # type: ignore
                elif rdata.rdtype == dns.rdatatype.NS:
                    record['target'] = rdata.target.to_text() # type: ignore
                elif rdata.rdtype == dns.rdatatype.PTR:
                    record['target'] = rdata.target.to_text() # type: ignore
                elif rdata.rdtype == dns.rdatatype.SOA:
                    record['mname'] = rdata.mname.to_text() # type: ignore
                    record['rname'] = rdata.rname.to_text() # type: ignore
                    record['serial'] = rdata.serial # type: ignore
                    record['refresh'] = rdata.refresh # type: ignore
                    record['retry'] = rdata.retry # type: ignore
                    record['expire'] = rdata.expire # type: ignore
                    record['minimum'] = rdata.minimum # type: ignore
                else:
                    # For other record types, provide the raw text representation.
                    record['data'] = rdata.to_text()
                parsed_records.append(record)
            return parsed_records
        except dns.resolver.NXDOMAIN as e:
            logger.info("DnsResolverClient: NXDOMAIN for %s (%s): %s",
                        query_name_str, record_type_str, e)
            raise DnsNxDomainError(
                f"Domain not found: {query_name_str}") from e
        except dns.resolver.NoAnswer as e:
            logger.info("DnsResolverClient: NoAnswer for %s (%s): %s",
                        query_name_str, record_type_str, e)
            raise DnsNoAnswerError(
                f"No {record_type_str} records found for {query_name_str}, "
                "though the name exists.") from e
        except dns.resolver.Timeout as e:
            logger.warning("DnsResolverClient: Timeout for %s (%s): %s",
                           query_name_str, record_type_str, e)
            raise DnsResolutionTimeoutError(
                f"DNS query timed out for {query_name_str}") from e
        except dns.exception.DNSException as e:  # Catch other dnspython specific exceptions
            logger.warning("DnsResolverClient: DNSException for %s (%s): %s",
                           query_name_str, record_type_str, e)
            raise DnsGenericError(
                f"DNS error for {query_name_str}: {e}") from e

    def resolve(self, domain_or_ip: str, record_type: str) -> List[Dict[str, Any]]:
        """Resolve DNS records for a given domain or IP address and record type.

        If `record_type` is "PTR" and `domain_or_ip` is an IP address, this method
        automatically handles the conversion to the appropriate reverse DNS name format
        (e.g., 'x.x.x.x.in-addr.arpa').

        Args:
            domain_or_ip (str): The domain name or IP address to query.
            record_type (str): The DNS record type string (e.g., "A", "MX", "PTR").
                               Case-insensitive.

        Raises:
            DnsClientError: and its subclasses (DnsResolutionTimeoutError,
                            DnsNxDomainError, DnsNoAnswerError, DnsGenericError)
                            for various DNS resolution issues.

        Returns:
            List[Dict[str, Any]]: A list of dictionaries, each representing a
                                  parsed DNS record. Returns an empty list if
                                  no records are found but no error occurred.
        """
        logger.debug(
            "DnsResolverClient: resolve called for %s, type %s",
            domain_or_ip, record_type)

        query_target = domain_or_ip
        # For PTR queries of IP addresses, convert to reverse DNS name.
        if record_type.upper() == "PTR":
            try:
                # Validate if it's an IP address before attempting reverse conversion.
                ipaddress.ip_address(domain_or_ip)
                query_target = dns.reversename.from_address(domain_or_ip)
                logger.debug(
                    "DnsResolverClient: Converted IP %s to reverse name %s "
                    "for PTR lookup", domain_or_ip, query_target)
            except ValueError:
                # Not a valid IP address, proceed with domain_or_ip as is for PTR.
                # This might be intentional if querying PTR for a non-IP name.
                logger.debug(
                    "DnsResolverClient: '%s' is not an IP address. "
                    "Proceeding with it directly for PTR lookup.", domain_or_ip)
            except dns.exception.SyntaxError as e:  # Should be rare if ipaddress validation passes
                logger.warning(
                    "DnsResolverClient: Syntax error converting '%s' for PTR "
                    "query: %s. Proceeding with original target.",
                    domain_or_ip, e)
            except Exception as e:  # Catch any other unexpected error during conversion
                logger.error(
                    "DnsResolverClient: Unexpected error converting '%s' for "
                    "PTR query: %s. Proceeding with original target.",
                    domain_or_ip, e, exc_info=True)

        return self._lookup_record_internal(query_target, record_type.upper())
