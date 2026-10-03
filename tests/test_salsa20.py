"""Salsa20, against published vectors."""

import pytest

from opent5.container.salsa20 import (
    BLOCK_SIZE,
    DEFAULT_ROUNDS,
    KEY_SIZE,
    NONCE_SIZE,
    SIGMA,
    TAU,
    Salsa20Error,
    crypt,
    crypt_reference,
    keystream,
    keystream_fast,
)

# ECRYPT Set 6, vector 0, 256-bit key.
VECTOR_KEY = bytes.fromhex("0053A6F94C9FF24598EB3E91E4378ADD3083D6297CCF2275C81B6EC11467BA0D")
VECTOR_NONCE = bytes.fromhex("0D74DB42A91077DE")
VECTOR_STREAM = bytes.fromhex(
    "F5FAD53F79F9DF58C4AEA0D0ED9A9601F278112CA7180D565B420A48019670EA"
    "F24CE493A86263F677B46ACE1924773D2BB25571E1AA8593758FC382B1280B71"
)


class TestTheCipher:
    def test_it_matches_the_published_vector(self):
        assert keystream(VECTOR_KEY, VECTOR_NONCE, BLOCK_SIZE) == VECTOR_STREAM

    def test_the_constants_are_the_ones_the_spec_names(self):
        assert SIGMA == b"expand 32-byte k"
        assert TAU == b"expand 16-byte k"

    def test_twenty_rounds_is_the_default(self):
        assert DEFAULT_ROUNDS == 20

    def test_a_longer_stream_continues_the_same_bytes(self):
        assert keystream(VECTOR_KEY, VECTOR_NONCE, BLOCK_SIZE * 2)[:BLOCK_SIZE] == VECTOR_STREAM

    def test_the_second_block_differs_from_the_first(self):
        stream = keystream(VECTOR_KEY, VECTOR_NONCE, BLOCK_SIZE * 2)

        assert stream[:BLOCK_SIZE] != stream[BLOCK_SIZE:]

    def test_starting_at_a_later_counter_skips_blocks(self):
        whole = keystream(VECTOR_KEY, VECTOR_NONCE, BLOCK_SIZE * 2)

        assert keystream(VECTOR_KEY, VECTOR_NONCE, BLOCK_SIZE, counter=1) == whole[BLOCK_SIZE:]

    def test_a_length_that_is_not_a_whole_block_is_cut(self):
        assert keystream(VECTOR_KEY, VECTOR_NONCE, 10) == VECTOR_STREAM[:10]

    def test_no_length_is_no_bytes(self):
        assert keystream(VECTOR_KEY, VECTOR_NONCE, 0) == b""


class TestEncryptAndDecrypt:
    def test_it_is_its_own_inverse(self):
        message = b"the zone content" * 9

        once = crypt(message, VECTOR_KEY, VECTOR_NONCE)
        assert crypt(once, VECTOR_KEY, VECTOR_NONCE) == message

    def test_the_ciphertext_is_the_plaintext_xor_the_keystream(self):
        message = bytes(BLOCK_SIZE)

        assert crypt(message, VECTOR_KEY, VECTOR_NONCE) == VECTOR_STREAM

    def test_a_different_nonce_gives_a_different_stream(self):
        message = b"x" * 32
        other = bytes(NONCE_SIZE)

        assert crypt(message, VECTOR_KEY, VECTOR_NONCE) != crypt(message, VECTOR_KEY, other)


class TestRefusals:
    @pytest.mark.parametrize("size", [0, 8, 24, 31, 33])
    def test_a_key_of_the_wrong_length_is_refused(self, size):
        with pytest.raises(Salsa20Error, match="key must be"):
            keystream(bytes(size), VECTOR_NONCE, 16)

    @pytest.mark.parametrize("size", [0, 4, 7, 9, 16])
    def test_a_nonce_of_the_wrong_length_is_refused(self, size):
        with pytest.raises(Salsa20Error, match="nonce must be"):
            keystream(VECTOR_KEY, bytes(size), 16)

    def test_a_negative_length_is_refused(self):
        with pytest.raises(Salsa20Error, match="must not be negative"):
            keystream(VECTOR_KEY, VECTOR_NONCE, -1)

    def test_a_sixteen_byte_key_is_accepted(self):
        assert len(keystream(bytes(16), VECTOR_NONCE, 16)) == 16

    def test_the_key_size_constant_is_the_one_used(self):
        assert len(VECTOR_KEY) == KEY_SIZE


class TestTheFastPathAgreesWithTheReference:
    def test_it_matches_the_published_vector(self):
        assert keystream_fast(VECTOR_KEY, VECTOR_NONCE, BLOCK_SIZE) == VECTOR_STREAM

    @pytest.mark.parametrize("length", [0, 1, 63, 64, 65, 1000, 4097])
    def test_it_matches_the_reference_at_every_length(self, length):
        assert keystream_fast(VECTOR_KEY, VECTOR_NONCE, length) == keystream(
            VECTOR_KEY, VECTOR_NONCE, length
        )

    def test_it_matches_the_reference_from_a_later_counter(self):
        assert keystream_fast(VECTOR_KEY, VECTOR_NONCE, 200, counter=5) == keystream(
            VECTOR_KEY, VECTOR_NONCE, 200, counter=5
        )

    def test_a_counter_past_32_bits_carries_into_the_high_word(self):
        start = (1 << 32) - 1
        assert keystream_fast(VECTOR_KEY, VECTOR_NONCE, 192, counter=start) == keystream(
            VECTOR_KEY, VECTOR_NONCE, 192, counter=start
        )

    def test_crypt_matches_the_reference(self):
        message = bytes(range(256)) * 5
        assert crypt(message, VECTOR_KEY, VECTOR_NONCE) == crypt_reference(
            message, VECTOR_KEY, VECTOR_NONCE
        )

    def test_a_sixteen_byte_key_agrees_too(self):
        assert keystream_fast(bytes(range(16)), VECTOR_NONCE, 130) == keystream(
            bytes(range(16)), VECTOR_NONCE, 130
        )
