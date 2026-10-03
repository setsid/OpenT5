"""Run t5mp.elf's own XFile loader functions in the PowerPC interpreter (ppc.py) over a
decompressed zone, logging every stream primitive. A test oracle for the parser, the writer
and the remap (docs/research/remap.md); local emulation only. The ELF path comes from
OPENT5_ELF in .env."""

import struct

from ppc import CPU, M32, Mem

from opent5 import env

ELF = str(env.path_of("OPENT5_ELF") or "t5mp.elf")
MAGIC = 0x10200  # stop address
BLOCKBASE = 0x30000000
BLOCKSTEP = 0x08000000
G218 = 218 << 16
G281 = 281 << 16
STREAM_IDX = G281 - 22360
STREAM_POS = G281 + 10420

PRIM = {
    0x26AEB8: "Load_Stream",
    0x26AB78: "PushStreamPos",
    0x26AC08: "PopStreamPos",
    0x26ACA0: "AllocStreamPos",
    0x26ACC0: "IncStreamPos",
    0x26ACD8: "InsertPointer",
    0x26ADA0: "ConvertOffsetToAlias",
    0x26ADD8: "ConvertOffsetToPointer",
    0x26AE08: "Load_XStringRaw",
    0x26AFA0: "Load_ScriptStringRaw",
}

LOADER_RANGES = [(0x235000, 0x258000), (0x26AAC0, 0x26B000), (0x736EC0, 0x736EC0 + 404 * 4)]

_ELFIMG = None


class Emu:
    def __init__(self, zone, log=True, extra_native=()):
        global _ELFIMG
        self.z = open(zone, "rb").read() if isinstance(zone, str) else zone
        self.log = [] if log else None
        self.stubbed = {}
        self.native = list(LOADER_RANGES) + list(extra_native)
        m = self.m = Mem()
        if _ELFIMG is None:
            _ELFIMG = open(ELF, "rb").read()
        elf = _ELFIMG
        m.map(0x10000, 0xB20000, elf[0:0xB1AC68])
        rwsz = (0x1C79798 + 0xFFFF) & ~0xFFFF
        m.map(0xB30000, rwsz, elf[0xB20000 : 0xB20000 + 0x376BC])
        m.map(0x20000000, 0x200000)
        self.sp = 0x201F0000
        m.map(0x28000000, 0x10000)
        hdr = struct.unpack(">9I", self.z[:36])
        self.hdr = hdr
        self.bsizes = hdr[2:9]
        tbl = b""
        self.bbase = []
        for i, s in enumerate(self.bsizes):
            base = BLOCKBASE + i * BLOCKSTEP
            m.map(base, s + 0x10000)
            self.bbase.append(base)
            tbl += struct.pack(">II", base, s)
        m.write(0x28000000, tbl)
        c = self.c = CPU(m)
        c.stub = self._stub
        for a in PRIM:
            c.hooks[a] = self._prim
        self.fp = 36
        self.call(0x26AAC0, 0x28000000)

    def block(self):
        return self.m.r32(STREAM_IDX)

    def pos(self):
        return self.m.r32(STREAM_POS)

    def _prim(self, c):
        if self.log is not None:
            self.log.append(
                (
                    PRIM[c.pc],
                    c.lr & M32,
                    c.r[3] & M32,
                    c.r[4] & M32,
                    c.r[5] & M32,
                    self.block(),
                    self.pos(),
                    self.fp,
                )
            )
        return False

    def _stub(self, c, a):
        if a == MAGIC:
            return False
        for s, e in self.native:
            if s <= a < e:
                return False
        if a == 0x233558:  # DB_ReadXFile(ptr, size)
            p, n = c.r[3] & M32, c.r[4] & M32
            self.m.write(p, self.z[self.fp : self.fp + n])
            if self.log is not None:
                self.log.append(("READ", c.lr & M32, p, n, 0, self.block(), self.pos(), self.fp))
            self.fp += n
        elif a == 0x2335D0:  # DB_ReadXString(ptr) -> len incl NUL
            p = c.r[3] & M32
            e = self.z.index(b"\0", self.fp) + 1
            self.m.write(p, self.z[self.fp : e])
            if self.log is not None:
                self.log.append(
                    ("READSTR", c.lr & M32, p, e - self.fp, 0, self.block(), self.pos(), self.fp)
                )
            c.r[3] = e - self.fp
            self.fp = e
        else:
            self.stubbed[a] = self.stubbed.get(a, 0) + 1
            if self.log is not None:
                self.log.append(
                    (
                        "STUB",
                        c.lr & M32,
                        c.r[3] & M32,
                        c.r[4] & M32,
                        a,
                        self.block(),
                        self.pos(),
                        self.fp,
                    )
                )
        c.pc = c.lr & M32
        return True

    def call(self, f, *args):
        c = self.c
        for i, v in enumerate(args):
            c.r[3 + i] = v & 0xFFFFFFFFFFFFFFFF
        c.r[1] = self.sp
        c.lr = MAGIC
        c.pc = f
        c.run(MAGIC)
        return c.r[3] & M32

    def load_zone(self, on_asset=None, limit=None):
        L = 0x28001000
        lst = self.z[36:52]
        self.m.write(L, lst)
        self.fp = 52
        self.call(0x26AB78, 4)
        self.m.w32(G218 - 16748, L)
        self.call(0x2389F0, 0)
        self.call(0x26AC08)
        self.call(0x26AB78, 4)
        cnt, ap = struct.unpack(">II", lst[8:16])
        self.assets = []
        self.list_start = self.fp
        if ap:
            p = self.call(0x26ACA0, 3)
            self.m.w32(L + 12, p)
            self.call(0x26AEB8, 1, p, cnt * 8)
            for i in range(cnt if limit is None else min(cnt, limit)):
                t = self.m.r32(p + 8 * i)
                start = self.fp
                li = len(self.log) if self.log is not None else 0
                self.m.w32(G218 - 15472, p + 8 * i)
                self.call(0x256E60, 0)
                self.assets.append((i, t, start, self.fp, li))
                if on_asset:
                    on_asset(self, i, t, start, self.fp)
        self.call(0x26AC08)
        self.deferred_start = self.fp
        self.call(0x26AE38)
        return self.fp
