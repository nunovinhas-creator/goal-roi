"""Verify independent RFC 3161 timestamps for Goal-ROI's scientific records."""

import argparse
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography import x509
from cryptography.hazmat.primitives import hashes


DIRECTORY = 'scientific_receipts'
ROOT = Path(__file__).with_name('trust') / 'freetsa-root.pem'
ROOT_SHA256 = 'a6379e7cecc05faa3cbf076013d745e327bbbaa38c0b9af22469d4701d18aabc'
MAX_RECEIPT_BYTES = 64 * 1024


@dataclass(frozen=True)
class Receipt:
    identifier: str
    digest: str
    at: datetime


def digest(domain, content):
    payload = {'schema': 1, 'repository': 'nunovinhas-creator/goal-roi', 'domain': domain, 'content': content}
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def verify(directory, domain, content, *, as_of):
    """Return a verified receipt known by the cutoff, or None for any insufficient proof."""
    if directory is None or as_of is None:
        return None
    try:
        imprint = digest(domain, content)
        with (Path(directory) / (imprint + '.tsr')).open('rb') as handle:
            token = handle.read(MAX_RECEIPT_BYTES + 1)
        if not token or len(token) > MAX_RECEIPT_BYTES:
            return None
        root = ROOT.read_bytes()
        if x509.load_pem_x509_certificate(root).fingerprint(hashes.SHA256()).hex() != ROOT_SHA256:
            return None
        with TemporaryDirectory() as tmp:
            certificate, response = Path(tmp) / 'root.pem', Path(tmp) / 'receipt.tsr'
            certificate.write_bytes(root)
            response.write_bytes(token)
            env = {'PATH': os.defpath, 'LC_ALL': 'C', 'OPENSSL_CONF': os.devnull,
                   'SSL_CERT_FILE': str(certificate), 'SSL_CERT_DIR': tmp}

            def openssl(*args):
                return subprocess.run(['openssl', 'ts', *args], capture_output=True, text=True,
                                      check=True, timeout=5, env=env).stdout

            summary = openssl('-reply', '-in', str(response), '-text')
            stamp = re.search(r'^Time stamp: (.*) GMT$', summary, re.M)
            serial = re.search(r'^Serial number: 0x([0-9A-Fa-f]+)$', summary, re.M)
            if not stamp or not serial or not re.search(r'^Hash Algorithm: sha256$', summary, re.M):
                return None
            stamp = stamp[1]
            fmt = '%b %d %H:%M:%S.%f %Y' if '.' in stamp else '%b %d %H:%M:%S %Y'
            at = datetime.strptime(stamp, fmt).replace(tzinfo=timezone.utc)
            if at > as_of:
                return None
            openssl('-verify', '-in', str(response), '-digest', imprint, '-CAfile', str(certificate),
                    '-CApath', tmp, '-attime', str(int(at.timestamp())))
            return Receipt('freetsa:' + serial[1].lower(), imprint, at)
    except (OSError, ValueError, TypeError, OverflowError, subprocess.SubprocessError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('domain')
    parser.add_argument('content', type=Path, help='JSON record to bind to the timestamp')
    parser.add_argument('--verify-from', type=Path)
    args = parser.parse_args()
    content = json.loads(args.content.read_bytes())
    if args.verify_from is None:
        print(digest(args.domain, content))
        return
    receipt = verify(args.verify_from, args.domain, content, as_of=datetime.now(timezone.utc))
    if receipt is None:
        parser.exit(1, 'No valid independent receipt\n')
    print(receipt.identifier, receipt.at.isoformat(), receipt.digest)


if __name__ == '__main__':
    main()
