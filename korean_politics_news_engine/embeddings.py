from __future__ import annotations

import hashlib
import math
import re
import struct


TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]+")


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)]


def text_similarity(a: str, b: str) -> float:
    left = set(tokenize(a))
    right = set(tokenize(b))
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


class HashingTextEmbedder:
    model_name = "local-hashing-text"
    model_version = "v1"

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for token in tokenize(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]


def pack_vector(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack_vector(blob: bytes, dim: int) -> list[float]:
    return list(struct.unpack(f"<{dim}f", blob))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    numerator = sum(left * right for left, right in zip(a, b))
    left_norm = math.sqrt(sum(value * value for value in a))
    right_norm = math.sqrt(sum(value * value for value in b))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)
