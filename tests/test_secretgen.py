"""Tests for credential generation."""

from __future__ import annotations

import string

from adomi_platform_controller import secretgen


def test_lengths_and_alphabet():
    s = secretgen.random_string(40)
    assert len(s) == 40
    assert set(s) <= set(string.ascii_letters + string.digits)


def test_zero_length():
    assert secretgen.random_string(0) == ""
    assert secretgen.random_string(-5) == ""


def test_values_are_unique():
    # Cryptographically random: two draws should not collide in practice.
    assert secretgen.random_string(128) != secretgen.random_string(128)


def test_default_constants():
    assert secretgen.CLIENT_ID_LENGTH == 40
    assert secretgen.CLIENT_SECRET_LENGTH == 128
