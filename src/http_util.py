import requests

class HttpUtil:
    """Utility class for performing basic HTTP operations."""

    @staticmethod
    def fetch_headers(url, use_akamai_pragma=False):
        """Fetches HTTP headers from a given URL.

        Args:
            url: The URL to fetch headers from.
            use_akamai_pragma: Whether to include Akamai Pragma headers in the request.

        Returns:
            A dictionary containing the fetched headers, or None on error.
        """
        headers = {}
        if use_akamai_pragma:
            akamai_pragma_directives = [
                'akamai-x-get-request-id',
                'akamai-x-get-cache-key',
                'akamai-x-cache-on',
                'akamai-x-cache-remote-on',
                'akamai-x-get-true-cache-key',
                'akamai-x-check-cacheable',
                'akamai-x-get-extracted-values',
                'akamai-x-feo-trace',
                'x-akamai-logging-mode: verbose'
            ]
            headers['Pragma'] = ', '.join(akamai_pragma_directives)
        try:
            response = requests.head(url, headers=headers)
            response.raise_for_status()  # Raise exception for non-2xx status codes
            return response.headers
        except requests.exceptions.RequestException as e:
            # Return error details or propagate exception
            return {'error': str(e)}

