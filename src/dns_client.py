"""Provides a client for performing DNS lookups."""

import logging
from typing import Optional, Any # Use list, dict
import ipaddress

import dns.resolver
import dns.reversename
import dns.rdatatype
import dns.rdataclass
import dns.exception

from gi.repository import Gio # Added for GSettings
from .constants import APP_ID # Added for GSettings

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

        self.settings = Gio.Settings.new(APP_ID)
        dns_timeout_seconds_float = self.settings.get_double("dns-resolver-timeout")
        # Default is 2.0 if not in schema, but schema should define it.
        # dns_timeout_seconds_float = self.settings.get_double_with_default("dns-resolver-timeout", 2.0)

        self.resolver.timeout = dns_timeout_seconds_float
        self.resolver.lifetime = dns_timeout_seconds_float
        logger.info(f"DnsResolverClient initialized with timeout/lifetime: {dns_timeout_seconds_float} seconds.")

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
            answer: dns.resolver.Answer = self.resolver.resolve(query_name_str, record_type_str)  # type: ignore[no-untyped-call]
            parsed_records: list[dict[str, Any]] = []
            for rdata in answer:
                record: dict[str, Any] = {
                    "name": answer.qname.to_text(),  # type: ignore[attr-defined]
                    "ttl": rdata.ttl if hasattr(rdata, "ttl") else answer.response.answer[0].ttl,  # type: ignore[union-attr]
                    "class": dns.rdataclass.to_text(rdata.rdclass),  # type: ignore[no-untyped-call]
                    "type": dns.rdatatype.to_text(rdata.rdtype),  # type: ignore[no-untyped-call]
                }
                if rdata.rdtype == dns.rdatatype.A:
                    record["address"] = rdata.address
                elif rdata.rdtype == dns.rdatatype.AAAA:
                    record["address"] = rdata.address
                elif rdata.rdtype == dns.rdatatype.CNAME:
                    record["target"] = rdata.target.to_text()
                elif rdata.rdtype == dns.rdatatype.MX:
                    record["preference"] = rdata.preference
                    record["exchange"] = rdata.exchange.to_text()
                elif rdata.rdtype == dns.rdatatype.TXT:  # type: ignore[attr-defined]
                    record["texts"] = [s.decode("utf-8", "replace") for s in rdata.strings]  # type: ignore[attr-defined]
                elif rdata.rdtype == dns.rdatatype.NS:  # type: ignore[attr-defined]
                    record["target"] = rdata.target.to_text()  # type: ignore[attr-defined]
                elif rdata.rdtype == dns.rdatatype.PTR:  # type: ignore[attr-defined]
                    record["target"] = rdata.target.to_text()  # type: ignore[attr-defined]
                elif rdata.rdtype == dns.rdatatype.SOA:  # type: ignore[attr-defined]
                    record["mname"] = rdata.mname.to_text()  # type: ignore[attr-defined]
                    record["rname"] = rdata.rname.to_text()  # type: ignore[attr-defined]
                    record["serial"] = rdata.serial  # type: ignore[attr-defined]
                    record["refresh"] = rdata.refresh  # type: ignore[attr-defined]
                    record["retry"] = rdata.retry  # type: ignore[attr-defined]
                    record["expire"] = rdata.expire  # type: ignore[attr-defined]
                    record["minimum"] = rdata.minimum  # type: ignore[attr-defined]
                else:
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
