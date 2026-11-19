"""Dispatch under the shared writer lock. All ambiguous retries occupy one pending slot."""
import http.client
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

TRANSIENT = {429, 500, 502, 503, 504}


def dispatch(request, open_url=urlopen, sleep=time.sleep):
    for delay in (2, 4, 8, 16, None):
        try:
            with open_url(request, timeout=6):
                return
        except HTTPError as error:
            if error.code not in TRANSIENT:
                raise
            retry_after = (error.headers or {}).get('Retry-After', '')
            if retry_after:
                if not retry_after.isdecimal() or int(retry_after) > 16:
                    raise
                if delay is not None:
                    delay = max(delay, int(retry_after))
        except (URLError, TimeoutError, ConnectionError, http.client.IncompleteRead):
            pass
        if delay is None:
            raise RuntimeError('Collection dispatch attempts exhausted')
        print(f'Dispatch temporarily unavailable. Retrying in {delay}s.', flush=True)
        sleep(delay)


if __name__ == '__main__':
    url = f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/actions/workflows/collect.yml/dispatches"
    dispatch(Request(url, data=json.dumps({'ref': 'main'}).encode(), headers={
        'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'Content-Type': 'application/json'}))
