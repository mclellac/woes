"""Provides a custom HTTPAdapter for the 'requests' library to enable
fine-grained control over DNS resolution and Server Name Indication (SNI)
for HTTP/HTTPS requests.

This is particularly useful for scenarios where DNS lookups need to bypass
system defaults or when targeting IP addresses directly while needing correct
SNI for TLS handshakes.
"""

import logging
import socket
import ssl
from typing import Optional, Tuple, List, Dict, Any # Added Any for type hints

import requests
import requests.utils  # For urlparse, urlunparse
from requests.adapters import HTTPAdapter

try:
    import dns.resolver
    import dns.exception
    dnspython_available = True
except ImportError:
    dns = None # type: ignore # Assign None to dns if import fails for type checking
    dnspython_available = False
    logging.getLogger(__name__).warning(
        "The 'dnspython' library was not found. Custom DNS resolution "
        "functionality within CustomDNSAdapter will be disabled. System DNS "
        "will be used instead.")

logger = logging.getLogger(__name__)


class CustomDNSAdapter(HTTPAdapter):
    """A `requests` HTTPAdapter for custom DNS resolution and SNI control.

    This adapter allows overriding default DNS resolution by using specified
    DNS servers (via `dnspython`) and manually controlling the Server Name
    Indication (SNI) value for TLS handshakes. This is essential when:
    - Testing services hosted behind load balancers or CDNs by targeting specific backend IPs.
    - Forcing DNS resolution through a particular server for diagnostics.
    - Interacting with servers that host multiple TLS sites on a single IP address.

    Key behaviors:
    1.  **Custom DNS Resolution:** If `custom_dns_server` is provided and `dnspython`
        is available, hostnames are resolved using this server. The resolved IP
        is then used for the connection, while the original hostname is preserved
        for the 'Host' header and SNI.
    2.  **SNI Control:**
        - For hostname-based requests with custom DNS, SNI is set to the original hostname.
        - For IP-based request URLs, `default_sni_for_ip_url` (if provided) is used for SNI.
    3.  **Certificate Validation:** `assert_hostname` is configured for `urllib3`'s
        connection pool to validate the server certificate against the SNI hostname.

    If custom DNS resolution is not active (no server specified, `dnspython` missing,
    or resolution failure), the adapter falls back to standard system DNS resolution
    and default `requests` behavior for SNI.
    """

    def __init__(self, *args: Any, custom_dns_server: Optional[str] = None,
                 default_sni: Optional[str] = None, **kwargs: Any):
        """Initialize the CustomDNSAdapter.

        Args:
        ----
            *args: Positional arguments to pass to the parent `HTTPAdapter`.
            custom_dns_server: IP address of the custom DNS server. If None,
                               or if `dnspython` module is unavailable,
                               system DNS will be used.
            default_sni: The hostname to use for SNI and certificate validation
                         if the request URL is already an IP address. This is
                         typically derived from a user-provided 'Host' header.
            **kwargs: Keyword arguments to pass to the parent `HTTPAdapter`.
        """
        self.custom_dns_server = custom_dns_server
        self.default_sni_for_ip_url = default_sni
        # SNI derived from successful hostname resolution by this adapter.
        self._resolved_sni: Optional[str] = None
        # Cache for resolved IPs: hostname -> IP. Avoids repeated lookups.
        self.resolved_ip_cache: Dict[str, str] = {}
        super().__init__(*args, **kwargs)

    def _resolve_hostname_to_ip(self, hostname: str) -> Optional[str]:
        """Resolve a hostname to an IP address using the custom DNS server.

        Attempts to resolve AAAA (IPv6) records first, then A (IPv4) records.
        Uses short timeouts for DNS queries to avoid long hangs.

        Args:
        ----
            hostname: The hostname to resolve.

        Returns:
        -------
            The first resolved IP address (preferring IPv6 if available) as a string.
            Returns None if resolution fails, custom DNS is not configured,
            or `dnspython` is unavailable.
        """
        if not self.custom_dns_server or not dnspython_available: # Check dnspython_available
            logger.debug(
                "CustomDNSAdapter: Skipping custom DNS (server: %s, "
                "dnspython available: %s) for %s.",
                self.custom_dns_server, dnspython_available, hostname)
            return None

        logger.info(
            "CustomDNSAdapter: Attempting to resolve '%s' using DNS server %s",
            hostname, self.custom_dns_server)
        resolver = dns.resolver.Resolver() # type: ignore
        resolver.nameservers = [self.custom_dns_server]
        resolver.timeout = 2.0  # Timeout for each individual DNS query.
        # Total time allowed for the entire resolution attempt, including retries.
        resolver.lifetime = 2.0

        ips: List[str] = []
        try:
            # Prefer AAAA (IPv6) if available
            for rdtype in ("AAAA", "A"):
                try:
                    answers = resolver.resolve(hostname, rdtype)
                    for rdata in answers:
                        ips.append(rdata.address)
                    if ips:  # Found records of the current type, no need to try A if AAAA succeeded
                        break
                except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN): # type: ignore
                    logger.debug(
                        "CustomDNSAdapter: No %s records found for %s via %s.",
                        rdtype, hostname, self.custom_dns_server)
                # More specific exceptions like Timeout, YXDOMAIN, etc.
                except dns.exception.DNSException as e: # type: ignore
                    logger.warning(
                        "CustomDNSAdapter: DNS %s query for %s via %s "
                        "failed: %s",
                        rdtype, hostname, self.custom_dns_server, e)

            if ips:
                selected_ip = ips[0] # Prefer first resolved (AAAA if found, then A)
                logger.info(
                    "CustomDNSAdapter: Resolved '%s' to %s (using %s).",
                    hostname, selected_ip, self.custom_dns_server)
                return selected_ip
            logger.warning(
                "CustomDNSAdapter: No AAAA or A records found for %s via %s.",
                hostname, self.custom_dns_server)
        except Exception as e:  # Catch-all for other unexpected errors
            logger.error(
                "CustomDNSAdapter: Unexpected error during custom DNS "
                "resolution for %s: %s", hostname, e, exc_info=True)
        return None

    def send(self, request: requests.models.PreparedRequest,
             stream: bool = False, timeout: Optional[float] = None, # type: ignore[override]
             verify: bool = True,
             cert: Optional[Tuple[str, str] | str] = None,
             proxies: Optional[Dict[str, str]] = None) -> requests.models.Response:
        """Prepare and send a request with custom DNS resolution logic.

        This method is the primary entry point for `requests` to use this adapter.
        It attempts to resolve the request's hostname using the custom DNS server
        (if configured and `dnspython` is available). If successful, it caches
        the resolved IP and sets `_resolved_sni` to the original hostname.
        This SNI value will be used by `init_poolmanager` to configure the
        connection pool for correct TLS handshake and certificate validation.
        The actual connection to the resolved IP (if any) happens within
        `get_connection`.

        Args:
        ----
            request: The `PreparedRequest` object to send.
            stream: Whether to stream the response content.
            timeout: Request timeout. Can be a float or a tuple (connect, read).
            verify: Whether to verify SSL certificates.
            cert: Client SSL certificate (path to cert file or tuple of cert/key).
            proxies: Proxies to use for the request.

        Returns:
        -------
            A `requests.models.Response` object.
        """
        parsed_url = requests.utils.urlparse(request.url)
        original_hostname = parsed_url.hostname
        self._resolved_sni = None  # Reset for each request

        # Determine if the original hostname in the URL is already an IP address.
        # This helps decide if default_sni_for_ip_url should be considered later.
        is_original_hostname_ip = False
        if original_hostname:
            try:
                socket.inet_pton(socket.AF_INET, original_hostname)  # Check for IPv4
                is_original_hostname_ip = True
            except socket.error:
                try:
                    socket.inet_pton(socket.AF_INET6, original_hostname)  # Check for IPv6
                    is_original_hostname_ip = True
                except socket.error:
                    is_original_hostname_ip = False  # Not an IP address

        # If the hostname is not an IP, and custom DNS is configured, attempt resolution.
        if original_hostname and not is_original_hostname_ip and self.custom_dns_server and dnspython_available:
            resolved_ip = self._resolve_hostname_to_ip(original_hostname)
            if resolved_ip:
                self._resolved_sni = original_hostname  # Save original hostname for SNI.
                self.resolved_ip_cache[original_hostname] = resolved_ip  # Cache for get_connection.
                logger.info("CustomDNSAdapter.send: Original hostname '%s' resolved to IP %s. "
                            "SNI will be '%s'.",
                            original_hostname, resolved_ip, self._resolved_sni)
                # Note: The request.url is NOT changed here. The IP is used in get_connection.

        return super().send(request, stream, timeout, verify, cert, proxies)

    def get_connection(self, url: str, proxies: Optional[Dict[str, str]]=None) -> requests.packages.urllib3.connectionpool.HTTPConnectionPool: # type: ignore
        """Get a connection from the pool for the given URL.

        If a hostname was previously resolved to an IP by `send()` and cached,
        this method modifies the URL passed to `poolmanager.connection_from_url`
        to use the resolved IP address for the actual connection. The original
        hostname (stored in `_resolved_sni`) is used by `init_poolmanager`
        to set SNI and `assert_hostname`.

        Args:
        ----
            url: The URL to connect to.
            proxies: Proxies configuration.

        Returns:
        -------
            An `urllib3.connectionpool.HTTPConnectionPool` object.
        """
        parsed_url = requests.utils.urlparse(url)
        original_hostname = parsed_url.hostname

        resolved_ip_for_connection = None
        if self.custom_dns_server and dnspython_available and original_hostname and original_hostname in self.resolved_ip_cache:
            resolved_ip_for_connection = self.resolved_ip_cache[original_hostname]

        if resolved_ip_for_connection:
            logger.info("CustomDNSAdapter.get_connection: Using resolved IP %s "
                        "for connection to original host %s (URL: %s)",
                        resolved_ip_for_connection, original_hostname, url)

            conn_url_parts = list(parsed_url[:])  # Make a mutable copy
            new_netloc = resolved_ip_for_connection
            if parsed_url.port:
                new_netloc += f":{parsed_url.port}"
            conn_url_parts[1] = new_netloc  # Update netloc with IP:port

            if not conn_url_parts[0]:  # Ensure scheme is present, default to http if missing
                conn_url_parts[0] = "http"

            # Reconstruct the URL with the resolved IP for the connection.
            connection_target_url = requests.utils.urlunparse(conn_url_parts)

            logger.debug("CustomDNSAdapter.get_connection: PoolManager will "
                         "connect to IP-based URL: %s (SNI via "
                         "_resolved_sni: %s)",
                         connection_target_url, self._resolved_sni)
            return self.poolmanager.connection_from_url(connection_target_url)
        else:
            logger.debug(
                "CustomDNSAdapter.get_connection: Proceeding with default "
                "connection for URL: %s. Cache miss or custom DNS not used "
                "for this host.", url)
            return super().get_connection(url, proxies=proxies)

    def init_poolmanager(self, connections: int, maxsize: int,
                         block: bool = False, **pool_kwargs: Any):
        """Initialize the `urllib3.PoolManager`.

        This method is overridden to inject SNI (`server_hostname`) and
        certificate validation hostname (`assert_hostname`) into the pool
        configuration if a hostname was resolved via custom DNS or if a
        `default_sni_for_ip_url` was provided for IP-based requests.

        Args:
        ----
            connections: The number of `urllib3` connection pools to cache.
            maxsize: The maximum number of connections to save in the pool.
            block: Whether to block when no free connections are available.
            **pool_kwargs: Additional keyword arguments for the PoolManager.
        """
        sni_hostname_for_pool = None
        if self._resolved_sni:
            sni_hostname_for_pool = self._resolved_sni
            logger.info(
                "CustomDNSAdapter.init_poolmanager: Using SNI/assert_hostname "
                "from resolved hostname: %s", sni_hostname_for_pool)
        elif self.default_sni_for_ip_url:
            # This case applies if the original request URL was already an IP address.
            sni_hostname_for_pool = self.default_sni_for_ip_url
            logger.info(
                "CustomDNSAdapter.init_poolmanager: Using default SNI for IP "
                "URL: %s", sni_hostname_for_pool)

        if sni_hostname_for_pool:
            pool_kwargs["assert_hostname"] = sni_hostname_for_pool  # For cert validation
            pool_kwargs["server_hostname"] = sni_hostname_for_pool  # For SNI in TLS handshake
            # Ensure certificate validation is enabled when SNI is used.
            if pool_kwargs.get("cert_reqs") is None: # Don't override if already set
                pool_kwargs["cert_reqs"] = ssl.CERT_REQUIRED
            logger.debug(
                "CustomDNSAdapter.init_poolmanager: Pool configured with "
                "SNI/assert_hostname: %s, cert_reqs: %s",
                sni_hostname_for_pool, pool_kwargs["cert_reqs"])
        else:
            logger.debug(
                "CustomDNSAdapter.init_poolmanager: No specific "
                "SNI/assert_hostname configuration for this pool.")

        super().init_poolmanager(
            connections, maxsize, block=block, **pool_kwargs)
