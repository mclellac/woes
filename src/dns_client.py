"""Provides a client for performing DNS lookups with support for Standard DNS, DoH, and DoT."""

import asyncio
import concurrent.futures
import ipaddress
import logging
from typing import Any, Optional

import dns.asyncquery
import dns.asyncresolver
import dns.exception
import dns.message
import dns.query
import dns.rdataclass
import dns.rdatatype
import dns.rcode
import dns.resolver
import dns.reversename

logger = logging.getLogger(__name__)

MODE_STANDARD = "standard"
MODE_DOH = "doh"
MODE_DOT = "dot"

DOH_PRESETS: dict[str, str] = {
    "Cloudflare": "https://cloudflare-dns.com/dns-query",
    "Google": "https://dns.google/dns-query",
    "Quad9": "https://dns.quad9.net/dns-query",
}

DOT_PRESETS: dict[str, str] = {
    "Cloudflare": "1.1.1.1",
    "Quad9": "9.9.9.9",
    "Google": "8.8.8.8",
}

ANY_RECORD_TYPES: list[str] = [
    "A",
    "AAAA",
    "CNAME",
    "MX",
    "TXT",
    "NS",
    "SOA",
    "SRV",
    "CAA",
    "HTTPS",
    "DNSKEY",
]


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


def _parse_single_rdata(name: str, ttl: int, rdclass: Any, rdata: Any) -> dict[str, Any]:
    """Parse a single DNS rdata record into a standardized dictionary."""
    rdtype = getattr(rdata, "rdtype", None)
    rdclass_str = dns.rdataclass.to_text(rdclass) if isinstance(rdclass, int) else str(rdclass)
    rdtype_str = (
        dns.rdatatype.to_text(rdtype)
        if isinstance(rdtype, (int, dns.rdatatype.RdataType))
        else str(getattr(rdata, "type", ""))
    )
    raw_data = rdata.to_text() if hasattr(rdata, "to_text") else str(rdata)

    record: dict[str, Any] = {
        "name": name,
        "ttl": ttl,
        "class": rdclass_str,
        "type": rdtype_str,
        "data": raw_data,
    }

    if rdtype in (dns.rdatatype.A, dns.rdatatype.AAAA):
        record["address"] = getattr(rdata, "address", raw_data)
    elif rdtype in (dns.rdatatype.CNAME, dns.rdatatype.NS, dns.rdatatype.PTR):
        record["target"] = rdata.target.to_text() if hasattr(rdata, "target") else raw_data
    elif rdtype == dns.rdatatype.MX:
        record["preference"] = getattr(rdata, "preference", 0)
        record["exchange"] = rdata.exchange.to_text() if hasattr(rdata, "exchange") else raw_data
    elif rdtype == dns.rdatatype.TXT:
        if hasattr(rdata, "strings"):
            record["texts"] = [
                s.decode("utf-8", "replace") if isinstance(s, bytes) else str(s)
                for s in rdata.strings
            ]
        else:
            record["texts"] = [raw_data]
    elif rdtype == dns.rdatatype.SOA:
        record["mname"] = rdata.mname.to_text() if hasattr(rdata, "mname") else "N/A"
        record["rname"] = rdata.rname.to_text() if hasattr(rdata, "rname") else "N/A"
        record["serial"] = getattr(rdata, "serial", 0)
        record["refresh"] = getattr(rdata, "refresh", 0)
        record["retry"] = getattr(rdata, "retry", 0)
        record["expire"] = getattr(rdata, "expire", 0)
        record["minimum"] = getattr(rdata, "minimum", 0)
    elif rdtype == dns.rdatatype.CAA:
        record["flags"] = getattr(rdata, "flags", 0)
        tag = getattr(rdata, "tag", "")
        record["tag"] = tag.decode("utf-8", "replace") if isinstance(tag, bytes) else str(tag)
        val = getattr(rdata, "value", "")
        record["value"] = val.decode("utf-8", "replace") if isinstance(val, bytes) else str(val)
    elif rdtype == dns.rdatatype.SRV:
        record["priority"] = getattr(rdata, "priority", 0)
        record["weight"] = getattr(rdata, "weight", 0)
        record["port"] = getattr(rdata, "port", 0)
        record["target"] = rdata.target.to_text() if hasattr(rdata, "target") else raw_data
    elif rdtype in (dns.rdatatype.HTTPS, dns.rdatatype.SVCB):
        record["priority"] = getattr(rdata, "priority", 0)
        record["target"] = rdata.target.to_text() if hasattr(rdata, "target") else raw_data
        if hasattr(rdata, "params"):
            record["params"] = str(rdata.params)
    elif rdtype == dns.rdatatype.DNSKEY:
        record["flags"] = getattr(rdata, "flags", 0)
        record["protocol"] = getattr(rdata, "protocol", 0)
        record["algorithm"] = getattr(rdata, "algorithm", 0)
        record["key"] = str(getattr(rdata, "key", raw_data))
    elif rdtype == dns.rdatatype.DS:
        record["key_tag"] = getattr(rdata, "key_tag", 0)
        record["algorithm"] = getattr(rdata, "algorithm", 0)
        record["digest_type"] = getattr(rdata, "digest_type", 0)
        digest = getattr(rdata, "digest", raw_data)
        record["digest"] = digest.hex() if hasattr(digest, "hex") else str(digest)
    elif rdtype == dns.rdatatype.RRSIG:
        type_cov = getattr(rdata, "type_covered", None)
        record["type_covered"] = dns.rdatatype.to_text(type_cov) if type_cov is not None else "ANY"
        record["algorithm"] = getattr(rdata, "algorithm", 0)
        record["labels"] = getattr(rdata, "labels", 0)
        record["original_ttl"] = getattr(rdata, "original_ttl", 0)
        record["expiration"] = getattr(rdata, "expiration", 0)
        record["inception"] = getattr(rdata, "inception", 0)
        record["key_tag"] = getattr(rdata, "key_tag", 0)
        signer = getattr(rdata, "signer", None)
        record["signer"] = signer.to_text() if hasattr(signer, "to_text") else str(signer)
        record["signature"] = str(getattr(rdata, "signature", ""))

    return record


def _parse_resolver_answer(answer: dns.resolver.Answer) -> list[dict[str, Any]]:
    """Parse a :class:`dns.resolver.Answer` into record dicts."""
    parsed_records: list[dict[str, Any]] = []
    qname = answer.qname.to_text() if hasattr(answer, "qname") else ""
    default_ttl = (
        answer.response.answer[0].ttl
        if (hasattr(answer, "response") and answer.response and answer.response.answer)
        else 0
    )
    for rdata in answer:
        ttl = getattr(rdata, "ttl", default_ttl)
        rdclass = getattr(rdata, "rdclass", dns.rdataclass.IN)
        record = _parse_single_rdata(qname, ttl, rdclass, rdata)
        parsed_records.append(record)
    return parsed_records


def _parse_message_answer(
    response: dns.message.Message, query_name_str: str, record_type_str: str
) -> list[dict[str, Any]]:
    """Parse a :class:`dns.message.Message` (from DoH or DoT) into record dicts."""
    rcode = response.rcode()
    if rcode == dns.rcode.NXDOMAIN:
        raise DnsNxDomainError(f"Domain not found: {query_name_str}")
    if rcode != dns.rcode.NOERROR:
        rcode_text = dns.rcode.to_text(rcode)
        raise DnsGenericError(f"DNS error ({rcode_text}) for {query_name_str}")

    if not response.answer:
        raise DnsNoAnswerError(
            f"No {record_type_str} records found for {query_name_str}, though the name exists."
        )

    parsed_records: list[dict[str, Any]] = []
    for rrset in response.answer:
        name = rrset.name.to_text()
        ttl = rrset.ttl
        rdclass = rrset.rdclass
        for rdata in rrset:
            record = _parse_single_rdata(name, ttl, rdclass, rdata)
            parsed_records.append(record)
    return parsed_records


class DnsResolverClient:
    """Encapsulates DNS lookup logic with support for Standard DNS, DoH, and DoT."""

    DOH_PRESETS = DOH_PRESETS
    DOT_PRESETS = DOT_PRESETS

    def __init__(
        self,
        custom_dns_server: Optional[str] = None,
        resolver_mode: str = MODE_STANDARD,
        endpoint: Optional[str] = None,
    ):
        """Initialize :class:`DnsResolverClient`.

        :param custom_dns_server: Optional IP address or URL of a custom DNS server.
        :param resolver_mode: Mode of resolution ("standard", "doh", or "dot").
        :param endpoint: Target DoH URL or DoT hostname/IP when mode is doh/dot.
        """
        self.resolver: dns.resolver.Resolver = dns.resolver.Resolver()
        self.resolver_mode: str = resolver_mode
        self.endpoint: Optional[str] = endpoint
        self.custom_dns_server: Optional[str] = custom_dns_server

        if custom_dns_server:
            if custom_dns_server.startswith("https://") or custom_dns_server.startswith("http://"):
                self.resolver_mode = MODE_DOH
                self.endpoint = custom_dns_server
            elif custom_dns_server.startswith("tls://"):
                self.resolver_mode = MODE_DOT
                self.endpoint = custom_dns_server.removeprefix("tls://")
            else:
                self.resolver.nameservers = [custom_dns_server]

        self.resolver.timeout = 2.0
        self.resolver.lifetime = 2.0

    @property
    def nameservers(self) -> list[str]:
        """Return nameservers or endpoints used for lookups."""
        return self.resolver.nameservers

    def _normalize_query_target(self, domain_or_ip: str, record_type: str) -> str:
        """Handle PTR queries for IP addresses by converting to reverse DNS notation."""
        if record_type.upper() == "PTR":
            try:
                ipaddress.ip_address(domain_or_ip)
                return str(dns.reversename.from_address(domain_or_ip))
            except (ValueError, dns.exception.SyntaxError, TypeError):
                return domain_or_ip
        return domain_or_ip

    def _lookup_record_internal(self, query_name_str: str, record_type_str: str) -> list[dict[str, Any]]:
        """Look up and parse DNS records synchronously.

        :param query_name_str: Domain name or reverse IP to query.
        :param record_type_str: DNS record type string.
        """
        if self.resolver_mode == MODE_STANDARD:
            try:
                answer: dns.resolver.Answer = self.resolver.resolve(query_name_str, record_type_str)
                return _parse_resolver_answer(answer)
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
        elif self.resolver_mode == MODE_DOH:
            try:
                query = dns.message.make_query(query_name_str, record_type_str)
                endpoint = self.endpoint or DOH_PRESETS["Cloudflare"]
                response = dns.query.https(query, endpoint, timeout=2.0)
                return _parse_message_answer(response, query_name_str, record_type_str)
            except (DnsNxDomainError, DnsNoAnswerError, DnsGenericError):
                raise
            except dns.exception.Timeout as e:
                raise DnsResolutionTimeoutError(f"DNS query timed out for {query_name_str}") from e
            except Exception as e:
                raise DnsGenericError(f"DoH query failed for {query_name_str}: {e}") from e
        elif self.resolver_mode == MODE_DOT:
            try:
                query = dns.message.make_query(query_name_str, record_type_str)
                endpoint = self.endpoint or DOT_PRESETS["Cloudflare"]
                response = dns.query.tls(query, endpoint, port=853, timeout=2.0)
                return _parse_message_answer(response, query_name_str, record_type_str)
            except (DnsNxDomainError, DnsNoAnswerError, DnsGenericError):
                raise
            except dns.exception.Timeout as e:
                raise DnsResolutionTimeoutError(f"DNS query timed out for {query_name_str}") from e
            except Exception as e:
                raise DnsGenericError(f"DoT query failed for {query_name_str}: {e}") from e
        else:
            raise DnsGenericError(f"Unsupported resolver mode: {self.resolver_mode}")

    async def _resolve_any_parallel(self, query_target: str) -> list[dict[str, Any]]:
        """Perform concurrent lookups across multiple record types."""
        async def _query_single(rtype: str) -> list[dict[str, Any]]:
            try:
                if self.resolver_mode == MODE_DOH:
                    q = dns.message.make_query(query_target, rtype)
                    endpoint = self.endpoint or DOH_PRESETS["Cloudflare"]
                    resp = await dns.asyncquery.https(q, endpoint, timeout=2.0)
                    return _parse_message_answer(resp, query_target, rtype)
                elif self.resolver_mode == MODE_DOT:
                    q = dns.message.make_query(query_target, rtype)
                    endpoint = self.endpoint or DOT_PRESETS["Cloudflare"]
                    resp = await dns.asyncquery.tls(q, endpoint, port=853, timeout=2.0)
                    return _parse_message_answer(resp, query_target, rtype)
                else:
                    async_resolver = dns.asyncresolver.Resolver()
                    if self.custom_dns_server:
                        async_resolver.nameservers = [self.custom_dns_server]
                    elif self.resolver.nameservers:
                        async_resolver.nameservers = self.resolver.nameservers
                    async_resolver.timeout = 2.0
                    async_resolver.lifetime = 2.0
                    ans = await async_resolver.resolve(query_target, rtype)
                    return _parse_resolver_answer(ans)
            except Exception:
                return []

        results = await asyncio.gather(*(_query_single(t) for t in ANY_RECORD_TYPES))
        combined: list[dict[str, Any]] = []
        seen = set()
        for res_list in results:
            for rec in res_list:
                key = (rec.get("name"), rec.get("type"), rec.get("data"))
                if key not in seen:
                    seen.add(key)
                    combined.append(rec)

        if not combined:
            raise DnsNoAnswerError(f"No records found for {query_target} across all queried types.")
        return combined

    async def resolve_async(self, domain_or_ip: str, record_type: str) -> list[dict[str, Any]]:
        """Asynchronously resolve DNS records.

        :param domain_or_ip: Domain or IP to query.
        :param record_type: Record type string (e.g., "A", "HTTPS", "ANY").
        """
        query_target = self._normalize_query_target(domain_or_ip, record_type)
        record_type_upper = record_type.upper()

        if record_type_upper == "ANY":
            return await self._resolve_any_parallel(query_target)

        if self.resolver_mode == MODE_DOH:
            try:
                query = dns.message.make_query(query_target, record_type_upper)
                endpoint = self.endpoint or DOH_PRESETS["Cloudflare"]
                response = await dns.asyncquery.https(query, endpoint, timeout=2.0)
                return _parse_message_answer(response, query_target, record_type_upper)
            except (DnsNxDomainError, DnsNoAnswerError, DnsGenericError):
                raise
            except dns.exception.Timeout as e:
                raise DnsResolutionTimeoutError(f"DNS query timed out for {query_target}") from e
            except Exception as e:
                raise DnsGenericError(f"DoH query failed for {query_target}: {e}") from e

        elif self.resolver_mode == MODE_DOT:
            try:
                query = dns.message.make_query(query_target, record_type_upper)
                endpoint = self.endpoint or DOT_PRESETS["Cloudflare"]
                response = await dns.asyncquery.tls(query, endpoint, port=853, timeout=2.0)
                return _parse_message_answer(response, query_target, record_type_upper)
            except (DnsNxDomainError, DnsNoAnswerError, DnsGenericError):
                raise
            except dns.exception.Timeout as e:
                raise DnsResolutionTimeoutError(f"DNS query timed out for {query_target}") from e
            except Exception as e:
                raise DnsGenericError(f"DoT query failed for {query_target}: {e}") from e

        else:
            async_resolver = dns.asyncresolver.Resolver()
            if self.custom_dns_server:
                async_resolver.nameservers = [self.custom_dns_server]
            elif self.resolver.nameservers:
                async_resolver.nameservers = self.resolver.nameservers
            async_resolver.timeout = 2.0
            async_resolver.lifetime = 2.0
            try:
                answer = await async_resolver.resolve(query_target, record_type_upper)
                return _parse_resolver_answer(answer)
            except dns.resolver.NXDOMAIN as e:
                raise DnsNxDomainError(f"Domain not found: {query_target}") from e
            except dns.resolver.NoAnswer as e:
                raise DnsNoAnswerError(
                    f"No {record_type_upper} records found for {query_target}, though the name exists."
                ) from e
            except dns.resolver.Timeout as e:
                raise DnsResolutionTimeoutError(f"DNS query timed out for {query_target}") from e
            except dns.exception.DNSException as e:
                raise DnsGenericError(f"DNS error for {query_target}: {e}") from e

    def resolve(self, domain_or_ip: str, record_type: str) -> list[dict[str, Any]]:
        """Resolve DNS records for a domain/IP and record type.

        :param domain_or_ip: Domain name or IP address to query.
        :param record_type: DNS record type string.
        """
        record_type_upper = record_type.upper()
        if record_type_upper != "ANY":
            query_target = self._normalize_query_target(domain_or_ip, record_type)
            return self._lookup_record_internal(query_target, record_type_upper)

        # For async pipelines (DoH, DoT, parallel ANY), execute safely even if inside thread
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, self.resolve_async(domain_or_ip, record_type)).result()
        return asyncio.run(self.resolve_async(domain_or_ip, record_type))
