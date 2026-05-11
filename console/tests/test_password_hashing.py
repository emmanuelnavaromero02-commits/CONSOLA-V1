"""Regression tests for the bcrypt 72-byte truncation fix.

Pre-fix: bcrypt silently truncated any password longer than 72 bytes, so
two long passwords that differed only after byte 72 would map to the same
hash. The SHA-256 pre-hash collapses arbitrarily long inputs into a
44-character base64 digest before bcrypt sees it, so distinct long
passwords produce distinct hashes again.
"""
from __future__ import annotations

from app.services.auth import hash_password, verify_password


def test_short_password_round_trip():
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong", h)


def test_long_passwords_differing_after_byte_72_do_not_collide():
    # 72 identical bytes followed by a one-byte tail. Without pre-hashing
    # bcrypt would treat both as the same input and verify either against
    # the other's hash.
    base = "A" * 72
    p1 = base + "1"
    p2 = base + "2"
    h1 = hash_password(p1)
    h2 = hash_password(p2)
    assert verify_password(p1, h1)
    assert verify_password(p2, h2)
    assert not verify_password(p1, h2), \
        "long passwords differing past byte 72 must not collide"
    assert not verify_password(p2, h1), \
        "long passwords differing past byte 72 must not collide"


def test_unicode_password_round_trip():
    pw = "contraseña-con-ñ-y-emoji-🔐-x" * 5  # easily over 72 bytes encoded
    h = hash_password(pw)
    assert verify_password(pw, h)
    assert not verify_password(pw + "x", h)


def test_verify_password_rejects_missing_hash():
    # Invited-but-not-activated users have NULL password_hash and must not
    # be able to authenticate, regardless of what they submit.
    assert verify_password("anything", None) is False
    assert verify_password("anything", "") is False
