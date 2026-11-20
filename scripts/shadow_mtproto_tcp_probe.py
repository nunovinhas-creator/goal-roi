"""Single-shot, credential-free network reachability test for Telegram MTProto DC2.

Only opens and immediately closes a TCP socket on port 443. It does NOT
send a Telegram request, authenticate, read messages, install Telethon,
read environment secrets, or modify the SHADOW collector or heartbeat.
Results show network reachability only, NOT Telegram/SHADOW viability.
"""
import argparse
import json
import socket
from datetime import datetime, timezone
from pathlib import Path

DC2_IPV4 = "149.154.167.51"
DC2_IPV6 = "2001:67c:4e8:f002::a"
PORT = 443


def try_tcp(address, family, timeout=4):
    if not 0.5 <= timeout <= 10:
        raise ValueError("invalid timeout")
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect((address, PORT))
            return True
    except OSError:
        return False


def probe(timeout=4):
    return {
        "test": "telegram_dc2_tcp_handshake_only",
        "observed_utc": datetime.now(timezone.utc).isoformat(),
        "tcp443_ipv4": try_tcp(DC2_IPV4, socket.AF_INET, timeout),
        "tcp443_ipv6": try_tcp(DC2_IPV6, socket.AF_INET6, timeout),
        "telegram_authenticated": False,
        "telegram_messages_read": False,
        "credentials_used": False,
        "scientific_state": "not_activated",
        "mtproto_protocol_verified": False,
        "free_host_24h_verified": False,
    }


def main():
    p = argparse.ArgumentParser(description="No-secret one-time MTProto TCP reachability probe")
    p.add_argument("--output", default="shadow_tcp_probe.json")
    p.add_argument("--timeout", type=float, default=4)
    args = p.parse_args()
    if not 0.5 <= args.timeout <= 10:
        p.error("timeout must be 0.5..10 seconds")
    dest = Path(args.output)
    result = probe(args.timeout)
    # Exclusive creation: never replace an earlier result or startup configuration.
    with dest.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write("\n")
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
