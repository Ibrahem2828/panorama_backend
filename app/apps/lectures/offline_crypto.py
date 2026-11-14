"""Chunked AES-256-GCM container for offline lecture packages ("PNOF" v1).

The format is specified byte for byte in ``docs/OFFLINE_LECTURE_PACKAGES.md``; keep both in sync.

Layout (all integers big-endian)::

    header (40 bytes)
      0   4  magic            b"PNOF"
      4   1  format version   1
      5   1  algorithm        1  (AES-256-GCM, chunked)
      6   2  flags            0
      8   4  chunk_size       plaintext bytes per full chunk
      12  8  plaintext_size   total plaintext bytes
      20 16  key_id           UUID bytes of the content key
      36  4  nonce_prefix     random per package
    chunk i (0-based), i = 0 .. n-1, n = ceil(plaintext_size / chunk_size)
      ciphertext (== plaintext chunk length)  +  16 byte GCM tag

    nonce_i = nonce_prefix (4) || i as uint64 (8)                   -> 12 bytes
    aad_i   = header (40) || i as uint32 || final flag (1 byte)     -> 45 bytes
              final flag is 0x01 for the last chunk, else 0x00 (detects truncation / chunk dropping)
"""

from __future__ import annotations

import hashlib
import os
import struct
import uuid
from dataclasses import dataclass
from typing import IO

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"PNOF"
FORMAT_VERSION = 1
ALGORITHM_AES_256_GCM_CHUNKED = 1
ALGORITHM_NAME = "AES-256-GCM-chunked"
HEADER_STRUCT = struct.Struct(">4sBBHIQ16s4s")
HEADER_SIZE = HEADER_STRUCT.size  # 40
TAG_SIZE = 16
KEY_SIZE = 32
DEFAULT_CHUNK_SIZE = 64 * 1024
NONCE_SCHEME = "nonce = nonce_prefix(4 bytes from header) || uint64_be(chunk_index)"
AAD_SCHEME = "aad = header(40 bytes) || uint32_be(chunk_index) || final_flag(1 byte: 1 for last chunk else 0)"


@dataclass(frozen=True)
class PackageInfo:
    size: int  # ciphertext package size in bytes
    sha256: str  # hex digest of the whole package
    plaintext_size: int
    chunk_size: int
    chunk_count: int


def package_size(plaintext_size: int, chunk_size: int = DEFAULT_CHUNK_SIZE) -> int:
    chunks = -(-plaintext_size // chunk_size)
    return HEADER_SIZE + plaintext_size + chunks * TAG_SIZE


def build_header(*, chunk_size: int, plaintext_size: int, key_id: uuid.UUID, nonce_prefix: bytes) -> bytes:
    return HEADER_STRUCT.pack(
        MAGIC, FORMAT_VERSION, ALGORITHM_AES_256_GCM_CHUNKED, 0, chunk_size, plaintext_size, key_id.bytes, nonce_prefix
    )


def chunk_nonce(nonce_prefix: bytes, index: int) -> bytes:
    return nonce_prefix + struct.pack(">Q", index)


def chunk_aad(header: bytes, index: int, final: bool) -> bytes:
    return header + struct.pack(">IB", index, 1 if final else 0)


def encrypt_stream(
    source: IO[bytes],
    destination: IO[bytes],
    *,
    key: bytes,
    key_id: uuid.UUID,
    plaintext_size: int,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    nonce_prefix: bytes | None = None,
) -> PackageInfo:
    """Encrypt ``source`` (exactly ``plaintext_size`` bytes) into ``destination`` chunk by chunk.

    Never holds more than one chunk in memory. Raises ``ValueError`` when the source length differs from
    ``plaintext_size`` or is empty.
    """

    if len(key) != KEY_SIZE:
        raise ValueError("Content key must be 32 bytes.")
    if plaintext_size <= 0:
        raise ValueError("Cannot package an empty document.")
    prefix = nonce_prefix if nonce_prefix is not None else os.urandom(4)
    header = build_header(chunk_size=chunk_size, plaintext_size=plaintext_size, key_id=key_id, nonce_prefix=prefix)
    aes = AESGCM(key)
    digest = hashlib.sha256()
    written = 0

    def emit(data: bytes) -> None:
        nonlocal written
        destination.write(data)
        digest.update(data)
        written += len(data)

    emit(header)
    chunk_count = -(-plaintext_size // chunk_size)
    remaining = plaintext_size
    for index in range(chunk_count):
        want = min(chunk_size, remaining)
        block = source.read(want)
        if len(block) != want:
            raise ValueError("Source ended before the declared plaintext size.")
        remaining -= want
        emit(aes.encrypt(chunk_nonce(prefix, index), block, chunk_aad(header, index, index == chunk_count - 1)))
    if source.read(1):
        raise ValueError("Source is longer than the declared plaintext size.")
    return PackageInfo(
        size=written,
        sha256=digest.hexdigest(),
        plaintext_size=plaintext_size,
        chunk_size=chunk_size,
        chunk_count=chunk_count,
    )
