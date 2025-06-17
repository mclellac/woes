"""
Custom HTTPAdapter for requests with custom DNS and Server Name Indication (SNI).

This module provides :class:`CustomDNSAdapter`, a specialized version of
:class:`requests.adapters.HTTPAdapter`. It allows for custom DNS resolution
by specifying a DNS server and enhances HTTPS connections by managing SNI,
particularly when requests are made to IP addresses.
"""

import logging
import socket
import ssl
from typing import Optional, Any, Dict, List, Tuple # Using Any for *args, **kwargs as per base class

import requests
import requests.utils  # For urlparse, urlunparse
from requests.adapters import HTTPAdapter

try:
    import dns.resolver
    import dns.exception

    DNSPYTHON_AVAILABLE = True
except ImportError:
    dns = None  # Make `dns` module explicitly None if not importable
    DNSPYTHON_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "dnspython library not found. Custom DNS functionality will be disabled in CustomDNSAdapter."
    )

logger = logging.getLogger(__name__)


class CustomDNSAdapter(HTTPAdapter):
    """
    A requests HTTPAdapter that allows for custom DNS resolution and SNI handling.

    This adapter can resolve hostnames using a specified DNS server (if the
    `dnspython` library is available). It also correctly sets the Server Name
    Indication (SNI) for HTTPS connections, which is crucial when the request URL
    directly uses an IP address, in which case a `default_sni` (typically derived
    from the original 'Host' header) can be provided for certificate validation.

    The adapter caches resolved IPs for the lifetime of the adapter instance to
    avoid repeated DNS lookups for the same hostname.
    """

    def __init__(
        self,
        *args: Any,
        custom_dns_server: Optional[str] = None,
        default_sni: Optional[str] = None,
        **kwargs: Any,
    ):
        """
        Initialize the CustomDNSAdapter.

        :param custom_dns_server: The IP address of the custom DNS server to use.
                                  If ``None`` or if `dnspython` is unavailable,
                                  system DNS resolution will be used.
        :param default_sni: The hostname to use for SNI and SSL certificate validation
                            when the request URL is an IP address. This is typically
                            the value from the original 'Host' header.
        :param args: Positional arguments to pass to the parent :class:`requests.adapters.HTTPAdapter`.
        :param kwargs: Keyword arguments to pass to the parent :class:`requests.adapters.HTTPAdapter`.
        """
        self.custom_dns_server: Optional[str] = custom_dns_server
        self.default_sni_for_ip_url: Optional[str] = default_sni
        self._resolved_sni: Optional[str] = None #: Stores the SNI to be used, derived from resolved hostname.
        self.resolved_ip_cache: Dict[str, str] = {} #: Cache for resolved hostnames to IP addresses.
        super().__init__(*args, **kwargs)

    def _resolve_hostname_to_ip(self, hostname: str) -> Optional[str]:
        """
        Resolve a given hostname to an IP address using the custom DNS server.

        It attempts to resolve AAAA records (IPv6) first, then A records (IPv4).
        If resolution is successful, the first IP address from the results is returned.
        The result is not cached at this level; caching is handled by `resolved_ip_cache`
        at the adapter instance level.

        :param hostname: The hostname to resolve.
        :return: The resolved IP address (IPv6 preferred if available, otherwise IPv4),
                 or ``None`` if resolution fails, custom DNS is not configured,
                 or `dnspython` is unavailable.
        """
        if not self.custom_dns_server:
            logger.debug(
                "CustomDNSAdapter: Skipping custom DNS resolution for '%s' (no custom DNS server specified).",
                hostname,
            )
            return None
        if not DNSPYTHON_AVAILABLE: # Check the flag
            logger.debug(
                "CustomDNSAdapter: Skipping custom DNS resolution for '%s' (dnspython library not available).",
                hostname,
            )
            return None

        logger.info(
            "CustomDNSAdapter: Attempting to resolve '%s' using DNS server %s", hostname, self.custom_dns_server
        )
        resolver = dns.resolver.Resolver()
        resolver.nameservers = [self.custom_dns_server]
        # Short timeouts to prevent long hangs if DNS server is unresponsive
        resolver.timeout = 2.0
        resolver.lifetime = 2.0

        ips: List[str] = []
        try:
            # Prefer AAAA (IPv6) if available
            for rdtype in ("AAAA", "A"):
                try:
                    answers = resolver.resolve(hostname, rdtype)
                    for rdata in answers:
                        ips.append(rdata.address)
                    if ips:  # Found records of the current type
                        break
                except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
                    logger.debug(
                        "CustomDNSAdapter: No %s records found for '%s' via %s.",
                        rdtype,
                        hostname,
                        self.custom_dns_server,
                    )
                except dns.exception.Timeout:
                    logger.warning(
                        "CustomDNSAdapter: DNS query for %s record of '%s' via %s timed out.",
                        rdtype,
                        hostname,
                        self.custom_dns_server,
                    )
                except dns.exception.DNSException as e:
                    logger.warning(
                        "CustomDNSAdapter: DNS %s query for '%s' via %s failed: %s",
                        rdtype,
                        hostname,
                        self.custom_dns_server,
                        e,
                    )
            if ips:
                selected_ip = ips[0] # Prefer the first resolved IP (AAAA if any, else A)
                logger.info(
                    "CustomDNSAdapter: Resolved '%s' to %s (using %s).", hostname, selected_ip, self.custom_dns_server
                )
                return selected_ip
            logger.warning(
                "CustomDNSAdapter: No AAAA or A records found for '%s' via %s after trying both.",
                hostname,
                self.custom_dns_server,
            )
        except Exception as e: # Catch-all for unexpected errors during DNS resolution process
            logger.error(
                "CustomDNSAdapter: Unexpected error during custom DNS resolution for '%s': %s",
                hostname,
                e,
                exc_info=True,
            )
        return None

    def send(
        self,
        request: requests.models.PreparedRequest,
        stream: bool = False,
        timeout: Optional[float | Tuple[float, float]] = None,
        verify: bool | str = True, # More precise type for verify
        cert: Optional[str | Tuple[str, str]] = None, # More precise type for cert
        proxies: Optional[Dict[str, str]] = None,
    ) -> requests.Response:  # type: ignore[override]
        """
        Send a prepared request, potentially with custom DNS resolution and SNI.

        This method overrides the parent's `send` method. If a custom DNS server
        is configured and `dnspython` is available, it attempts to resolve the
        hostname in the request URL. If successful, the resolved IP is cached,
        and the original hostname is stored for SNI purposes (`_resolved_sni`).
        The actual connection to the IP address happens in `get_connection`,
        and SNI is configured in `init_poolmanager`.

        :param request: The :class:`requests.PreparedRequest` to send.
        :param stream: If ``True``, the response content will not be immediately downloaded.
        :param timeout: How long to wait for the server to send data before giving up,
                        as a float, or a :py:obj:`tuple` (connect timeout, read timeout).
        :param verify: Either a boolean, in which case it controls whether to verify
                       the server's TLS certificate, or a string, in which case it must
                       be a path to a CA bundle to use.
        :param cert: Any CA certificate file or directory of CA certificate files to use for HTTPS requests.
                     Can be a path to a single file (containing the private key and the certificate) or a
                     :py:obj:`tuple` of (certificate file, private key file) paths.
        :param proxies: Dictionary mapping protocol to the URL of the proxy.
        :return: The :class:`requests.Response` object.
        :raises requests.exceptions.RequestException: For various request errors.
        """
        parsed_url = requests.utils.urlparse(request.url)  # type: ignore[attr-defined]
        original_hostname = parsed_url.hostname
        self._resolved_sni = None  # Reset SNI for each new request

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

        if original_hostname and not is_original_hostname_ip:
            # Only attempt custom DNS if hostname is not an IP
            resolved_ip = self.resolved_ip_cache.get(original_hostname)
            if not resolved_ip: # Not in cache, try to resolve
                 resolved_ip = self._resolve_hostname_to_ip(original_hostname)

            if resolved_ip:
                self._resolved_sni = original_hostname # SNI should be the original hostname
                self.resolved_ip_cache[original_hostname] = resolved_ip # Cache it
                logger.info(
                    "CustomDNSAdapter.send: Original hostname '%s' resolved to IP %s. SNI will be: '%s'",
                    original_hostname,
                    resolved_ip,
                    self._resolved_sni,
                )
            # If not resolved_ip, it will proceed without changing the request URL's host part
            # and self._resolved_sni remains None. Standard DNS resolution will occur later.

        # If the original URL was an IP, and default_sni is set, that will be used by init_poolmanager
        elif is_original_hostname_ip and self.default_sni_for_ip_url:
            self._resolved_sni = self.default_sni_for_ip_url
            logger.info(
                "CustomDNSAdapter.send: Original URL uses IP '%s'. Using default_sni '%s' for SNI.",
                original_hostname, self._resolved_sni
            )


        return super().send(request, stream, timeout, verify, cert, proxies)  # type: ignore[return-value]

    def get_connection(self, url: str, proxies: Optional[Dict[str, str]] = None) -> Any:  # type: ignore[override]
        """
        Get a connection from the connection pool for the given URL.

        If the hostname in the URL was previously resolved to an IP address using
        custom DNS, this method modifies the URL to use the resolved IP address
        for the connection, while the original hostname is used for SNI (handled
        in `init_poolmanager` via `_resolved_sni`).

        :param url: The URL to establish a connection for.
        :param proxies: A dictionary of proxy URLs.
        :return: A connection from the pool.
        :rtype: urllib3.connectionpool.HTTPConnectionPool or urllib3.connectionpool.HTTPSConnectionPool
        """
        parsed_url = requests.utils.urlparse(url)  # type: ignore[attr-defined]
        original_hostname = parsed_url.hostname

        # Check if we have a cached, custom-resolved IP for this hostname
        resolved_ip_for_connection = self.resolved_ip_cache.get(original_hostname) if original_hostname else None

        if resolved_ip_for_connection:
            logger.info(
                "CustomDNSAdapter.get_connection: Using resolved IP %s for connection to original host '%s' (URL: %s)",
                resolved_ip_for_connection,
                original_hostname,
                url,
            )

            # Reconstruct the URL, replacing hostname with the resolved IP
            conn_url_parts = list(parsed_url[:])  # Make a mutable copy of parsed components
            new_netloc = resolved_ip_for_connection
            if parsed_url.port:
                new_netloc += f":{parsed_url.port}"
            conn_url_parts[1] = new_netloc  # Replace netloc with IP:port

            # Ensure scheme is present if it was missing (though urlparse usually fills it)
            if not conn_url_parts[0]:
                conn_url_parts[0] = "https" if parsed_url.scheme == "https" else "http"

            connection_target_url = requests.utils.urlunparse(conn_url_parts)  # type: ignore[attr-defined]

            logger.debug(
                "CustomDNSAdapter.get_connection: PoolManager will connect to IP-based URL: %s (SNI via _resolved_sni: %s)",
                connection_target_url,
                self._resolved_sni, # This will be used by init_poolmanager
            )
            return self.poolmanager.connection_from_url(connection_target_url) # type: ignore[no-any-return]
        else:
            logger.debug(
                "CustomDNSAdapter.get_connection: Proceeding with default connection for URL: %s. "
                "Original hostname '%s' not in resolved IP cache or custom DNS not used.",
                url, original_hostname
            )
            return super().get_connection(url, proxies=proxies) # type: ignore[no-any-return]

    def init_poolmanager(self, connections: int, maxsize: int, block: bool = False, **pool_kwargs: Any) -> None:  # type: ignore[override]
        """
        Initialize the `urllib3.PoolManager` with custom SNI and certificate validation settings.

        This method is called by `requests` to set up the connection pool.
        If `_resolved_sni` (from a custom DNS resolution) or `default_sni_for_ip_url`
        (for direct IP requests) is available, it's used to configure `server_hostname`
        (for SNI) and `assert_hostname` (for certificate validation) in the pool's
        connection arguments.

        :param connections: The number of urllib3 connection pools to cache.
        :param maxsize: The maximum number of connections to save in the pool.
        :param block: Whether the pool should block for connections.
        :param pool_kwargs: Extra keyword arguments for PoolManager and connection initialization.
        """
        sni_hostname_for_pool: Optional[str] = None

        if self._resolved_sni: # This is set in send() if custom DNS was used or if URL was IP + default_sni
            sni_hostname_for_pool = self._resolved_sni
            logger.info(
                "CustomDNSAdapter.init_poolmanager: Using SNI/assert_hostname from _resolved_sni: '%s'",
                sni_hostname_for_pool,
            )
        # Note: default_sni_for_ip_url is now handled by setting _resolved_sni in send()
        # This simplifies logic here. If _resolved_sni is None, no specific SNI override is done here.

        if sni_hostname_for_pool:
            # These kwargs are passed down to the HTTPSConnectionPool
            pool_kwargs["assert_hostname"] = sni_hostname_for_pool
            pool_kwargs["server_hostname"] = sni_hostname_for_pool # For SNI
            # Ensure certificate validation is enabled when overriding hostname
            if pool_kwargs.get("cert_reqs") is None: # Don't override if user explicitly set it
                 pool_kwargs["cert_reqs"] = ssl.CERT_REQUIRED
            logger.debug(
                "CustomDNSAdapter.init_poolmanager: Pool configured with SNI/assert_hostname: '%s', cert_reqs: '%s'",
                sni_hostname_for_pool, pool_kwargs.get("cert_reqs")
            )
        else:
            logger.debug(
                "CustomDNSAdapter.init_poolmanager: No specific SNI/assert_hostname configuration for this pool from adapter."
            )

        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)
