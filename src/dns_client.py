"""
Provides a client for performing DNS lookups using the `dnspython` library.

This module includes :class:`DnsResolverClient` which facilitates DNS queries,
allowing for the use of a custom DNS server and providing specific error types
for common DNS issues like NXDOMAIN, NoAnswer, and timeouts. It also handles
reverse DNS lookups for PTR records.
"""

import logging
import ipaddress
from typing import Optional, Any, List, Dict # Standard library types can be used directly

import dns.resolver
import dns.reversename
import dns.rdatatype
import dns.rdataclass
import dns.exception

from gi.repository import Gio, GLib # Import GLib
from .constants import APP_ID

logger = logging.getLogger(__name__)


class DnsClientError(Exception):
    """Base exception for :class:`DnsResolverClient` errors."""

    pass


class DnsResolutionTimeoutError(DnsClientError):
    """DNS resolution query timed out."""

    pass


class DnsNxDomainError(DnsClientError):
    """DNS query resulted in NXDOMAIN (Non-Existent Domain)."""

    pass


class DnsNoAnswerError(DnsClientError):
    """DNS query resulted in NoAnswer (query name exists, but not for the specified record type)."""

    pass


class DnsGenericError(DnsClientError):
    """Encapsulates other DNS resolution errors not covered by specific exceptions."""

    pass


class DnsResolverClient:
    """
    A client to perform DNS lookups using custom or system DNS servers.

    This client utilizes the `dnspython` library for DNS resolution and can be
    configured with specific timeouts obtained from GSettings.
    """

    def __init__(self, custom_dns_server: Optional[str] = None):
        """
        Initialize the DnsResolverClient.

        :param custom_dns_server: Optional IP address of a custom DNS server.
                                  If not provided, the system's default DNS resolver is used.
        """
        self.resolver: dns.resolver.Resolver = dns.resolver.Resolver()
        if custom_dns_server:
            self.resolver.nameservers = [custom_dns_server]
            logger.info("DnsResolverClient using custom DNS server: %s", custom_dns_server)
        else:
            logger.info("DnsResolverClient using system default DNS servers.")

        self.settings = Gio.Settings.new(APP_ID)
        # Ensure a default value is gracefully handled if gsettings schema is not perfectly set up,
        # though schema should define a default.
        try:
            dns_timeout_seconds_float = self.settings.get_double("dns-resolver-timeout")
        except GLib.Error as e: # More specific exception for GSettings issues
            logger.warning(
                "Could not read 'dns-resolver-timeout' from GSettings (error: %s). Using default timeout 2.0s.", e
            )
            dns_timeout_seconds_float = 2.0

        if dns_timeout_seconds_float <= 0:
            logger.warning(
                "DNS resolver timeout from GSettings was invalid (<=0). Using default timeout 2.0s."
            )
            dns_timeout_seconds_float = 2.0


        self.resolver.timeout = dns_timeout_seconds_float
        self.resolver.lifetime = dns_timeout_seconds_float
        logger.info(
            "DnsResolverClient initialized with resolver timeout/lifetime: %.1f seconds.", dns_timeout_seconds_float
        )

    def _lookup_record_internal(self, query_name_str: str, record_type_str: str) -> List[Dict[str, Any]]:
        """
        Perform the actual DNS lookup and parse various record types.

        This is an internal method that handles the core resolution logic and
        formats the results into a list of dictionaries.

        :param query_name_str: The domain name or reverse IP address (e.g., "x.x.x.x.in-addr.arpa") to query.
        :param record_type_str: The DNS record type to query for (e.g., "A", "MX", "SOA").
        :raises DnsResolutionTimeoutError: If the DNS query times out.
        :raises DnsNxDomainError: If the queried domain name does not exist.
        :raises DnsNoAnswerError: If the domain name is valid but no records of the
                                  requested type exist.
        :raises DnsGenericError: For other DNS lookup failures or unexpected errors.
        :return: A list of dictionaries, where each dictionary represents a parsed DNS record.
                 Returns an empty list if no records are found but no exception is raised.
        """
        try:
            # The resolve() method returns an Answer object.
            answer: dns.resolver.Answer = self.resolver.resolve(query_name_str, record_type_str)  # type: ignore[no-untyped-call]

            parsed_records: list[dict[str, Any]] = []
            # Iterating through an Answer object yields Rdata objects.
            # The Answer object itself has attributes like `qname`, `rrset`, etc.
            # The `rrset` contains all Rdata for the answer, and `rrset.ttl` is common.
            # Each rdata in `answer` (which is an iterable of the RRset's Rdata) might also have a ttl,
            # though it's typically the same as answer.ttl or answer.rrset.ttl.
            common_ttl = answer.rrset.ttl if answer.rrset else 300 # Default TTL if not available in rrset

            for rdata in answer:
                record: Dict[str, Any] = {
                    "name": answer.qname.to_text(),
                    "ttl": rdata.ttl if hasattr(rdata, "ttl") else common_ttl, # Prefer rdata specific, fallback to common
                    "class": dns.rdataclass.to_text(rdata.rdclass),  # type: ignore[no-untyped-call]
                    "type": dns.rdatatype.to_text(rdata.rdtype),  # type: ignore[no-untyped-call]
                }
                # Populate record-specific fields
                if rdata.rdtype == dns.rdatatype.A:
                    record["address"] = rdata.address
                elif rdata.rdtype == dns.rdatatype.AAAA:
                    record["address"] = rdata.address
                elif rdata.rdtype == dns.rdatatype.CNAME:
                    record["target"] = rdata.target.to_text()
                elif rdata.rdtype == dns.rdatatype.MX:
                    record["preference"] = rdata.preference
                    record["exchange"] = rdata.exchange.to_text()
                elif rdata.rdtype == dns.rdatatype.TXT:
                    record["texts"] = [s.decode("utf-8", "replace") for s in rdata.strings]
                elif rdata.rdtype == dns.rdatatype.NS:
                    record["target"] = rdata.target.to_text()
                elif rdata.rdtype == dns.rdatatype.PTR:
                    record["target"] = rdata.target.to_text()
                elif rdata.rdtype == dns.rdatatype.SOA:
                    record["mname"] = rdata.mname.to_text()
                    record["rname"] = rdata.rname.to_text()
                    record["serial"] = rdata.serial
                    record["refresh"] = rdata.refresh
                    record["retry"] = rdata.retry
                    record["expire"] = rdata.expire
                    record["minimum"] = rdata.minimum
                else:
                    # Generic representation for other record types
                    record["data"] = rdata.to_text()  # type: ignore[no-untyped-call]
                parsed_records.append(record)
            return parsed_records
        except dns.resolver.NXDOMAIN as e:
            logger.info("DnsResolverClient: NXDOMAIN for %s (%s): %s", query_name_str, record_type_str, e)
            raise DnsNxDomainError(f"Domain not found: {query_name_str}") from e
        except dns.resolver.NoAnswer as e:
            logger.info("DnsResolverClient: NoAnswer for %s (%s): %s", query_name_str, record_type_str, e)
            raise DnsNoAnswerError(
                f"No {record_type_str} records found for {query_name_str}, though the name exists."
            ) from e
        except dns.resolver.Timeout as e: # More specific than just DNSException for timeouts
            logger.warning("DnsResolverClient: Timeout for %s (%s): %s", query_name_str, record_type_str, e)
            raise DnsResolutionTimeoutError(f"DNS query timed out for {query_name_str}") from e
        except dns.exception.DNSException as e: # Catch other dnspython-specific exceptions
            logger.warning("DnsResolverClient: DNSException for %s (%s): %s", query_name_str, record_type_str, e)
            raise DnsGenericError(f"A DNS error occurred for {query_name_str}: {e}") from e
        except Exception as e: # Catch any other unexpected errors
            logger.exception(
                "DnsResolverClient: Unexpected generic exception for %s (%s): %s", query_name_str, record_type_str, e
            )
            raise DnsGenericError(f"An unexpected error occurred while resolving {query_name_str}: {e}") from e


    def resolve(self, domain_or_ip: str, record_type: str) -> List[Dict[str, Any]]:
        """
        Resolve DNS records for a given domain/IP address and record type.

        For "PTR" record types, if `domain_or_ip` is a valid IP address,
        it will be automatically converted to its corresponding reverse DNS name
        (e.g., "192.0.2.1" becomes "1.2.0.192.in-addr.arpa").

        :param domain_or_ip: The domain name or IP address to query.
        :param record_type: The type of DNS record to query (e.g., "A", "AAAA", "MX", "PTR", "SOA").
        :raises DnsNxDomainError: If the domain does not exist.
        :raises DnsNoAnswerError: If the domain exists but has no records of the specified type.
        :raises DnsResolutionTimeoutError: If the DNS query times out.
        :raises DnsGenericError: For other DNS or unexpected errors during resolution.
        :return: A list of dictionaries, where each dictionary represents a parsed DNS record.
        """
        logger.debug("DnsResolverClient: resolve called for '%s', type '%s'", domain_or_ip, record_type)

        query_target = domain_or_ip.strip() # Ensure no leading/trailing whitespace
        record_type_upper = record_type.strip().upper()

        if not query_target:
            raise DnsGenericError("Domain or IP address cannot be empty.")
        if not record_type_upper:
            raise DnsGenericError("Record type cannot be empty.")

        if record_type_upper == "PTR":
            try:
                # Validate if it's an IP address first. ipaddress module is stricter.
                ipaddress.ip_address(query_target)
                # If valid, convert to reverse DNS name.
                query_target = dns.reversename.from_address(query_target)
                logger.debug(
                    "DnsResolverClient: Converted IP '%s' to reverse name '%s' for PTR lookup",
                    domain_or_ip, # Log original for clarity
                    query_target,
                )
            except ValueError:
                # Not a valid IP address. If user wants PTR for a non-IP, let dnspython handle it.
                logger.debug(
                    "DnsResolverClient: '%s' is not a valid IP address. Proceeding with it directly for PTR query.",
                    query_target
                )
            except dns.exception.SyntaxError as e: # Should be rare if ipaddress validated first
                logger.warning(
                    "DnsResolverClient: Syntax error converting '%s' for PTR query: %s. Proceeding with original target.",
                    query_target, e
                )
            except Exception as e: # Catch-all for other unexpected errors during conversion
                logger.error(
                    "DnsResolverClient: Unexpected error converting '%s' for PTR query: %s. Proceeding with original target.",
                    query_target, e
                )

        return self._lookup_record_internal(query_target, record_type_upper)
