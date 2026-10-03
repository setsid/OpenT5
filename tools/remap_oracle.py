"""Oracle tool: resize one inline string in a zone and remap every offset pointer,
using the game's own XFile loader (run in a local PowerPC interpreter) as the
source of truth. Not product code.

Why an oracle. A fastfile is a memory image: offset pointers are encoded as
((block << 29) | offset) + 1 and point into the in-memory block, not into the file.
Growing a string inside VIRTUAL (block 4) moves everything loaded after it in that
block, and every later DB_AllocStreamPos re-aligns, so the shift is piecewise. The
product remapper (another track) derives the layout from its own struct walker; this
tool derives it from the shipped loader instead, so the two can be compared byte for
byte (fixture tests/fixtures/remap_<zone>.json).

How it works.
1. Trace. The harness in .oracle/r2b (emu.py, ppc.py: a scratch PPC64 interpreter
   that runs t5mp.elf's loader functions over a decompressed zone, with only the file
   reader 0x233558 and the string reader 0x2335d0 replaced) is run over the original
   content. Hooks on the stream primitives record, for VIRTUAL:
     DB_AllocStreamPos 0x26aca0   ('A', mask)        position aligned
     DB_IncStreamPos   0x26acc0   ('I', size, file)  position advanced; `file` is the
                                                     file offset of the bytes just read
                                                     there (Load_Stream / string read)
     DB_InsertPointer  0x26acd8   ('P',)             align 4 + reserve 4 (alias slot,
                                                     no file bytes)
   and every offset-pointer conversion:
     DB_ConvertOffsetToPointer 0x26add8  *p = blocks[b].data + off
     DB_ConvertOffsetToAlias   0x26ada0  *p = *(u32 *)(blocks[b].data + off)
   (roles confirmed from the disassembly: 0x26ada0 has the extra `lwz r0,0(r9)`).
   The field address r3 is mapped back to a file offset through the read log (the
   most recent read that covered it; TEMP memory is reused, so "most recent" matters).
2. Relayout. The VIRTUAL op list is replayed (every logged position is checked) and
   then replayed again with the edited string 3 bytes longer and the same masks. That
   gives old -> new start of every VIRTUAL allocation.
3. Rewrite. Each pointer into VIRTUAL is mapped through the allocation that contains
   its target (keeping the intra-allocation offset); fields are rewritten in place,
   the string is replaced, header blockSize[4] is set to the new final position.
   Zone.save then derives the size field at content offset 0, re-chunks and rebuilds
   the nonce chain. The RSA signature is carried unchanged.
4. Validate. The emulated loader is run over the edited content: it must consume it
   exactly, end with block positions equal to the new header, and its loaded VIRTUAL
   / PHYSICAL / TEMP images must equal the original ones once each allocation is moved
   back and each pointer value is mapped back.

Usage:  .venv/bin/python tools/remap_oracle.py OUT.ff [FIXTURE.json [LENGTH]]
(code_post_gfx_mp, localize asset 4162, "PLAYER MATCH" -> "OPENT5 REMAP OK"; LENGTH pads the
new value with trailing spaces to that many characters, e.g. 140 for a +0x80 edit).
"""

from __future__ import annotations

import bisect
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / ".oracle" / "r2b"))  # the emulator harness (scratch code)

from emu import BLOCKBASE, BLOCKSTEP, G281, STREAM_POS, Emu  # noqa: E402
from ppc import M32  # noqa: E402

from opent5 import env  # noqa: E402
from opent5.container.zone import Zone, verify  # noqa: E402

VIRTUAL = 4
SAVED_POS = G281 - 22388  # u32[7]: saved position of every block not current
ALLOC, INC, INSERT = 0x26ACA0, 0x26ACC0, 0x26ACD8
TO_POINTER, TO_ALIAS = 0x26ADD8, 0x26ADA0


def block_of(addr: int) -> tuple[int, int]:
    return (addr - BLOCKBASE) // BLOCKSTEP, (addr - BLOCKBASE) % BLOCKSTEP


class TracingEmu(Emu):
    """Emu that records VIRTUAL position ops, reads and pointer conversions."""

    def __init__(self, content: bytes):
        self.ops: list[tuple] = []  # (kind, arg, pos_before, file_offset)
        self.reads: list[tuple[int, int, int]] = []  # (dst address, size, file offset)
        # (function, field address, raw value, file offset of the field)
        self.convs: list[tuple[int, int, int, int | None]] = []
        self._last_read = None
        super().__init__(content, log=False)
        for a in (ALLOC, INC, INSERT, TO_POINTER, TO_ALIAS):
            self.c.hooks[a] = self._hook

    def vpos(self) -> int:
        """VIRTUAL position as an offset into the block (the globals hold addresses)."""
        cur = self.pos() if self.block() == VIRTUAL else self.m.r32(SAVED_POS + 4 * VIRTUAL)
        return cur - self.bbase[VIRTUAL]

    def _hook(self, c):
        a = c.pc
        r3 = c.r[3] & M32
        if a in (TO_POINTER, TO_ALIAS):
            # Resolve the field's file offset now: TEMP memory is reused later.
            self.convs.append((a, r3, self.m.r32(r3), self.file_offset_of(r3)))
        elif a == INSERT:
            self.ops.append(("P", 0, self.vpos(), None))
        elif self.block() == VIRTUAL:
            if a == ALLOC:
                self.ops.append(("A", r3, self.vpos(), None))
            else:
                fo = None
                lr = self._last_read
                if lr and lr[0] == self.bbase[VIRTUAL] + self.vpos() and lr[1] == r3:
                    fo = lr[2]
                self.ops.append(("I", r3, self.vpos(), fo))
        if a == INC:
            self._last_read = None
        return False

    def _stub(self, c, a):
        fp = self.fp
        if a == 0x233558:
            dst, n = c.r[3] & M32, c.r[4] & M32
        elif a == 0x2335D0:
            dst, n = c.r[3] & M32, self.z.index(b"\0", fp) + 1 - fp
        else:
            dst = None
        handled = super()._stub(c, a)
        if dst is not None and n:
            self.reads.append((dst, n, fp))
            self._last_read = (dst, n, fp)
        return handled

    def run(self) -> int:
        consumed = self.load_zone()
        self.read_starts = [r[0] for r in self.reads]
        return consumed

    def file_offset_of(self, addr: int) -> int | None:
        """File offset of the byte at `addr`: the most recent read covering it so far."""
        for dst, n, fo in reversed(self.reads):
            if dst <= addr < dst + n:
                return fo + addr - dst
        return None

    def final_positions(self) -> list[int]:
        pos = [self.m.r32(SAVED_POS + 4 * i) for i in range(7)]
        pos[self.block()] = self.pos()
        return [p - b for p, b in zip(pos, self.bbase)]

    def image(self, b: int, size: int) -> bytes:
        return self.m.read(self.bbase[b], size)


def replay(ops, grow_file_offset: int | None = None, delta: int = 0, check: bool = False):
    """Re-simulate VIRTUAL positions. Returns segments
    [(old_start, new_start, old_len, new_len, file_offset)] and the final position."""
    pos = 0
    segs = []
    for kind, arg, before, fo in ops:
        if check and pos != before:
            raise AssertionError(f"replay drift: expected {before:#x}, have {pos:#x} ({kind})")
        if kind == "A":
            pos = (pos + arg) & ~arg
        elif kind == "P":
            pos = (pos + 3) & ~3
            segs.append((before, pos, 4, 4, None))
            pos += 4
        else:
            n = arg + (delta if fo is not None and fo == grow_file_offset else 0)
            segs.append((before, pos, arg, n, fo))
            pos += n
    return segs, pos


def alignment_effects(ops, grow_file_offset: int, delta: int):
    """Every DB_AllocStreamPos in VIRTUAL met while the shift is non-zero, as
    {(mask, shift before, shift after): count}, and the old position where the shift
    changes."""
    po = pn = 0
    seen: dict[tuple[int, int, int], int] = {}
    changes = []
    for kind, arg, _, fo in ops:
        if kind == "A":
            no, nn = (po + arg) & ~arg, (pn + arg) & ~arg
            if pn != po:
                key = (arg, pn - po, nn - no)
                seen[key] = seen.get(key, 0) + 1
                if nn - no != pn - po:
                    changes.append((po, arg, pn - po, nn - no))
            po, pn = no, nn
        elif kind == "P":
            po, pn = ((po + 3) & ~3) + 4, ((pn + 3) & ~3) + 4
        else:
            po += arg
            pn += arg + (delta if fo == grow_file_offset else 0)
    return seen, changes


class Relayout:
    def __init__(self, ops, grow_at: int, delta: int):
        # The unchanged replay (every logged position checked) gives the old starts.
        old, self.old_end = replay(ops, check=True)
        new, self.new_end = replay(ops, grow_at, delta)
        self.segs = []  # (old_start, old_len, new_start, new_len, file_offset)
        for (_, os_, ol, _, fo), (_, ns, _, nl, _) in zip(old, new, strict=True):
            if ol:
                self.segs.append((os_, ol, ns, nl, fo))
        self.starts = [s[0] for s in self.segs]
        self.gap_hits = 0

    def map(self, t: int) -> int:
        i = bisect.bisect_right(self.starts, t) - 1
        if i >= 0:
            os_, ol, ns, nl, _ = self.segs[i]
            if t < os_ + ol:
                return ns + (t - os_)
        # padding or end of block: not inside any allocation
        self.gap_hits += 1
        if t == self.old_end:
            return self.new_end
        raise ValueError(f"VIRTUAL target {t:#x} lies in no allocation")

    def unmap(self, t: int) -> int:
        """new -> old, for validation."""
        ns_list = getattr(self, "_ns", None)
        if ns_list is None:
            self._ns = ns_list = [s[2] for s in self.segs]
        i = bisect.bisect_right(ns_list, t) - 1
        os_, ol, ns, nl, _ = self.segs[i]
        if t < ns + nl:
            return os_ + min(t - ns, ol)
        raise ValueError(f"new VIRTUAL address {t:#x} lies in no allocation")

    def runs(self):
        """[old_start, delta] breakpoints: delta applies from old_start on."""
        out = []
        for os_, ol, ns, nl, fo in self.segs:
            d = ns - os_
            if not out or out[-1][1] != d:
                out.append([os_, d])
        return out


def _count(items):
    out: dict[int, int] = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return out


def build(content: bytes, value_offset: int, old: bytes, new: bytes):
    tr = TracingEmu(content)
    consumed = tr.run()
    if consumed != len(content):
        raise AssertionError(f"original: loader consumed {consumed:#x}, expected {len(content):#x}")
    hdr = struct.unpack_from(">9I", content, 0)
    delta = len(new) - len(old)
    assert content[value_offset : value_offset + len(old) + 1] == old + b"\0"
    lay = Relayout(tr.ops, value_offset, delta)
    if lay.old_end != hdr[2 + VIRTUAL]:
        raise AssertionError(f"VIRTUAL end {lay.old_end:#x}, header says {hdr[2 + VIRTUAL]:#x}")
    grown = [s for s in lay.segs if s[4] == value_offset]
    assert len(grown) == 1, grown
    string_old_pos = grown[0][0]

    # Pointer fields.
    pointers = []  # (file_offset, function, old_value, new_value)
    unresolved = 0
    by_block: dict[int, int] = {}
    for fn, field, value, fo in tr.convs:
        if fo is None:
            unresolved += 1
            continue
        assert struct.unpack_from(">I", content, fo)[0] == value, (hex(fo), hex(value))
        b, off = (value - 1) >> 29, (value - 1) & 0x1FFFFFFF
        by_block[b] = by_block.get(b, 0) + 1
        nv = value
        if b == VIRTUAL:
            nv = ((b << 29) | lay.map(off)) + 1
        pointers.append((fo, fn, value, nv))
    if unresolved:
        raise AssertionError(f"{unresolved} converted fields map to no file offset")

    out = bytearray(content)
    for fo, _, v, nv in pointers:
        if nv != v:
            struct.pack_into(">I", out, fo, nv)
    out[value_offset : value_offset + len(old)] = new
    struct.pack_into(">I", out, 8 + 4 * VIRTUAL, lay.new_end)
    struct.pack_into(">I", out, 0, len(out) - 36)
    stats = dict(
        consumed=consumed,
        conversions=len(tr.convs),
        by_block=by_block,
        remapped=sum(1 for p in pointers if p[3] != p[2]),
        string_old_pos=string_old_pos,
        old_end=lay.old_end,
        new_end=lay.new_end,
        gap_hits=lay.gap_hits,
        alias_conversions=sum(1 for c in tr.convs if c[0] == TO_ALIAS),
        remapped_fields_in_block=_count(
            block_of(c[1])[0] for c, p in zip(tr.convs, pointers) if p[3] != p[2]
        ),
        pointers_to_string=sum(
            1 for c in tr.convs if (c[2] - 1) & 0x1FFFFFFF == string_old_pos
            and (c[2] - 1) >> 29 == VIRTUAL
        ),
    )
    seen, changes = alignment_effects(tr.ops, value_offset, delta)
    stats["alignments_while_shifted"] = {f"mask{k[0]}:{k[1]}->{k[2]}": n for k, n in seen.items()}
    stats["shift_changes"] = [[hex(a), m, b, c] for a, m, b, c in changes]
    return bytes(out), tr, lay, pointers, stats


def validate(orig: bytes, edited: bytes, tr0: TracingEmu, lay: Relayout, new_text: bytes):
    tr1 = TracingEmu(edited)
    consumed = tr1.run()
    hdr = struct.unpack_from(">9I", edited, 0)
    report = {"consumed_ok": consumed == len(edited)}
    fin = tr1.final_positions()
    # TEMP is rewound on the last pop; its high-water mark is the header value.
    report["final_positions_ok"] = fin[1:] == list(hdr[3:9])
    report["final_positions"] = fin
    report["conversions_same_count"] = len(tr1.convs) == len(tr0.convs)
    # Pointers resolved in the edited run must be the mapped originals.
    bad = 0
    for (f0, a0, v0, _), (f1, a1, v1, _) in zip(tr0.convs, tr1.convs, strict=True):
        b0, o0 = (v0 - 1) >> 29, (v0 - 1) & 0x1FFFFFFF
        exp = ((b0 << 29) | (lay.map(o0) if b0 == VIRTUAL else o0)) + 1
        if f0 != f1 or v1 != exp:
            bad += 1
    report["conversion_values_mismatch"] = bad
    # Image comparison, VIRTUAL: move every allocation back, map pointers back.
    old_img = tr0.image(VIRTUAL, lay.old_end)
    new_img = tr1.image(VIRTUAL, lay.new_end)
    rebuilt = bytearray(old_img)
    for os_, ol, ns, nl, fo in lay.segs:
        if nl == ol:
            rebuilt[os_ : os_ + ol] = new_img[ns : ns + nl]
    base_old, base_new = tr0.bbase[VIRTUAL], tr1.bbase[VIRTUAL]
    explained = unexplained = 0
    pointed_checked = pointed_bad = pointed_raw_equal = 0

    def explain(img_old, img_rebuilt, size):
        nonlocal explained, unexplained
        i = 0
        while i < size:
            if img_old[i] == img_rebuilt[i]:
                i += 1
                continue
            ok = False
            for j in range(max(0, i - 3), i + 1):
                w0 = struct.unpack_from(">I", img_old, j)[0]
                w1 = struct.unpack_from(">I", img_rebuilt, j)[0]
                if base_old <= w0 <= base_old + lay.old_end and base_new <= w1 <= base_new + lay.new_end:
                    try:
                        hit = base_new + lay.map(w0 - base_old) == w1
                    except ValueError:
                        hit = False
                    if hit:
                        img_rebuilt[j : j + 4] = img_old[j : j + 4]
                        explained += 1
                        ok = True
                        break
            if not ok:
                unexplained += 1
                i += 1
            else:
                i = j + 4

    explain(old_img, rebuilt, lay.old_end)
    # The grown string's allocation is not copied back, so every remaining difference
    # is unexplained (neither relocation nor a mapped pointer).
    grown = [s for s in lay.segs if s[3] != s[1]][0]
    report["virtual_pointer_words_mapped_back"] = explained
    report["virtual_unexplained_bytes"] = unexplained
    report["virtual_diff_bytes_after_mapping"] = sum(
        1 for x, y in zip(old_img, rebuilt) if x != y
    )
    report["grown_allocations"] = sum(1 for s in lay.segs if s[3] != s[1])
    report["string_loaded"] = new_img[grown[2] : grown[2] + grown[3]].rstrip(b"\0").decode()
    # Other blocks: same positions, so compare directly with pointers mapped.
    for b in range(7):
        if b == VIRTUAL or not hdr[2 + b]:
            continue
        o = tr0.image(b, hdr[2 + b])
        n = bytearray(tr1.image(b, hdr[2 + b]))
        before = explained
        explain(o, n, hdr[2 + b])
        report[f"block{b}_pointer_words_mapped_back"] = explained - before
        report[f"block{b}_diff_bytes"] = sum(1 for x, y in zip(o, n) if x != y)
    # Pointed-to content for every VIRTUAL pointer resolved in the edited run.
    for (f0, a0, v0, _), (f1, a1, v1, _) in zip(tr0.convs, tr1.convs, strict=True):
        b0, o0 = (v0 - 1) >> 29, (v0 - 1) & 0x1FFFFFFF
        if b0 != VIRTUAL:
            continue
        o1 = (v1 - 1) & 0x1FFFFFFF
        i = bisect.bisect_right(lay.starts, o0) - 1
        os_, ol, ns, nl, _ = lay.segs[i]
        n = min(16, os_ + ol - o0) if o0 < os_ + ol else 0
        pointed_checked += 1
        if new_img[o1 : o1 + n] == old_img[o0 : o0 + n]:
            pointed_raw_equal += 1
        if bytes(rebuilt[o0 : o0 + n]) != old_img[o0 : o0 + n] or lay.unmap(o1) != o0:
            pointed_bad += 1
    report["pointed_checked"] = pointed_checked
    report["pointed_raw_equal"] = pointed_raw_equal
    report["pointed_bad"] = pointed_bad
    return report


def main(argv):
    out_path = Path(argv[1])
    fixture = Path(argv[2]) if len(argv) > 2 else None
    src = env.path_of("OPENT5_ZONES") / "code_post_gfx_mp.ff"
    zone = Zone.open(src)
    content = bytes(zone.content)
    asset_at = 0x34357F
    value_at = asset_at + 8
    old, new = b"PLAYER MATCH", b"OPENT5 REMAP OK"
    if len(argv) > 3:  # pad the new value with trailing spaces to this many characters
        new = new.ljust(int(argv[3], 0), b" ")
    edited, tr, lay, pointers, stats = build(content, value_at, old, new)
    print("build", json.dumps(stats))
    rep = validate(content, edited, tr, lay, new)
    print("validate", json.dumps(rep))
    zone.content[:] = edited
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = zone.save(out_path)
    back = Zone.open(out_path)
    same = bytes(back.content) == edited
    verify(out_path, expected=edited)
    sha = hashlib.sha1(out_path.read_bytes()).hexdigest()
    print("saved", out_path, "chunks", result.chunks, "kept", result.kept, "reopen_equal", same, "sha1", sha)
    if fixture:
        doc = {
            "block": VIRTUAL,
            "insert_file_offset": value_at + len(old),
            "insert_length": len(new) - len(old),
            "string_file_offset": value_at,
            "string_old_virtual": stats["string_old_pos"],
            "virtual_old_size": lay.old_end,
            "virtual_new_size": lay.new_end,
            "content_old_length": len(content),
            "content_new_length": len(edited),
            # [old VIRTUAL offset, delta]: delta applies from that offset on
            "relayout_runs": lay.runs(),
            # [old file offset of the field, old value, new value], VIRTUAL targets only,
            # only those that changed
            "pointers": [[fo, v, nv] for fo, fn, v, nv in pointers if nv != v],
            "conversions_total": stats["conversions"],
            "conversions_by_block": [stats["by_block"].get(b, 0) for b in range(7)],
            "alignment_changes": [[a, m, b, c] for a, m, b, c in
                                  alignment_effects(tr.ops, value_at, len(new) - len(old))[1]],
            # sha1 of the saved .ff as five big-endian u32
            "output_sha1": [int(sha[i : i + 8], 16) for i in range(0, 40, 8)],
        }
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_text(json.dumps(doc, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
