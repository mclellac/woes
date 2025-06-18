"""Provides a client for performing DNS lookups."""

import logging
from typing import Optional, Any # Use list, dict
import ipaddress

import dns.resolver
import dns.reversename
import dns.rdatatype
import dns.rdataclass
import dns.exception

logger = logging.getLogger(__name__)


class DnsClientError(Exception):
    """Base exception for :class:`DnsResolverClient` errors."""

    pass


class DnsResolutionTimeoutError(DnsClientError):
    """DNS resolution timeout."""

    pass


class DnsNxDomainError(DnsClientError):
    """NXDOMAIN error (non-existent domain)."""

    pass


class DnsNoAnswerError(DnsClientError):
    """NoAnswer error (query name exists, but not for specified type)."""

    pass


class DnsGenericError(DnsClientError):
    """Other DNS resolution error."""

    pass


class DnsResolverClient:
    """Encapsulates DNS lookup logic."""

    def __init__(self, custom_dns_server: Optional[str] = None):
        """
        Initialize :class:`DnsResolverClient`.

        :param custom_dns_server: Optional IP address of a custom DNS server.
        :type custom_dns_server: str, optional
        """
        self.resolver: dns.resolver.Resolver = dns.resolver.Resolver()
        if custom_dns_server:
            self.resolver.nameservers = [custom_dns_server]
        self.resolver.timeout = 2.0
        self.resolver.lifetime = 2.0

    def _lookup_record_internal(self, query_name_str: str, record_type_str: str) -> list[dict[str, Any]]:
        """
        Look up and parse DNS records.

        Internal method.

        :param query_name_str: Domain name or reverse IP to query.
        :type query_name_str: str
        :param record_type_str: DNS record type string.
        :type record_type_str: str
        :raises DnsResolutionTimeoutError: If DNS query times out.
        :raises DnsNxDomainError: If domain does not exist.
        :raises DnsNoAnswerError: If query name valid but no records of requested type exist.
        :raises DnsGenericError: For other DNS lookup failures.
        :return: List of parsed DNS record dictionaries.
        :rtype: list[dict[str, Any]]
        """
        try:
import dns.name
from dns.rdtypes.IN import A, AAAA, CNAME, MX, NS, PTR, SOA
from dns.rdtypes.ANY import TXT # TXT can be class IN or ANY

# Helper functions for parsing specific RDATA types
# Each function takes the rdata object and the base record dictionary,
# and adds/modifies it with type-specific information.

def _parse_a_record(rdata: A.A, record: dict[str, Any]) -> None:
    """Parses an A record."""
    record["address"] = rdata.address

def _parse_aaaa_record(rdata: AAAA.AAAA, record: dict[str, Any]) -> None:
    """Parses an AAAA record."""
    record["address"] = rdata.address

def _parse_cname_record(rdata: CNAME.CNAME, record: dict[str, Any]) -> None:
    """Parses a CNAME record."""
    record["target"] = rdata.target.to_text()

def _parse_mx_record(rdata: MX.MX, record: dict[str, Any]) -> None:
    """Parses an MX record."""
    record["preference"] = rdata.preference
    record["exchange"] = rdata.exchange.to_text()

def _parse_txt_record(rdata: TXT.TXT, record: dict[str, Any]) -> None:
    """Parses a TXT record."""
    # rdata.strings is a list of bytes
    record["texts"] = [s.decode("utf-8", "replace") for s in rdata.strings]

def _parse_ns_record(rdata: NS.NS, record: dict[str, Any]) -> None:
    """Parses an NS record."""
    record["target"] = rdata.target.to_text()

def _parse_ptr_record(rdata: PTR.PTR, record: dict[str, Any]) -> None:
    """Parses a PTR record."""
    record["target"] = rdata.target.to_text()

def _parse_soa_record(rdata: SOA.SOA, record: dict[str, Any]) -> None:
    """Parses an SOA record."""
    record["mname"] = rdata.mname.to_text()
    record["rname"] = rdata.rname.to_text()
    record["serial"] = rdata.serial
    record["refresh"] = rdata.refresh
    record["retry"] = rdata.retry
    record["expire"] = rdata.expire
    record["minimum"] = rdata.minimum

# Dispatcher dictionary mapping RDATA types to parser functions
_RDATA_PARSERS = {
    dns.rdatatype.A: _parse_a_record,
    dns.rdatatype.AAAA: _parse_aaaa_record,
    dns.rdatatype.CNAME: _parse_cname_record,
    dns.rdatatype.MX: _parse_mx_record,
    dns.rdatatype.TXT: _parse_txt_record,
    dns.rdatatype.NS: _parse_ns_record,
    dns.rdatatype.PTR: _parse_ptr_record,
    dns.rdatatype.SOA: _parse_soa_record,
}

# ... (DnsResolverClient class definition starts here) ...
# Inside DnsResolverClient:
    def _lookup_record_internal(self, query_name_str: str, record_type_str: str) -> list[dict[str, Any]]:
        """
        Look up and parse DNS records.

        Internal method.

        :param query_name_str: Domain name or reverse IP to query.
        :type query_name_str: str
        :param record_type_str: DNS record type string.
        :type record_type_str: str
        :raises DnsResolutionTimeoutError: If DNS query times out.
        :raises DnsNxDomainError: If domain does not exist.
        :raises DnsNoAnswerError: If query name valid but no records of requested type exist.
        :raises DnsGenericError: For other DNS lookup failures.
        :return: List of parsed DNS record dictionaries.
        :rtype: list[dict[str, Any]]
        """
        try:
            # The resolve method can take various types for record_type, including int or string.
            # We use string as per our method signature.
            answer: dns.resolver.Answer = self.resolver.resolve(query_name_str, record_type_str)

            parsed_records: list[dict[str, Any]] = []

            # The answer object has an rrset attribute, which is the RRset that matched the query.
            # All rdata within this answer will share the same TTL from this rrset.
            # If answer.rrset is None (e.g., for NXDOMAIN, though caught by exception),
            # this would be an issue, but successful resolution implies an rrset.
            common_ttl = answer.rrset.ttl if answer.rrset else 0 # Default to 0 if somehow no rrset

            for rdata in answer: # type: dns.rdata.Rdata
                # Common fields for all records
                record: dict[str, Any] = {
                    "name": answer.qname.to_text(), # qname is a dns.name.Name object
                    "ttl": common_ttl,
                    "class": dns.rdataclass.to_text(rdata.rdclass),
                    "type": dns.rdatatype.to_text(rdata.rdtype),
                }

                # Use the dispatcher to find the appropriate parser
                parser = _RDATA_PARSERS.get(rdata.rdtype)
                if parser:
                    # We cast rdata to 'Any' here because the parser functions have specific types.
                    # Alternatively, each parser could handle 'Any' and do its own check,
                    # or we'd need a more complex dispatcher type hint.
                    # For now, this keeps parser signatures clean.
                    # The dispatcher logic ensures correct parser is called.
                    parser(rdata, record) # type: ignore[arg-type]
                else:
                    # Fallback for unknown record types
                    record["data"] = rdata.to_text()

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
        except dns.resolver.Timeout as e:
            logger.warning("DnsResolverClient: Timeout for %s (%s): %s", query_name_str, record_type_str, e)
            raise DnsResolutionTimeoutError(f"DNS query timed out for {query_name_str}") from e
        except dns.exception.DNSException as e:
            logger.warning("DnsResolverClient: DNSException for %s (%s): %s", query_name_str, record_type_str, e)
            raise DnsGenericError(f"DNS error for {query_name_str}: {e}") from e

    def resolve(self, domain_or_ip: str, record_type: str) -> list[dict[str, Any]]:
        """
        Resolve DNS records for a domain/IP and record type.

        :param domain_or_ip: Domain name or IP address to query.
        :type domain_or_ip: str
        :param record_type: DNS record type string (e.g., "A", "MX", "PTR").
                            Handles reverse DNS for "PTR" queries of IP addresses.
        :type record_type: str
        :raises DnsClientError: and its subclasses for DNS resolution issues.
        :return: List of parsed DNS record dictionaries.
        :rtype: list[dict[str, Any]]
        """
        logger.debug("DnsResolverClient: resolve called for %s, type %s", domain_or_ip, record_type)

        query_target = domain_or_ip
        # For PTR queries of IP addresses, convert to reverse DNS name.
        # The `is_valid_ip` check should ideally be done by the caller to ensure
        # `domain_or_ip` is appropriate for the `record_type`.
        # However, `dns.reversename.from_address` will raise if it's not an IP.
        if record_type.upper() == "PTR":
            try:
                is_ip = False
                try:
                    ipaddress.ip_address(domain_or_ip)  # Use a proper IP validation library
                    is_ip = True
                except ValueError:
                    is_ip = False

                if is_ip:
                    query_target = dns.reversename.from_address(domain_or_ip)
                    logger.debug(
                        "DnsResolverClient: Converted IP %s to reverse name %s for PTR lookup",
                        domain_or_ip,
                        query_target,
                    )
                # If it's not an IP, and PTR is requested, it will likely fail or return empty,
                # which is the correct behavior for a non-IP PTR query.
            except dns.exception.SyntaxError as e:
                logger.warning(
                    "DnsResolverClient: Syntax error converting '%s' for PTR query: %s. Proceeding with original target.",
                    domain_or_ip,
                    e,
                )
                # Proceed with domain_or_ip as query_target, which might be intentional for non-IP PTRs
            except TypeError as e_type:
                logger.error(
                    "DnsResolverClient: Type error during IP to reverse name conversion for PTR query on '%s': %s. Proceeding with original target.",
                    domain_or_ip,
                    e_type,
                )
            except ValueError as e_value:
                logger.error(
                    "DnsResolverClient: Value error during IP to reverse name conversion for PTR query on '%s': %s. Proceeding with original target.",
                    domain_or_ip,
                    e_value,
                )
            except Exception as e:
                logger.error(
                    "DnsResolverClient: Unexpected error converting '%s' for PTR query: %s. Proceeding with original target.",
                    domain_or_ip,
                    e,
                )

        return self._lookup_record_internal(query_target, record_type.upper())
