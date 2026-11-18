"""Zero-secret uptime probe for untrusted free Python hosts.

No Telegram authentication, messages, Git operations, dependencies, or credentials.
One TCP connectivity check and a public req_pq_multi handshake run once at startup.
Records UTC heartbeat gaps in a separate local JSON file.
Never use this as evidence of live signal delivery or scientific admission.
"""
import argparse
import json
import os
import secrets
import socket
import struct
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

VERSION = 1
UTC = timezone.utc


# One-shot network checks: no account login, API credentials or message access.
# Uses documented DC2 reference IPs; reachability of one DC alone is not an MTProto test.
NETWORK_REPORT = "shadow_tcp_probe.json"


def _tcp_connect(address, family, timeout=4):
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect((address, 443))
        return True
    except OSError:
        return False


def network_once(path=NETWORK_REPORT):
    """Run at most once per report filename; never overwrite previous evidence."""
    destination = Path(path)
    if destination.exists():
        return None
    report = {
        "schema": 1,
        "test": "telegram_dc2_tcp_443_only",
        "observed_utc": datetime.now(UTC).isoformat(),
        "tcp443_ipv4": _tcp_connect("149.154.167.51", socket.AF_INET),
        "tcp443_ipv6": _tcp_connect("2001:67c:4e8:f002::a", socket.AF_INET6),
        "credentials_used": False,
        "telegram_authenticated": False,
        "messages_read": False,
        "mtproto_verified": False,
        "scientific_state": "not_activated",
    }
    with destination.open("x", encoding="utf-8") as file:
        json.dump(report, file, sort_keys=True)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    return report


# The Telegram MTProto specification allows a public, unauthenticated req_pq_multi
# exchange while creating an auth key. This probe stops immediately at resPQ.
# No auth key is generated, and no application/API calls are made.
MTPROTO_REPORT = "shadow_mtproto_probe.json"


def _recv_exact(sock, amount):
    if not isinstance(amount, int) or not 0 <= amount <= 4096:
        raise ValueError("unbounded MTProto response")
    result = bytearray()
    while len(result) < amount:
        chunk = sock.recv(amount - len(result))
        if not chunk:
            raise OSError("MTProto response ended early")
        result.extend(chunk)
    return bytes(result)


def _valid_res_pq(payload, nonce):
    """Validate unencrypted resPQ structure, request nonce, vector bounds."""
    if len(payload) < 20 + 4 + 16 + 16 + 4 + 8 + 8:
        return False
    if payload[:8] != b"\\x00" * 8:
        return False
    length = struct.unpack_from("<I", payload, 16)[0]
    if length != len(payload) - 20 or length % 4:
        return False
    body = payload[20:]
    if struct.unpack_from("<I", body, 0)[0] != 0x05162463:
        return False
    if body[4:20] != nonce:
        return False
    pos = 36  # constructor + nonce + server_nonce
    first = body[pos]
    if first == 254:
        if len(body) < pos + 4:
            return False
        size = int.from_bytes(body[pos+1:pos+4], "little")
        prefix = 4
    elif first < 254:
        size = first
        prefix = 1
    else:
        return False
    if not 1 <= size <= 32:
        return False
    pos += prefix + size
    pos += (-pos) % 4
    if len(body) < pos + 8:
        return False
    if struct.unpack_from("<I", body, pos)[0] != 0x1CB5C415:
        return False
    count = struct.unpack_from("<I", body, pos+4)[0]
    return 1 <= count <= 32 and len(body) == pos + 8 + count * 8


def _mtproto_req_pq(address="149.154.167.51", timeout=5):
    """One single abridged MTProto req_pq_multi roundtrip, no authentication."""
    if not 0.5 <= timeout <= 10:
        raise ValueError("invalid timeout")
    nonce = secrets.token_bytes(16)
    timestamp_ns = time.time_ns()
    sec, fraction = divmod(timestamp_ns, 1_000_000_000)
    message_id = (sec << 32) | (((fraction << 32) // 1_000_000_000) & ~3)
    body = struct.pack("<I", 0xBE7E8EF1) + nonce
    request = (b"\\x00" * 8 + struct.pack("<Q", message_id) +
               struct.pack("<I", len(body)) + body)
    try:
        with socket.create_connection((address, 443), timeout=timeout) as sock:
            sock.settimeout(timeout)
            # Abridged transport: 0xef marker, length in four-byte words.
            sock.sendall(b"\\xef" + bytes([len(request) // 4]) + request)
            first = _recv_exact(sock, 1)[0]
            if first == 127:
                size = int.from_bytes(_recv_exact(sock, 3), "little") * 4
            elif 1 <= first < 127:
                size = first * 4
            else:
                return False
            if not 40 <= size <= 4096:
                return False
            return _valid_res_pq(_recv_exact(sock, size), nonce)
    except OSError:
        return False


def mtproto_once(path=MTPROTO_REPORT):
    """Persist aggregate result only; do not rerun if a report already exists."""
    dest = Path(path)
    if dest.exists():
        return None
    result = {
        "schema": 1,
        "test": "one_shot_public_req_pq_multi",
        "observed_utc": datetime.now(UTC).isoformat(),
        "res_pq_nonce_valid": _mtproto_req_pq(),
        "session_created": False,
        "auth_key_created": False,
        "account_authenticated": False,
        "messages_read": False,
        "credentials_used": False,
        "scientific_state": "not_activated",
    }
    with dest.open("x", encoding="utf-8") as out:
        json.dump(result, out, sort_keys=True)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    return result


def _iso(now):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("timezone-aware clock required")
    return now.astimezone(UTC).isoformat()


def heartbeat(path, now, *, startup=False, max_gap_seconds=120):
    """Persist one synthetic heartbeat, explicitly flag clock regression."""
    if not isinstance(max_gap_seconds, (int, float)) or max_gap_seconds <= 0:
        raise ValueError("invalid max gap")
    state_path = Path(path)
    at = _iso(now)
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("schema") != VERSION:
            raise ValueError("unexpected state schema; do not overwrite")
        previous = datetime.fromisoformat(state["last_seen_utc"])
        gap = (now.astimezone(UTC) - previous).total_seconds()
        if gap < 0:
            raise ValueError("host clock moved backwards; fail closed")
        state["largest_gap_seconds"] = max(state["largest_gap_seconds"], gap)
        if gap > max_gap_seconds:
            state["gaps_over_120_seconds"] += 1
        state["heartbeats"] += 1
        if startup:
            state["process_starts"] += 1
    else:
        if not startup:
            raise ValueError("state missing during an active run")
        state = {
            "schema": VERSION,
            "started_utc": at,
            "last_seen_utc": at,
            "process_starts": 1,
            "heartbeats": 1,
            "largest_gap_seconds": 0.0,
            "gaps_over_120_seconds": 0,
            "synthetic_only": True,
            "live_source_connected": False,
            "scientific_state": "not_activated",
        }
    state["last_seen_utc"] = at
    state_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".shadow-uptime-", dir=str(state_path.parent))
    try:
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(state, out, sort_keys=True, separators=(",", ":"))
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, state_path)
        if hasattr(os, "O_DIRECTORY"):
            directory = os.open(str(state_path.parent), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return state


def main():
    p = argparse.ArgumentParser(description="Offline, no-secret SHADOW uptime probe")
    p.add_argument("--state", default="shadow_probe_state.json")
    p.add_argument("--interval", type=int, default=20)
    p.add_argument("--duration-minutes", type=int, default=0,
                   help="0 = until stopped; run at least 24h for reliability evidence")
    args = p.parse_args()
    if not 5 <= args.interval <= 60 or not 0 <= args.duration_minutes <= 43200:
        p.error("interval must be 5..60s; duration must be 0..43200 minutes")
    started = time.monotonic()
    first = True
    try:
        while True:
            result = heartbeat(args.state, datetime.now(UTC), startup=first)
            if first:
                try:
                    network = network_once()
                    if network is not None:
                        print(json.dumps({"network_probe": "tcp_only",
                                          "tcp443_ipv4": network["tcp443_ipv4"],
                                          "tcp443_ipv6": network["tcp443_ipv6"],
                                          "mtproto_verified": False,
                                          "credentials_used": False}), flush=True)
                except (OSError, ValueError) as exc:
                    print(json.dumps({"network_probe": "failed",
                                      "error_type": type(exc).__name__}), flush=True)
                try:
                    mtproto = mtproto_once()
                    if mtproto is not None:
                        print(json.dumps({"mtproto_probe": "unauthenticated_req_pq",
                                          "res_pq_nonce_valid": mtproto["res_pq_nonce_valid"],
                                          "account_authenticated": False,
                                          "credentials_used": False}), flush=True)
                except (OSError, ValueError) as exc:
                    print(json.dumps({"mtproto_probe": "failed",
                                      "error_type": type(exc).__name__}), flush=True)
            first = False
            if result["heartbeats"] % 3 == 1 or result["gaps_over_120_seconds"]:
                # Only aggregate statistics. No account, host, IP, message, URL or secrets.
                print(json.dumps({
                    "synthetic": True,
                    "heartbeats": result["heartbeats"],
                    "process_starts": result["process_starts"],
                    "gaps_over_120_seconds": result["gaps_over_120_seconds"],
                    "largest_gap_seconds": result["largest_gap_seconds"]
                }), flush=True)
            if args.duration_minutes and time.monotonic()-started >= args.duration_minutes*60:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Uptime probe stopped; existing state retained", flush=True)


if __name__ == "__main__":
    main()
