# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The protobuf wire format KiCad's API speaks, with no schema compiler.

A ``Message`` keeps its fields in the order they arrived, each as its
field number, wire type and raw value. Reading a field decodes it on
demand; writing one replaces it in place. A field nothing here knows
about is carried through untouched, so an item read from KiCad, changed
in one field and sent back, keeps every other property exactly as KiCad
sent it. Encoding an unchanged message gives back the same bytes.

Field numbers live with the code that uses them (``kicad_api.py``); this
module knows only the wire format.
"""
from __future__ import annotations

import struct
from typing import Iterable, Optional, Union

VARINT, FIXED64, BYTES, FIXED32 = 0, 1, 2, 5

Value = Union[int, bytes]


def encode_varint(n: int) -> bytes:
    if n < 0:
        n += 1 << 64  # int32/int64 negatives travel as 64-bit two's complement
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    shift = value = 0
    while True:
        if pos >= len(data):
            raise ValueError("truncated varint")
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos
        if shift > 63:
            raise ValueError("varint longer than 64 bits")


def signed(n: int) -> int:
    """A decoded varint read as int64."""
    return n - (1 << 64) if n >= 1 << 63 else n


class Message:
    """One protobuf message as an ordered list of [number, wire type, value]."""

    __slots__ = ("fields",)

    def __init__(self, fields: Optional[list] = None):
        self.fields: list[list] = fields if fields is not None else []

    # -- wire ---------------------------------------------------------------
    @classmethod
    def decode(cls, data: bytes) -> "Message":
        fields: list[list] = []
        pos = 0
        while pos < len(data):
            key, pos = decode_varint(data, pos)
            num, wire = key >> 3, key & 7
            if wire == VARINT:
                value, pos = decode_varint(data, pos)
            elif wire == BYTES:
                size, pos = decode_varint(data, pos)
                if pos + size > len(data):
                    raise ValueError(f"field {num} runs past the message end")
                value = bytes(data[pos:pos + size])
                pos += size
            elif wire == FIXED64:
                value = bytes(data[pos:pos + 8])
                pos += 8
            elif wire == FIXED32:
                value = bytes(data[pos:pos + 4])
                pos += 4
            else:
                raise ValueError(f"field {num}: unsupported wire type {wire}")
            fields.append([num, wire, value])
        return cls(fields)

    def encode(self) -> bytes:
        out = bytearray()
        for num, wire, value in self.fields:
            out += encode_varint(num << 3 | wire)
            if wire == VARINT:
                out += encode_varint(value)
            elif wire == BYTES:
                out += encode_varint(len(value)) + value
            else:
                out += value
        return bytes(out)

    def copy(self) -> "Message":
        return Message([list(f) for f in self.fields])

    # -- reading ------------------------------------------------------------
    def has(self, num: int) -> bool:
        return any(f[0] == num for f in self.fields)

    def raw(self, num: int) -> list[Value]:
        return [f[2] for f in self.fields if f[0] == num]

    def int(self, num: int, default: int = 0) -> int:
        values = self.ints(num)
        return values[-1] if values else default

    def sint(self, num: int, default: int = 0) -> int:
        return signed(self.int(num, default))

    def ints(self, num: int) -> list[int]:
        """A repeated integer field, packed or not."""
        out: list[int] = []
        for f in self.fields:
            if f[0] != num:
                continue
            if f[1] == VARINT:
                out.append(f[2])
            elif f[1] == BYTES:
                pos = 0
                while pos < len(f[2]):
                    value, pos = decode_varint(f[2], pos)
                    out.append(value)
        return out

    def bool(self, num: int) -> bool:
        return bool(self.int(num))

    def double(self, num: int, default: float = 0.0) -> float:
        values = [v for v in self.raw(num) if isinstance(v, bytes) and len(v) == 8]
        return struct.unpack("<d", values[-1])[0] if values else default

    def str(self, num: int, default: str = "") -> str:
        values = self.raw(num)
        return values[-1].decode("utf-8") if values else default

    def bytes(self, num: int) -> bytes:
        values = self.raw(num)
        return values[-1] if values else b""

    def msg(self, num: int) -> Optional["Message"]:
        """The sub-message in field ``num``, or None when it is absent."""
        values = self.raw(num)
        return Message.decode(values[-1]) if values else None

    def msgs(self, num: int) -> list["Message"]:
        return [Message.decode(v) for v in self.raw(num)]

    # -- writing ------------------------------------------------------------
    def _put(self, num: int, wire: int, value: Value) -> "Message":
        for f in self.fields:
            if f[0] == num:
                f[1], f[2] = wire, value
                self.fields = [g for g in self.fields if g[0] != num or g is f]
                return self
        self.fields.append([num, wire, value])
        self.fields.sort(key=lambda g: g[0])  # stable: repeated fields keep order
        return self

    def remove(self, num: int) -> "Message":
        self.fields = [f for f in self.fields if f[0] != num]
        return self

    def set_int(self, num: int, value: int) -> "Message":
        return self._put(num, VARINT, int(value))

    def set_bool(self, num: int, value: bool) -> "Message":
        return self._put(num, VARINT, 1 if value else 0)

    def set_double(self, num: int, value: float) -> "Message":
        return self._put(num, FIXED64, struct.pack("<d", float(value)))

    def set_str(self, num: int, value: str) -> "Message":
        return self._put(num, BYTES, str(value).encode("utf-8"))

    def set_bytes(self, num: int, value: bytes) -> "Message":
        return self._put(num, BYTES, bytes(value))

    def set_msg(self, num: int, value: "Message") -> "Message":
        return self._put(num, BYTES, value.encode())

    def set_msgs(self, num: int, values: Iterable["Message"]) -> "Message":
        self.remove(num)
        for v in values:
            self.fields.append([num, BYTES, v.encode()])
        self.fields.sort(key=lambda g: g[0])
        return self

    def set_ints(self, num: int, values: Iterable[int]) -> "Message":
        """A repeated integer field, packed as proto3 sends it."""
        self.remove(num)
        packed = b"".join(encode_varint(int(v)) for v in values)
        if packed:
            self.fields.append([num, BYTES, packed])
            self.fields.sort(key=lambda g: g[0])
        return self

    def __eq__(self, other) -> bool:
        return isinstance(other, Message) and self.encode() == other.encode()

    def __repr__(self) -> str:
        return f"Message({[(f[0], f[2] if f[1] == VARINT else len(f[2])) for f in self.fields]})"
