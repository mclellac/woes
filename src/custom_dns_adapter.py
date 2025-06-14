"""
Custom HTTPAdapter for 'requests' with custom DNS resolution and SNI handling.

This module provides a custom HTTPAdapter that allows for specifying a DNS server
for hostname resolution and handles Server Name Indication (SNI) for HTTPS connections.
"""

import logging
import socket
import ssl
from typing import Optional, Tuple, List, Dict

import requests
import requests.utils # For urlparse, urlunparse
from requests.adapters import HTTPAdapter

try:
    import dns.resolver
    import dns.exception
except ImportError:
    dns = None
    logging.getLogger(__name__).warning("dnspython library not found. Custom DNS functionality will be disabled in CustomDNSAdapter.")

logger = logging.getLogger(__name__)


class CustomDNSAdapter(HTTPAdapter):
    """
    A custom HTTPAdapter for `requests` that enables custom DNS resolution and SNI handling.

    This adapter intercepts requests to:
    1. Resolve the hostname using a specified custom DNS server if `dnspython` is available.
       If resolution occurs, the request URL is internally modified to use the resolved IP address
       for the connection, while the original hostname is saved for SNI and Host header purposes.
    2. Configure Server Name Indication (SNI) for HTTPS connections:
       - If a hostname was resolved to an IP, the original hostname is used for SNI.
       - If the request URL is already an IP address, a `default_sni` (typically from the
         user's 'Host' header input) can be used for SNI.
    3. Set `assert_hostname` for `urllib3`'s connection pool to ensure the SSL certificate
       is validated against the correct hostname (either the original or the specified SNI).

    If custom DNS is not used or resolution fails, it behaves like a standard HTTPAdapter.
    """

    def __init__(self, *args, custom_dns_server: Optional[str] = None, default_sni: Optional[str] = None, **kwargs):
        """
        Initialize the CustomDNSAdapter.

        :param args: Positional arguments to pass to the parent HTTPAdapter.
        :param custom_dns_server: IP address of the custom DNS server. If None, or if `dns` (dnspython)
                                  module is unavailable, system DNS will be used.
        :type custom_dns_server: Optional[str]
        :param default_sni: The hostname to use for SNI and certificate validation if the request URL
                            is already an IP address. This is typically derived from the user's
                            'Host' header input.
        :type default_sni: Optional[str]
        :param kwargs: Keyword arguments to pass to the parent HTTPAdapter.
        """
        self.custom_dns_server = custom_dns_server
        self.default_sni_for_ip_url = default_sni
        self._resolved_sni: Optional[str] = None  # SNI derived from hostname resolution by this adapter.
        self.resolved_ip_cache: Dict[str, str] = {} # Cache for resolved IPs: hostname -> IP
        super().__init__(*args, **kwargs)

    def _resolve_hostname_to_ip(self, hostname: str) -> Optional[str]:
        """
        Resolve a hostname using the custom DNS server.

        Attempts to resolve AAAA records first, then A records.

        :param hostname: The hostname to resolve.
        :type hostname: str
        :return: The first resolved IP address (preferring IPv6) as a string, or None if
                 resolution fails, custom DNS is not configured, or `dnspython` is unavailable.
        :rtype: Optional[str]
        """
        if not self.custom_dns_server or not dns:
            logger.debug("CustomDNSAdapter: Skipping custom DNS (server: %s, dnspython available: %s) for %s.",
                         self.custom_dns_server, bool(dns), hostname)
            return None

        logger.info("CustomDNSAdapter: Attempting to resolve '%s' using DNS server %s", hostname, self.custom_dns_server)
        resolver = dns.resolver.Resolver()
        resolver.nameservers = [self.custom_dns_server]
        resolver.timeout = 2.0  # Short timeout for each DNS query attempt
        resolver.lifetime = 2.0  # Total time for the entire resolution attempt including retries

        ips: List[str] = []
        try:
            for rdtype in ("AAAA", "A"):  # Prefer AAAA (IPv6) if available
                try:
                    answers = resolver.resolve(hostname, rdtype)
                    for rdata in answers:
                        ips.append(rdata.address)
                    if ips:  # Found records of the current type
                        break
                except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
                    logger.debug("CustomDNSAdapter: No %s records found for %s via %s.", rdtype, hostname, self.custom_dns_server)
                except dns.exception.DNSException as e: # More specific exceptions like Timeout, YXDOMAIN, etc.
                    logger.warning("CustomDNSAdapter: DNS %s query for %s via %s failed: %s",
                                   rdtype, hostname, self.custom_dns_server, e)

            if ips:
                selected_ip = ips[0] # Prefer first resolved (AAAA if found, then A)
                logger.info("CustomDNSAdapter: Resolved '%s' to %s (using %s).", hostname, selected_ip, self.custom_dns_server)
                return selected_ip
            logger.warning("CustomDNSAdapter: No AAAA or A records found for %s via %s.", hostname, self.custom_dns_server)
        except Exception as e: # Catch-all for other unexpected errors during resolution
            logger.error("CustomDNSAdapter: Unexpected error during custom DNS resolution for %s: %s", hostname, e, exc_info=True)
        return None

    def send(self, request: requests.models.PreparedRequest, stream: bool = False, timeout: Optional[float] = None, verify: bool = True, cert: Optional[Tuple[str, str] | str] = None, proxies=None):
        """
        Send a prepared request.

        This method handles the sending of a request, potentially using a custom DNS resolver
        to modify the destination IP address and configuring SNI. It overrides the base
        HTTPAdapter's send method.

        :param request: The :class:`PreparedRequest <requests.PreparedRequest>` object to send.
        :type request: requests.models.PreparedRequest
        :param stream: (optional) Whether to stream the request content. Defaults to False.
        :type stream: bool
        :param timeout: (optional) How long to wait for the server to send data
                        before giving up, as a float, or a :ref:`(connect timeout, read
                        timeout) <timeouts>` tuple. Defaults to None.
        :type timeout: Optional[float]
        :param verify: (optional) Either a boolean, in which case it controls whether we verify
                       the server's TLS certificate, or a string, in which case it must be a path
                       to a CA bundle to use. Defaults to True.
        :type verify: bool or str
        :param cert: (optional) Any user-provided SSL certificate to be trusted.
                     Can be a single file (containing the private key and the certificate)
                     or a tuple of (cert file, key file) paths. Defaults to None.
        :type cert: Optional[Tuple[str, str] | str]
        :param proxies: (optional) The proxies dictionary to apply to the request. Defaults to None.
        :type proxies: Optional[dict]
        :return: The :class:`Response <requests.Response>` object.
        :rtype: requests.Response
        """
        parsed_url = requests.utils.urlparse(request.url)
        original_hostname = parsed_url.hostname
        self._resolved_sni = None  # Reset for each request

        is_original_hostname_ip = False
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
                self.resolved_ip_cache[original_hostname] = resolved_ip # Cache successful resolution
                logger.info("CustomDNSAdapter.send: Original request URL %s will be used. Connection will target resolved IP %s (SNI will be: %s)",
                             request.url, resolved_ip, self._resolved_sni)

        return super().send(request, stream, timeout, verify, cert, proxies)

    def get_connection(self, url: str, proxies=None):
        """
        Override HTTPAdapter.get_connection for custom IP resolution.

        :param url: The URL to connect to.
        :type url: str
        :param proxies: Proxies configuration.
        :type proxies: Optional[dict]
        :return: The connection object.
        :rtype: urllib3.connectionpool.HTTPConnectionPool or urllib3.connectionpool.HTTPSConnectionPool
        """
        parsed_url = requests.utils.urlparse(url)
        original_hostname = parsed_url.hostname

        resolved_ip_for_connection = None
        if self.custom_dns_server and dns and original_hostname and original_hostname in self.resolved_ip_cache:
            resolved_ip_for_connection = self.resolved_ip_cache[original_hostname]

        if resolved_ip_for_connection:
            logger.info("CustomDNSAdapter.get_connection: Using resolved IP %s for connection to original host %s (URL: %s)",
                         resolved_ip_for_connection, original_hostname, url)

            conn_url_parts = list(parsed_url[:]) # Make a mutable copy
            new_netloc = resolved_ip_for_connection
            if parsed_url.port:
                new_netloc += f":{parsed_url.port}"
            conn_url_parts[1] = new_netloc # Index 1 is 'netloc'

            if not conn_url_parts[0]: # Ensure scheme is present for urlunparse
                conn_url_parts[0] = "https" if parsed_url.scheme == "https" else "http"

            connection_target_url = requests.utils.urlunparse(conn_url_parts)

            logger.debug("CustomDNSAdapter.get_connection: PoolManager will connect to IP-based URL: %s (SNI via _resolved_sni: %s)",
                         connection_target_url, self._resolved_sni)
            return self.poolmanager.connection_from_url(connection_target_url)
        else:
            logger.debug("CustomDNSAdapter.get_connection: Proceeding with default connection for URL: %s. Cache miss or custom DNS not used for this host.", url)
            return super().get_connection(url, proxies=proxies)

    def init_poolmanager(self, connections: int, maxsize: int, block: bool = False, **pool_kwargs):
        """
        Initialize the `urllib3.PoolManager` with SNI and certificate validation settings.

        :param connections: The number of `urllib3` connection pools to cache.
        :type connections: int
        :param maxsize: The maximum number of connections to save in the pool.
        :type maxsize: int
        :param block: Whether the connection pool should block for connections.
        :type block: bool
        :param pool_kwargs: Extra keyword arguments used to initialize the PoolManager.
        """
        sni_hostname_for_pool = None
        if self._resolved_sni:
            sni_hostname_for_pool = self._resolved_sni
            logger.info("CustomDNSAdapter.init_poolmanager: Using SNI/assert_hostname from resolved hostname: %s", sni_hostname_for_pool)
        elif self.default_sni_for_ip_url:
            sni_hostname_for_pool = self.default_sni_for_ip_url
            logger.info("CustomDNSAdapter.init_poolmanager: Using default SNI for IP URL: %s", sni_hostname_for_pool)

        if sni_hostname_for_pool:
            pool_kwargs["assert_hostname"] = sni_hostname_for_pool
            pool_kwargs["server_hostname"] = sni_hostname_for_pool # For SNI
            pool_kwargs["cert_reqs"] = ssl.CERT_REQUIRED # Ensure certificate validation
            logger.debug("CustomDNSAdapter.init_poolmanager: Pool configured with SNI/assert_hostname: %s", sni_hostname_for_pool)
        else:
            logger.debug("CustomDNSAdapter.init_poolmanager: No specific SNI/assert_hostname configuration for this pool.")

        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)
