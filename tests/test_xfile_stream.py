"""The loader primitives over synthetic streams: alignment, blocks, RUNTIME,
the deferred tail, strings, pointer kinds and the event log."""

import struct

import numpy as np
import pytest

from opent5.xfile.constants import (
    PTR_INLINE,
    PTR_INSERT,
    Block,
    decode_offset_pointer,
    encode_offset_pointer,
)
from opent5.xfile.events import NONE, EventKind, PtrKind
from opent5.xfile.stream import Chunk, DeferredData, RuntimeData, XFileError, XStream


def u32s(*values):
    return b"".join(struct.pack(">I", v & 0xFFFFFFFF) for v in values)


def stream(data: bytes) -> XStream:
    st = XStream(data)
    st.push(Block.VIRTUAL)
    return st


def chunk_of(data: bytes, at: int = 0) -> Chunk:
    return Chunk(memoryview(data), at, Block.TEMP, 0)


def events(st, kind):
    return [tuple(int(x) for x in row) for row in st.log.of_kind(kind)]


class TestOffsetPointers:
    @pytest.mark.parametrize("block,offset", [(0, 0), (4, 0x43EC), (6, 0x1FFFFFFF), (3, 1)])
    def test_round_trip(self, block, offset):
        assert decode_offset_pointer(encode_offset_pointer(block, offset)) == (block, offset)

    def test_known_value(self):
        # patch_mp 0x54ac: 0x800043ed -> VIRTUAL + 0x43ec (docs/research/xfile.md s4)
        assert decode_offset_pointer(0x800043ED) == (4, 0x43EC)


class TestPositions:
    def test_alloc_moves_memory_not_the_file(self):
        st = stream(bytes(16))
        st.load(3)
        assert st.alloc(15) == 16
        assert st.fp == 3
        assert st.pos[Block.VIRTUAL] == 16

    def test_load_reads_bytes_and_advances_both(self):
        st = stream(b"abcdefgh")
        st.load(2)
        got = st.load(3)
        assert got.bytes() == b"cde"
        assert (got.at, got.block, got.mem) == (2, Block.VIRTUAL, 2)
        assert st.fp == 5 and st.pos[Block.VIRTUAL] == 5

    def test_temp_rewinds_on_pop(self):
        st = stream(bytes(32))
        st.push(Block.TEMP)
        st.alloc(3)
        st.load(12)
        assert st.pos[Block.TEMP] == 12
        st.pop()
        assert st.pos[Block.TEMP] == 0
        assert st.temp_high == 12
        assert st.cur == Block.VIRTUAL

    def test_other_blocks_keep_their_position_on_pop(self):
        st = stream(bytes(32))
        st.push(Block.PHYSICAL)
        st.load(8)
        st.pop()
        assert st.pos[Block.PHYSICAL] == 8

    def test_runtime_has_no_file_bytes(self):
        st = stream(b"xyz")
        st.push(Block.RUNTIME)
        st.alloc(31)
        got = st.reserve(0x1000)
        st.pop()
        assert isinstance(got, RuntimeData)
        assert st.fp == 0
        assert st.pos[Block.RUNTIME] == 0x1000
        assert events(st, EventKind.READ) == [(EventKind.READ, NONE, 0x1000, 1, 0, 31)]

    def test_deferred_reads_come_after_everything_else(self):
        st = stream(b"HEAD" + b"PIX1" + b"PIX22")
        st.push(Block.PHYSICAL_RUNTIME)
        first = st.reserve(4)
        st.alloc(127)
        second = st.reserve(5)
        st.pop()
        st.load(4)
        assert isinstance(first, DeferredData) and first.file_offset is None
        st.pop()
        st.flush_deferred()
        assert bytes(first.data) == b"PIX1" and first.file_offset == 4
        assert bytes(second.data) == b"PIX22" and second.file_offset == 8
        assert second.mem == 128
        assert st.fp == 13
        tail = events(st, EventKind.TAIL)
        assert tail == [(EventKind.TAIL, 4, 4, 3, 0, 0), (EventKind.TAIL, 8, 5, 3, 128, 1)]

    def test_load_past_the_end_names_the_offset(self):
        st = stream(b"abc")
        with pytest.raises(XFileError, match=r"expected at most 3 left.*0x3"):
            st.load(4)

    def test_load_rejects_blocks_without_file_bytes(self):
        st = stream(b"abc")
        st.push(Block.RUNTIME)
        with pytest.raises(XFileError, match="block 1"):
            st.load(1)

    def test_insert_reserves_an_aligned_slot_in_virtual(self):
        st = stream(bytes(8))
        st.load(5)
        st.push(Block.TEMP)
        assert st.insert() == 8
        assert st.pos[Block.VIRTUAL] == 12
        assert st.pos[Block.TEMP] == 0


class TestStrings:
    def test_inline_string(self):
        st = stream(u32s(PTR_INLINE) + b"name\0rest")
        head = st.load(4)
        assert st.string(head, 0) == "name"
        assert st.fp == 9
        assert st.pos[Block.VIRTUAL] == 9

    def test_offset_string_resolves_to_the_earlier_one(self):
        data = u32s(PTR_INLINE) + b"shared\0" + u32s(encode_offset_pointer(4, 4))
        st = stream(data)
        first = st.load(4)
        assert st.string(first, 0) == "shared"
        second = st.load(4)
        assert st.string(second, 0) == "shared"
        assert st.fp == len(data)

    def test_null_string(self):
        st = stream(u32s(0))
        assert st.string(st.load(4), 0) is None

    def test_missing_nul(self):
        st = stream(u32s(PTR_INLINE) + b"abc")
        with pytest.raises(XFileError, match="NUL"):
            st.string(st.load(4), 0)


class TestPointerKinds:
    def kinds(self, st):
        return [(row[2], row[3]) for row in events(st, EventKind.POINTER)]

    def test_shareable_field(self):
        st = stream(u32s(0, PTR_INLINE, encode_offset_pointer(4, 0x10)))
        c = st.load(12)
        assert st.follows(c, 0) is False
        assert st.follows(c, 4) is True
        assert st.follows(c, 8) is False
        assert self.kinds(st) == [
            (0, PtrKind.NULL),
            (PTR_INLINE, PtrKind.INLINE),
            (0x80000011, PtrKind.OFFSET),
        ]
        last = events(st, EventKind.POINTER)[-1]
        assert last[1] == 8 and (last[4], last[5]) == (4, 0x10)

    def test_owned_field_any_non_zero_is_inline(self):
        st = stream(u32s(0x12345678))
        c = st.load(4)
        assert st.follows(c, 0, owned=True) is True
        assert self.kinds(st) == [(0x12345678, PtrKind.NONZERO_INLINE)]

    def test_asset_reference_kinds(self):
        st = stream(u32s(PTR_INLINE, PTR_INSERT, encode_offset_pointer(4, 8), 0))
        c = st.load(16)
        for off in range(0, 16, 4):
            st.ref(c, off)
        assert [k for _, k in self.kinds(st)] == [
            PtrKind.INLINE,
            PtrKind.INLINE_ALIAS,
            PtrKind.ALIAS_REF,
            PtrKind.NULL,
        ]

    def test_inline_target_is_filled_by_the_next_alloc(self):
        st = stream(u32s(PTR_INLINE) + bytes(16))
        c = st.load(4)
        assert st.follows(c, 0)
        st.alloc(15)
        st.load(16)
        row = events(st, EventKind.POINTER)[0]
        assert (row[4], row[5]) == (Block.VIRTUAL, 16)

    def test_convert_only_field_rejects_inline(self):
        st = stream(u32s(PTR_INLINE))
        with pytest.raises(XFileError, match="converted only"):
            st.convert(st.load(4), 0)

    def test_field_file_offset_follows_slices(self):
        st = stream(bytes(8) + u32s(0, encode_offset_pointer(4, 0)))
        st.load(8)
        c = st.load(8).sub(4, 4)
        st.follows(c, 0)
        assert events(st, EventKind.POINTER)[0][1] == 12


class TestEventLog:
    def test_records_are_six_words(self):
        st = stream(u32s(PTR_INLINE) + b"s\0")
        st.string(st.load(4), 0)
        table = st.log.table()
        assert table.dtype == np.uint32 and table.shape[1] == 6
        assert [EventKind(k).name for k in table[:, 0]] == [
            "PUSH",
            "READ",
            "POINTER",
            "ALLOC",
            "STRING",
        ]
        assert st.log.counts()["READ"] == 1
        assert st.log.nbytes == 4 * 6 * len(st.log)

    def test_read_carries_the_alignment_just_applied(self):
        st = stream(bytes(8))
        st.load(1)
        st.alloc(3)
        st.load(4)
        st.load(1)
        reads = events(st, EventKind.READ)
        assert [r[5] for r in reads] == [NONE, 3, NONE]

    def test_logging_can_be_disabled(self):
        st = XStream(b"abcd", log=False)
        st.push(Block.VIRTUAL)
        st.load(4)
        assert st.log is None
        assert st.fp == 4

    def test_chunk_accessors(self):
        c = chunk_of(struct.pack(">BbHhIif", 200, -2, 0xBEEF, -3, 0xDEADBEEF, -4, 1.5), at=10)
        assert (c.u8(0), c.s8(1), c.u16(2), c.s16(4)) == (200, -2, 0xBEEF, -3)
        assert (c.u32(6), c.s32(10), c.f32(14)) == (0xDEADBEEF, -4, 1.5)
        part = c.sub(6, 4)
        assert part.at == 16 and part.u32(0) == 0xDEADBEEF
        assert [x.bytes() for x in chunk_of(b"aabbcc").items(2, 3)] == [b"aa", b"bb", b"cc"]
