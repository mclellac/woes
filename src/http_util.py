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
      headers['Pragma'] = 'no-cache'
      headers['Cache-Control'] = 'no-cache'
    try:
      response = requests.head(url, headers=headers)
      response.raise_for_status()  # Raise exception for non-2xx status codes
      return response.headers
    except requests.exceptions.RequestException as e:
      print(f"Error fetching headers: {e}")
      return None

