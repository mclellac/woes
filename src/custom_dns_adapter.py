"""
Custom HTTPAdapter for requests with custom DNS and SNI.

Provides a :class:`requests.adapters.HTTPAdapter` for custom DNS resolution
and Server Name Indication (SNI) for HTTPS connections.
"""

import logging
import socket
import ssl
from typing import Optional, Tuple, Any  # list and dict will be used directly

import requests
import requests.utils  # For urlparse, urlunparse
from requests.adapters import HTTPAdapter

try:
    import dns.resolver
    import dns.exception
except ImportError:
    dns = None
    logging.getLogger(__name__).warning(
        "dnspython library not found. Custom DNS functionality will be disabled in CustomDNSAdapter."
    )

logger = logging.getLogger(__name__)


class CustomDNSAdapter(HTTPAdapter):
    """
    Custom requests.adapters.HTTPAdapter for DNS and SNI.

    Enables custom DNS resolution and SNI handling. Resolves hostnames using
    a specified DNS server (if dnspython is available) and configures SNI
    for HTTPS connections. If the request URL is an IP, `default_sni` (from
    Host header) can be used. Sets `assert_hostname` for urllib3's connection
    pool for SSL certificate validation. Falls back to standard adapter behavior
    if custom DNS is not used or resolution fails.
    """

    def __init__(
        self, *args: Any, custom_dns_server: Optional[str] = None, default_sni: Optional[str] = None, **kwargs: Any
    ):
        """
        Initialize the CustomDNSAdapter.

        :param args: Positional arguments for :class:`requests.adapters.HTTPAdapter`.
        :type args: Any
        :param custom_dns_server: IP of the custom DNS server. Uses system DNS if None or dnspython is unavailable.
        :type custom_dns_server: str, optional
        :param default_sni: Hostname for SNI/certificate validation if URL is an IP. Typically from Host header.
        :type default_sni: str, optional
        :param kwargs: Keyword arguments for :class:`requests.adapters.HTTPAdapter`.
        :type kwargs: Any
        :rtype: None
        """
        self.custom_dns_server: Optional[str] = custom_dns_server
        self.default_sni_for_ip_url: Optional[str] = default_sni
        self._resolved_sni: Optional[str] = None
        self.resolved_ip_cache: dict[str, str] = {}
        super().__init__(*args, **kwargs)

    def _resolve_hostname_to_ip(self, hostname: str) -> Optional[str]:
        """
        Resolve a hostname using the custom DNS server.

        Attempts AAAA records first, then A.

        :param hostname: The hostname to resolve.
        :type hostname: str
        :return: Resolved IP address (IPv6 preferred) or None if resolution fails,
                 custom DNS is not configured, or dnspython is unavailable.
        :rtype: str, optional
        """
        if not self.custom_dns_server or not dns:
            logger.debug(
                "CustomDNSAdapter: Skipping custom DNS (server: %s, dnspython available: %s) for %s.",
                self.custom_dns_server,
                bool(dns),
                hostname,
            )
            return None

        logger.info(
            "CustomDNSAdapter: Attempting to resolve '%s' using DNS server %s", hostname, self.custom_dns_server
        )
        resolver = dns.resolver.Resolver()
        resolver.nameservers = [self.custom_dns_server]
        resolver.timeout = 2.0
        resolver.lifetime = 2.0

        ips: list[str] = []
        try:
            for rdtype in ("AAAA", "A"):
                try:
                    answers = resolver.resolve(hostname, rdtype)
                    for rdata in answers:
                        ips.append(rdata.address)
                    if ips:
                        break
                except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
                    logger.debug(
                        "CustomDNSAdapter: No %s records found for %s via %s.", rdtype, hostname, self.custom_dns_server
                    )
                except dns.exception.DNSException as e:
                    logger.warning(
                        "CustomDNSAdapter: DNS %s query for %s via %s failed: %s",
                        rdtype,
                        hostname,
                        self.custom_dns_server,
                        e,
                    )

            if ips:
                selected_ip = ips[0]
                logger.info(
                    "CustomDNSAdapter: Resolved '%s' to %s (using %s).", hostname, selected_ip, self.custom_dns_server
                )
                return selected_ip
            logger.warning(
                "CustomDNSAdapter: No AAAA or A records found for %s via %s.", hostname, self.custom_dns_server
            )
        except Exception as e:
            logger.error(
                "CustomDNSAdapter: Unexpected error during custom DNS resolution for %s: %s", hostname, e, exc_info=True
            )
        return None

    def send(
        self,
        request: requests.models.PreparedRequest,
        stream: bool = False,
        timeout: Optional[float | Tuple[float, float]] = None,
        verify: bool = True,
        cert: Optional[Tuple[str, str] | str] = None,
        proxies: Optional[dict[str, str]] = None,
    ) -> requests.Response:  # type: ignore[override]
        """
        Send a prepared request.

        Handles sending a request, potentially using custom DNS to modify the
        destination IP and configuring SNI. Overrides
        :meth:`requests.adapters.HTTPAdapter.send`.

        :param request: The :class:`requests.PreparedRequest` to send.
        :type request: requests.models.PreparedRequest
        :param stream: Whether to stream the request content, defaults to False.
        :type stream: bool, optional
        :param timeout: Timeout for the request (float or tuple), defaults to None.
        :type timeout: float or tuple[float, float], optional
        :param verify: Whether to verify TLS certificate (bool or path to CA bundle), defaults to True.
        :type verify: bool or str, optional
        :param cert: Path to SSL certificate (single file or tuple of cert/key), defaults to None.
        :type cert: tuple[str, str] or str, optional
        :param proxies: Proxies dictionary for the request, defaults to None.
        :type proxies: dict[str, str], optional
        :return: The :class:`requests.Response` object.
        :rtype: requests.Response
        """
        parsed_url = requests.utils.urlparse(request.url)  # type: ignore[attr-defined]
        original_hostname = parsed_url.hostname
        self._resolved_sni = None

        is_original_hostname_ip: bool = False
        if original_hostname:
            try:
                socket.inet_pton(socket.AF_INET, original_hostname)
                is_original_hostname_ip = True
            except socket.error:
                try:
                    socket.inet_pton(socket.AF_INET6, original_hostname)
                    is_original_hostname_ip = True
                except socket.error:
                    is_original_hostname_ip = False

        if original_hostname and not is_original_hostname_ip and self.custom_dns_server and dns:
            resolved_ip = self._resolve_hostname_to_ip(original_hostname)
            if resolved_ip:
                self._resolved_sni = original_hostname
                self.resolved_ip_cache[original_hostname] = resolved_ip
                logger.info(
                    "CustomDNSAdapter.send: Original request URL %s will be used. Connection will target resolved IP %s (SNI will be: %s)",  # type: ignore[str-format]
                    request.url,
                    resolved_ip,
                    self._resolved_sni,
                )

        return super().send(request, stream, timeout, verify, cert, proxies)  # type: ignore[return-value]

    def get_connection(self, url: str, proxies: Optional[dict[str, str]] = None) -> Any:  # type: ignore[override]
        """
        Override :meth:`requests.adapters.HTTPAdapter.get_connection` for custom IP.

        :param url: The URL to connect to.
        :type url: str
        :param proxies: Proxies configuration.
        :type proxies: dict[str, str], optional
        :return: The connection object from urllib3.
        :rtype: urllib3.connectionpool.HTTPConnectionPool or urllib3.connectionpool.HTTPSConnectionPool
        """
        parsed_url = requests.utils.urlparse(url)  # type: ignore[attr-defined]
        original_hostname = parsed_url.hostname

        resolved_ip_for_connection: Optional[str] = None
        if self.custom_dns_server and dns and original_hostname and original_hostname in self.resolved_ip_cache:
            resolved_ip_for_connection = self.resolved_ip_cache[original_hostname]

        if resolved_ip_for_connection:
            logger.info(
                "CustomDNSAdapter.get_connection: Using resolved IP %s for connection to original host %s (URL: %s)",  # type: ignore[str-format]
                resolved_ip_for_connection,
                original_hostname,
                url,
            )

            conn_url_parts = list(parsed_url[:])  # Make a mutable copy
            new_netloc = resolved_ip_for_connection
            if parsed_url.port:
                new_netloc += f":{parsed_url.port}"
            conn_url_parts[1] = new_netloc

            if not conn_url_parts[0]:
                conn_url_parts[0] = "https" if parsed_url.scheme == "https" else "http"

            connection_target_url = requests.utils.urlunparse(conn_url_parts)  # type: ignore[attr-defined]

            logger.debug(
                "CustomDNSAdapter.get_connection: PoolManager will connect to IP-based URL: %s (SNI via _resolved_sni: %s)",  # type: ignore[str-format]
                connection_target_url,
                self._resolved_sni,
            )
            return self.poolmanager.connection_from_url(connection_target_url)  # type: ignore[no-any-return]
        else:
            logger.debug(
                "CustomDNSAdapter.get_connection: Proceeding with default connection for URL: %s. Cache miss or custom DNS not used for this host.",
                url,
            )
            return super().get_connection(url, proxies=proxies)  # type: ignore[no-any-return]

    def init_poolmanager(self, connections: int, maxsize: int, block: bool = False, **pool_kwargs: Any) -> None:  # type: ignore[override]
        """
        Initialize `urllib3.PoolManager` with SNI and cert validation.

        :param connections: Number of urllib3 connection pools to cache.
        :type connections: int
        :param maxsize: Maximum number of connections to save in the pool.
        :type maxsize: int
        :param block: Whether the pool should block for connections, defaults to False.
        :type block: bool, optional
        :param pool_kwargs: Extra keyword arguments for PoolManager initialization.
        :type pool_kwargs: Any
        :rtype: None
        """
        sni_hostname_for_pool: Optional[str] = None
        if self._resolved_sni:
            sni_hostname_for_pool = self._resolved_sni
            logger.info(
                "CustomDNSAdapter.init_poolmanager: Using SNI/assert_hostname from resolved hostname: %s",
                sni_hostname_for_pool,
            )
        elif self.default_sni_for_ip_url:
            sni_hostname_for_pool = self.default_sni_for_ip_url
            logger.info("CustomDNSAdapter.init_poolmanager: Using default SNI for IP URL: %s", sni_hostname_for_pool)

        if sni_hostname_for_pool:
            pool_kwargs["assert_hostname"] = sni_hostname_for_pool
            pool_kwargs["server_hostname"] = sni_hostname_for_pool
            pool_kwargs["cert_reqs"] = ssl.CERT_REQUIRED
            logger.debug(
                "CustomDNSAdapter.init_poolmanager: Pool configured with SNI/assert_hostname: %s", sni_hostname_for_pool
            )
        else:
            logger.debug(
                "CustomDNSAdapter.init_poolmanager: No specific SNI/assert_hostname configuration for this pool."
            )

        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)
