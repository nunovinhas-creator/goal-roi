"""BLS verification of drand `bls-unchained-g1-rfc9380` beacons (quicknet) with py_ecc.

Scheme: signature in G1 (48 bytes, compressed), public key in G2 (96 bytes,
compressed), message = SHA-256(round as 8-byte big-endian), hash to G1 per
RFC 9380 with the DST below. Valid iff e(signature, g2) == e(H(m), public key).
"""

import hashlib

from py_ecc.bls.hash_to_curve import hash_to_G1
from py_ecc.bls.point_compression import decompress_G1, decompress_G2
from py_ecc.fields import optimized_bls12_381_FQ12 as FQ12
from py_ecc.optimized_bls12_381 import G2, curve_order, final_exponentiate, is_inf, multiply, neg, pairing

DST = b'BLS_SIG_BLS12381G1_XMD:SHA-256_SSWU_RO_NUL_'
SCHEME = 'bls-unchained-g1-rfc9380'


def message(round_number):
    return hashlib.sha256(round_number.to_bytes(8, 'big')).digest()


def _g1(hex_signature):
    raw = bytes.fromhex(hex_signature)
    if len(raw) != 48:
        raise ValueError('signature_must_be_48_bytes')
    point = decompress_G1(int.from_bytes(raw, 'big'))
    if is_inf(point) or not is_inf(multiply(point, curve_order)):
        raise ValueError('signature_not_in_prime_order_subgroup')
    return point


def _g2(hex_key):
    raw = bytes.fromhex(hex_key)
    if len(raw) != 96:
        raise ValueError('public_key_must_be_96_bytes')
    point = decompress_G2((int.from_bytes(raw[:48], 'big'), int.from_bytes(raw[48:], 'big')))
    if is_inf(point) or not is_inf(multiply(point, curve_order)):
        raise ValueError('public_key_not_in_prime_order_subgroup')
    return point


def verify(public_key_hex, round_number, signature_hex):
    try:
        signature, key = _g1(signature_hex), _g2(public_key_hex)
    except (ValueError, AssertionError):
        return False
    hashed = hash_to_G1(message(round_number), DST, hashlib.sha256)
    product = pairing(neg(G2), signature, final_exponentiate=False) * pairing(key, hashed, final_exponentiate=False)
    return final_exponentiate(product) == FQ12.one()


def verify_entry(info, entry):
    """Full check of one relay answer against verified chain info."""
    return (info.get('schemeID') == SCHEME
            and hashlib.sha256(bytes.fromhex(entry['signature'])).hexdigest() == entry['randomness']
            and verify(info['public_key'], entry['round'], entry['signature']))
