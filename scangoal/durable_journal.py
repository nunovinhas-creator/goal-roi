"""Compare-and-set journal stored outside the GitHub Actions runner."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
import urllib.request


MAX_BYTES = 1_800_000  # Below the size of a SQLite Durable Object row.


class DurabilityError(RuntimeError):
    pass


class Journal:
    def __init__(self, url, token):
        endpoint = urlsplit(url)
        if (endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username is not None
                or endpoint.query or endpoint.fragment or endpoint.path != '/journal' or not token):
            raise DurabilityError('invalid durable journal configuration')
        self.url = url
        self.token = token

    def request(self, method, *, query='', value=None):
        data = None if value is None else json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
        if data is not None and len(data) > MAX_BYTES:
            raise DurabilityError('durable journal request exceeds capacity')
        url = self.url + query
        request = urllib.request.Request(url, data=data, method=method,
                                         headers={'Content-Type': 'application/json', 'Cache-Control': 'no-store'})
        request.add_unredirected_header('Authorization', 'Bearer ' + self.token)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                if getattr(response, 'url', url) != url:
                    raise ValueError('unexpected redirect')
                raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError('oversize response')
            result = json.loads(raw)
            if not isinstance(result, dict) or type(result.get('revision')) is not int or result['revision'] < 0:
                raise ValueError('invalid revision')
            return result
        except HTTPError as exc:
            raise DurabilityError('durable journal HTTP %d' % exc.code) from None
        except (URLError, OSError, ValueError):
            raise DurabilityError('durable journal unavailable or invalid') from None

    def read(self, key):
        result = self.request('GET', query='?' + urlencode({'key': key}))
        if 'value' not in result or (result['value'] is not None and not isinstance(result['value'], dict)):
            raise DurabilityError('invalid durable journal record')
        return result['revision'], result['value']

    def write(self, key, revision, value):
        result = self.request('PUT', value={'key': key, 'revision': revision, 'value': value})
        if result['revision'] != revision + 1:
            raise DurabilityError('invalid durable journal acknowledgement')
        return result['revision']


def configured():
    url = os.environ.get('GOAL_ROI_DURABLE_URL', '').strip()
    token = os.environ.get('GOAL_ROI_DURABLE_TOKEN', '').strip()
    if url or token or os.environ.get('GOAL_ROI_DURABLE_REQUIRED') == '1':
        return Journal(url, token)
    return None
