"""Salted SHA-256 commitments over a canonical directory digest.

The bank author publishes only `commitment` before the beacon round. At reveal,
anyone recomputes it from the revealed files and the revealed salt. The salt
stops guessing of short or templated files from the hash alone.
"""

import argparse
import hashlib
import json
from pathlib import Path


def tree_digest(root):
    """Order-independent and metadata-free: relative POSIX path, size and SHA-256 of each regular file."""
    root = Path(root)
    lines = []
    for path in sorted(p for p in root.rglob('*') if p.is_file() and not p.is_symlink()):
        relative = path.relative_to(root).as_posix()
        if any(part.startswith('.') or part == '__pycache__' for part in relative.split('/')):
            continue
        data = path.read_bytes()
        lines.append(f'{relative}\t{len(data)}\t{hashlib.sha256(data).hexdigest()}')
    manifest = '\n'.join(lines) + '\n'
    return hashlib.sha256(manifest.encode()).hexdigest(), lines


def commit(root, salt_hex, protocol_sha256, role):
    digest, files = tree_digest(root)
    if len(bytes.fromhex(salt_hex)) < 32:
        raise ValueError('salt_must_have_at_least_32_bytes')
    payload = f'goal-roi-k0/commitment/v1\n{role}\n{protocol_sha256}\n{digest}\n{salt_hex}\n'
    return dict(role=role, protocol_sha256=protocol_sha256, files=len(files),
                commitment=hashlib.sha256(payload.encode()).hexdigest())


def verify(root, salt_hex, protocol_sha256, role, published):
    return commit(root, salt_hex, protocol_sha256, role)['commitment'] == published


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root')
    parser.add_argument('--salt', required=True, help='hex, at least 32 bytes; keep private until reveal')
    parser.add_argument('--protocol-sha256', required=True)
    parser.add_argument('--role', required=True, choices=['bank_author', 'algorithm_author'])
    parser.add_argument('--verify')
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(dict(valid=verify(args.root, args.salt, args.protocol_sha256, args.role, args.verify))))
    else:
        print(json.dumps(commit(args.root, args.salt, args.protocol_sha256, args.role), sort_keys=True))
