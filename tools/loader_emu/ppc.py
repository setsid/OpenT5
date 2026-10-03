"""A minimal big-endian PPC64 integer interpreter: enough of the Cell PPU instruction set to run
t5mp.elf's zone loader (tools/loader_emu/emu.py)."""

import struct

M32 = 0xFFFFFFFF
M64 = 0xFFFFFFFFFFFFFFFF


def s32(x):
    x &= M32
    return x - (1 << 32) if x & 0x80000000 else x


def s64(x):
    x &= M64
    return x - (1 << 64) if x >> 63 else x


def sext16(x):
    return x - 0x10000 if x & 0x8000 else x


def rotl32(x, n):
    x &= M32
    return ((x << n) | (x >> (32 - n))) & M32 if n else x


def rotl64(x, n):
    x &= M64
    return ((x << n) | (x >> (64 - n))) & M64 if n else x


def mask32(mb, me):
    if mb <= me:
        m = 0
        for i in range(mb, me + 1):
            m |= 1 << (31 - i)
        return m
    return (~mask32(me + 1, mb - 1)) & M32 if me + 1 <= mb - 1 else M32


def mask64(mb, me):
    if mb <= me:
        return ((1 << (64 - mb)) - 1) ^ ((1 << (63 - me)) - 1)
    return (~mask64(me + 1, mb - 1)) & M64


class Mem:
    def __init__(self):
        self.regions = []  # (start, end, bytearray)
        self.last = None

    def map(self, start, size, data=b""):
        b = bytearray(size)
        b[: len(data)] = data
        self.regions.append((start, start + size, b))
        return b

    def find(self, a, n=1):
        l = self.last
        if l and l[0] <= a and a + n <= l[1]:
            return l
        for r in self.regions:
            if r[0] <= a and a + n <= r[1]:
                self.last = r
                return r
        raise MemoryError("unmapped 0x%x" % a)

    def read(self, a, n):
        s, e, b = self.find(a, n)
        return bytes(b[a - s : a - s + n])

    def write(self, a, data):
        s, e, b = self.find(a, len(data))
        b[a - s : a - s + len(data)] = data

    def r8(self, a):
        s, e, b = self.find(a)
        return b[a - s]

    def r16(self, a):
        s, e, b = self.find(a, 2)
        o = a - s
        return (b[o] << 8) | b[o + 1]

    def r32(self, a):
        s, e, b = self.find(a, 4)
        o = a - s
        return int.from_bytes(b[o : o + 4], "big")

    def r64(self, a):
        s, e, b = self.find(a, 8)
        o = a - s
        return int.from_bytes(b[o : o + 8], "big")

    def w8(self, a, v):
        s, e, b = self.find(a)
        b[a - s] = v & 0xFF

    def w16(self, a, v):
        s, e, b = self.find(a, 2)
        o = a - s
        b[o : o + 2] = (v & 0xFFFF).to_bytes(2, "big")

    def w32(self, a, v):
        s, e, b = self.find(a, 4)
        o = a - s
        b[o : o + 4] = (v & M32).to_bytes(4, "big")

    def w64(self, a, v):
        s, e, b = self.find(a, 8)
        o = a - s
        b[o : o + 8] = (v & M64).to_bytes(8, "big")


class CPU:
    def __init__(self, mem):
        self.m = mem
        self.r = [0] * 32
        self.f = [0] * 32  # raw 64-bit
        self.v = [0] * 32  # raw 128-bit
        self.cr = [0] * 8  # 4-bit fields: LT GT EQ SO = 8 4 2 1
        self.lr = 0
        self.ctr = 0
        self.ca = 0
        self.pc = 0
        self.hooks = {}
        self.stub = None  # fn(cpu, addr) -> bool handled
        self.cache = {}
        self.count = 0
        self.on_stw = None
        self.fstarts = set()
        self.on_call = None
        self.on_ret = None

    def setcr(self, f, v, signed=True):
        if v < 0:
            self.cr[f] = 8
        elif v > 0:
            self.cr[f] = 4
        else:
            self.cr[f] = 2

    def cmp(self, f, a, b):
        self.cr[f] = 8 if a < b else (4 if a > b else 2)

    def rc(self, rD):
        self.setcr(0, s64(self.r[rD]))

    def cond(self, bo, bi):
        ok = True
        if not (bo & 4):
            self.ctr = (self.ctr - 1) & M64
            z = (self.ctr & M32) == 0
            ok = z if (bo & 2) else not z
        if not (bo & 16):
            bit = (self.cr[bi >> 2] >> (3 - (bi & 3))) & 1
            ok = ok and (bit == ((bo >> 3) & 1))
        return ok

    def run(self, until, limit=50_000_000):
        while True:
            pc = self.pc
            if pc == until:
                return
            h = self.hooks.get(pc)
            if h is not None:
                if h(self) is True:
                    continue
            if self.stub and self.stub(self, pc):
                continue
            self.step()
            self.count += 1

    def step(self):
        pc = self.pc
        w = self.m.r32(pc)
        self.pc = pc + 4
        op = w >> 26
        rD = (w >> 21) & 31
        rA = (w >> 16) & 31
        rB = (w >> 11) & 31
        imm = w & 0xFFFF
        r = self.r
        m = self.m
        if op == 14:  # addi
            r[rD] = ((r[rA] if rA else 0) + sext16(imm)) & M64
        elif op == 15:  # addis
            r[rD] = ((r[rA] if rA else 0) + (sext16(imm) << 16)) & M64
        elif op == 32:  # lwz
            r[rD] = m.r32(((r[rA] if rA else 0) + sext16(imm)) & M32)
        elif op == 33:  # lwzu
            a = (r[rA] + sext16(imm)) & M32
            r[rD] = m.r32(a)
            r[rA] = a
        elif op == 34:
            r[rD] = m.r8(((r[rA] if rA else 0) + sext16(imm)) & M32)
        elif op == 35:
            a = (r[rA] + sext16(imm)) & M32
            r[rD] = m.r8(a)
            r[rA] = a
        elif op == 40:
            r[rD] = m.r16(((r[rA] if rA else 0) + sext16(imm)) & M32)
        elif op == 41:
            a = (r[rA] + sext16(imm)) & M32
            r[rD] = m.r16(a)
            r[rA] = a
        elif op == 42:  # lha
            r[rD] = sext16(m.r16(((r[rA] if rA else 0) + sext16(imm)) & M32)) & M64
        elif op == 36:
            a = ((r[rA] if rA else 0) + sext16(imm)) & M32
            m.w32(a, r[rD])
            if self.on_stw:
                self.on_stw(self, a, r[rD] & M32)
        elif op == 37:
            a = (r[rA] + sext16(imm)) & M32
            m.w32(a, r[rD])
            r[rA] = a
        elif op == 38:
            m.w8(((r[rA] if rA else 0) + sext16(imm)) & M32, r[rD])
        elif op == 39:
            a = (r[rA] + sext16(imm)) & M32
            m.w8(a, r[rD])
            r[rA] = a
        elif op == 44:
            m.w16(((r[rA] if rA else 0) + sext16(imm)) & M32, r[rD])
        elif op == 45:
            a = (r[rA] + sext16(imm)) & M32
            m.w16(a, r[rD])
            r[rA] = a
        elif op == 48:  # lfs
            a = ((r[rA] if rA else 0) + sext16(imm)) & M32
            self.f[rD] = ("s", m.r32(a))
        elif op == 50:  # lfd
            a = ((r[rA] if rA else 0) + sext16(imm)) & M32
            self.f[rD] = ("d", m.r64(a))
        elif op == 52:  # stfs
            a = ((r[rA] if rA else 0) + sext16(imm)) & M32
            fv = self.f[rD]
            if isinstance(fv, tuple) and fv[0] == "s":
                m.w32(a, fv[1])
            elif isinstance(fv, tuple):
                m.w32(
                    a,
                    struct.unpack(
                        ">I", struct.pack(">f", struct.unpack(">d", fv[1].to_bytes(8, "big"))[0])
                    )[0],
                )
            else:
                m.w32(a, 0)
        elif op == 54:  # stfd
            a = ((r[rA] if rA else 0) + sext16(imm)) & M32
            fv = self.f[rD]
            if isinstance(fv, tuple) and fv[0] == "d":
                m.w64(a, fv[1])
            elif isinstance(fv, tuple):
                m.w64(
                    a,
                    struct.unpack(
                        ">Q", struct.pack(">d", struct.unpack(">f", fv[1].to_bytes(4, "big"))[0])
                    )[0],
                )
            else:
                m.w64(a, 0)
        elif op == 58:  # ld/ldu/lwa
            ds = sext16(imm & 0xFFFC)
            x = imm & 3
            a = ((r[rA] if rA else 0) + ds) & M32
            if x == 0:
                r[rD] = m.r64(a)
            elif x == 1:
                r[rD] = m.r64(a)
                r[rA] = a
            else:
                r[rD] = s32(m.r32(a)) & M64
        elif op == 62:  # std/stdu
            ds = sext16(imm & 0xFFFC)
            x = imm & 3
            a = ((r[rA] if rA else 0) + ds) & M32
            m.w64(a, r[rD])
            if x == 1:
                r[rA] = a
        elif op == 24:
            r[rA] = r[rD] | imm
        elif op == 25:
            r[rA] = r[rD] | (imm << 16)
        elif op == 26:
            r[rA] = r[rD] ^ imm
        elif op == 27:
            r[rA] = r[rD] ^ (imm << 16)
        elif op == 28:
            r[rA] = r[rD] & imm
            self.rc(rA)
        elif op == 29:
            r[rA] = r[rD] & (imm << 16)
            self.rc(rA)
        elif op == 7:  # mulli
            r[rA if False else rD] = (s64(r[rA]) * sext16(imm)) & M64
        elif op == 8:  # subfic
            v = (sext16(imm) & M64) - r[rA]
            self.ca = 1 if (sext16(imm) & M64) >= r[rA] else 0
            r[rD] = v & M64
        elif op == 12 or op == 13:  # addic / addic.
            v = r[rA] + (sext16(imm) & M64)
            self.ca = 1 if v > M64 else 0
            r[rD] = v & M64
            if op == 13:
                self.rc(rD)
        elif op == 10:  # cmpli
            bf = rD >> 2
            L = rD & 1
            a = r[rA] if L else r[rA] & M32
            self.cmp(bf, a, imm)
        elif op == 11:  # cmpi
            bf = rD >> 2
            L = rD & 1
            a = s64(r[rA]) if L else s32(r[rA])
            self.cmp(bf, a, sext16(imm))
        elif op == 21:  # rlwinm
            sh = rB
            mb = (w >> 6) & 31
            me = (w >> 1) & 31
            r[rA] = rotl32(r[rD], sh) & mask32(mb, me)
            if w & 1:
                self.rc(rA)
        elif op == 20:  # rlwimi
            sh = rB
            mb = (w >> 6) & 31
            me = (w >> 1) & 31
            mk = mask32(mb, me)
            r[rA] = ((rotl32(r[rD], sh) & mk) | (r[rA] & ~mk)) & M32
            if w & 1:
                self.rc(rA)
        elif op == 23:  # rlwnm
            sh = r[rB] & 31
            mb = (w >> 6) & 31
            me = (w >> 1) & 31
            r[rA] = rotl32(r[rD], sh) & mask32(mb, me)
            if w & 1:
                self.rc(rA)
        elif op == 30:
            self.md(w, rD, rA, rB)
        elif op == 18:  # b
            li = w & 0x03FFFFFC
            if li & 0x02000000:
                li -= 0x04000000
            t = (li if (w & 2) else pc + li) & M64
            if w & 1:
                self.lr = pc + 4
                if self.on_call:
                    self.on_call(self, t, False)
            elif self.on_call and (t < pc - 0x4000 or t > pc + 0x4000 or t in self.fstarts):
                self.on_call(self, t, True)
            self.pc = t
        elif op == 16:  # bc
            bo = rD
            bi = rA
            bd = w & 0xFFFC
            if bd & 0x8000:
                bd -= 0x10000
            if self.cond(bo, bi):
                if w & 1:
                    self.lr = pc + 4
                self.pc = (bd if (w & 2) else pc + bd) & M64
            elif w & 1:
                self.lr = pc + 4
        elif op == 19:
            xo = (w >> 1) & 0x3FF
            if xo == 16:  # bclr
                bo, bi = rD, rA
                t = self.lr
                if self.cond(bo, bi):
                    self.pc = t & M64
                    if self.on_ret:
                        self.on_ret(self)
                if w & 1:
                    self.lr = pc + 4
            elif xo == 528:  # bcctr
                bo, bi = rD, rA
                if self.cond(bo | 4, bi):
                    if w & 1:
                        self.lr = pc + 4
                    self.pc = self.ctr & M64
            elif xo in (150, 0):  # isync / mcrf
                if xo == 0:
                    self.cr[rD >> 2] = self.cr[rA >> 2]
            elif xo in (257, 449, 193, 289, 129, 225, 33, 417):  # cr logical

                def g(b):
                    return (self.cr[b >> 2] >> (3 - (b & 3))) & 1

                a, b = g(rA), g(rB)
                v = {
                    257: a & b,
                    449: a | b,
                    193: a ^ b,
                    289: 1 - (a ^ b),
                    129: a & (1 - b),
                    225: 1 - (a & b),
                    33: 1 - (a | b),
                    417: a | (1 - b),
                }[xo]
                f, bit = rD >> 2, 3 - (rD & 3)
                self.cr[f] = (self.cr[f] & ~(1 << bit)) | (v << bit)
            else:
                raise NotImplementedError("op19 xo %d at 0x%x" % (xo, pc))
        elif op == 31:
            self.x31(w, rD, rA, rB, pc)
        elif op == 4:
            self.vmx(w, rD, rA, rB, pc)
        elif op == 63 or op == 59:
            xo = (w >> 1) & 0x3FF
            if op == 63 and xo == 72:  # fmr
                self.f[rD] = self.f[rB]
            elif op == 63 and xo == 12:  # frsp
                fv = self.f[rB]
                self.f[rD] = fv
            else:
                # float arithmetic: not needed for layout; zero result
                self.f[rD] = ("d", 0)
        else:
            raise NotImplementedError("op %d at 0x%x (%08x)" % (op, pc, w))

    def md(self, w, rS, rA, rB):
        r = self.r
        sh = rB | (((w >> 1) & 1) << 5)
        mbe = ((w >> 6) & 31) | (((w >> 5) & 1) << 5)
        xo = (w >> 2) & 7
        if xo == 0:  # rldicl
            r[rA] = rotl64(r[rS], sh) & mask64(mbe, 63)
        elif xo == 1:  # rldicr
            r[rA] = rotl64(r[rS], sh) & mask64(0, mbe)
        elif xo == 2:  # rldic
            r[rA] = rotl64(r[rS], sh) & mask64(mbe, 63 - sh)
        elif xo == 3:  # rldimi
            mk = mask64(mbe, 63 - sh)
            r[rA] = (rotl64(r[rS], sh) & mk) | (r[rA] & ~mk & M64)
        elif xo == 4:
            xo2 = (w >> 1) & 15
            n = r[rB] & 63
            if xo2 == 8:  # rldcl
                r[rA] = rotl64(r[rS], n) & mask64(mbe, 63)
            else:
                r[rA] = rotl64(r[rS], n) & mask64(0, mbe)
        else:
            raise NotImplementedError("md")
        if w & 1:
            self.rc(rA)

    def x31(self, w, rD, rA, rB, pc):
        r = self.r
        m = self.m
        xo = (w >> 1) & 0x3FF
        rc = w & 1
        ea = lambda: ((r[rA] if rA else 0) + r[rB]) & M32
        xo9 = xo & 0x1FF
        if xo == 23:
            r[rD] = m.r32(ea())
        elif xo == 55:
            a = ea()
            r[rD] = m.r32(a)
            r[rA] = a
        elif xo == 87:
            r[rD] = m.r8(ea())
        elif xo == 279:
            r[rD] = m.r16(ea())
        elif xo == 343:
            r[rD] = sext16(m.r16(ea())) & M64
        elif xo == 21:
            r[rD] = m.r64(ea())
        elif xo == 341:  # lwax
            r[rD] = s32(m.r32(ea())) & M64
        elif xo == 151:
            m.w32(ea(), r[rD])
        elif xo == 183:
            a = ea()
            m.w32(a, r[rD])
            r[rA] = a
        elif xo == 215:
            m.w8(ea(), r[rD])
        elif xo == 407:
            m.w16(ea(), r[rD])
        elif xo == 149:
            m.w64(ea(), r[rD])
        elif xo == 181:
            a = ea()
            m.w64(a, r[rD])
            r[rA] = a
        elif xo == 535:  # lfsx
            self.f[rD] = ("s", m.r32(ea()))
        elif xo == 599:
            self.f[rD] = ("d", m.r64(ea()))
        elif xo == 663:  # stfsx
            fv = self.f[rD]
            m.w32(ea(), fv[1] if isinstance(fv, tuple) and fv[0] == "s" else 0)
        elif xo == 727:
            fv = self.f[rD]
            m.w64(ea(), fv[1] if isinstance(fv, tuple) and fv[0] == "d" else 0)
        elif xo == 103 or xo == 359:  # lvx / lvxl
            a = ea() & ~15
            self.v[rD] = int.from_bytes(m.read(a, 16), "big")
        elif xo == 231 or xo == 487:  # stvx
            a = ea() & ~15
            m.write(a, self.v[rD].to_bytes(16, "big"))
        elif xo == 0:  # cmp
            bf = rD >> 2
            if rD & 1:
                self.cmp(bf, s64(r[rA]), s64(r[rB]))
            else:
                self.cmp(bf, s32(r[rA]), s32(r[rB]))
        elif xo == 32:  # cmpl
            bf = rD >> 2
            if rD & 1:
                self.cmp(bf, r[rA], r[rB])
            else:
                self.cmp(bf, r[rA] & M32, r[rB] & M32)
        elif xo9 == 266:  # add
            r[rD] = (r[rA] + r[rB]) & M64
            if rc:
                self.rc(rD)
        elif xo9 == 40:  # subf
            r[rD] = (r[rB] - r[rA]) & M64
            if rc:
                self.rc(rD)
        elif xo9 == 10:  # addc
            v = r[rA] + r[rB]
            self.ca = int(v > M64)
            r[rD] = v & M64
        elif xo9 == 8:  # subfc
            v = r[rB] + ((~r[rA]) & M64) + 1
            self.ca = int(v > M64)
            r[rD] = v & M64
        elif xo9 == 138:  # adde
            v = r[rA] + r[rB] + self.ca
            self.ca = int(v > M64)
            r[rD] = v & M64
        elif xo9 == 136:  # subfe
            v = ((~r[rA]) & M64) + r[rB] + self.ca
            self.ca = int(v > M64)
            r[rD] = v & M64
        elif xo9 == 202:  # addze
            v = r[rA] + self.ca
            self.ca = int(v > M64)
            r[rD] = v & M64
            if rc:
                self.rc(rD)
        elif xo9 == 234:  # addme
            v = r[rA] + self.ca + M64
            self.ca = int(v > M64)
            r[rD] = v & M64
        elif xo9 == 200:  # subfze
            v = ((~r[rA]) & M64) + self.ca
            self.ca = int(v > M64)
            r[rD] = v & M64
        elif xo9 == 104:  # neg
            r[rD] = (-r[rA]) & M64
            if rc:
                self.rc(rD)
        elif xo9 == 235:  # mullw
            r[rD] = (s32(r[rA]) * s32(r[rB])) & M64
            if rc:
                self.rc(rD)
        elif xo9 == 233:  # mulld
            r[rD] = (s64(r[rA]) * s64(r[rB])) & M64
        elif xo9 == 75:  # mulhw
            r[rD] = ((s32(r[rA]) * s32(r[rB])) >> 32) & M64
        elif xo9 == 11:  # mulhwu
            r[rD] = (((r[rA] & M32) * (r[rB] & M32)) >> 32) & M32
        elif xo9 == 73:  # mulhd
            r[rD] = ((s64(r[rA]) * s64(r[rB])) >> 64) & M64
        elif xo9 == 9:  # mulhdu
            r[rD] = ((r[rA] * r[rB]) >> 64) & M64
        elif xo9 == 491:  # divw
            a, b = s32(r[rA]), s32(r[rB])
            q = 0 if b == 0 else int(a / b)
            r[rD] = q & M64
        elif xo9 == 459:  # divwu
            a, b = r[rA] & M32, r[rB] & M32
            r[rD] = 0 if b == 0 else a // b
        elif xo9 == 489:  # divd
            a, b = s64(r[rA]), s64(r[rB])
            r[rD] = (0 if b == 0 else int(a / b)) & M64
        elif xo9 == 457:
            r[rD] = 0 if r[rB] == 0 else r[rA] // r[rB]
        elif xo == 28:  # and
            r[rA] = r[rD] & r[rB]
            if rc:
                self.rc(rA)
        elif xo == 60:  # andc
            r[rA] = r[rD] & ~r[rB] & M64
            if rc:
                self.rc(rA)
        elif xo == 444:  # or
            r[rA] = r[rD] | r[rB]
            if rc:
                self.rc(rA)
        elif xo == 412:  # orc
            r[rA] = (r[rD] | ~r[rB]) & M64
        elif xo == 316:  # xor
            r[rA] = r[rD] ^ r[rB]
            if rc:
                self.rc(rA)
        elif xo == 124:  # nor
            r[rA] = ~(r[rD] | r[rB]) & M64
            if rc:
                self.rc(rA)
        elif xo == 476:  # nand
            r[rA] = ~(r[rD] & r[rB]) & M64
        elif xo == 284:  # eqv
            r[rA] = ~(r[rD] ^ r[rB]) & M64
        elif xo == 24:  # slw
            n = r[rB] & 63
            r[rA] = 0 if n > 31 else (r[rD] << n) & M32
            if rc:
                self.rc(rA)
        elif xo == 536:  # srw
            n = r[rB] & 63
            r[rA] = 0 if n > 31 else (r[rD] & M32) >> n
            if rc:
                self.rc(rA)
        elif xo == 27:  # sld
            n = r[rB] & 127
            r[rA] = 0 if n > 63 else (r[rD] << n) & M64
        elif xo == 539:  # srd
            n = r[rB] & 127
            r[rA] = 0 if n > 63 else r[rD] >> n
        elif xo == 792:  # sraw
            n = r[rB] & 63
            v = s32(r[rD])
            res = v >> min(n, 31)
            self.ca = int(v < 0 and (res << min(n, 31)) != v)
            r[rA] = res & M64
            if rc:
                self.rc(rA)
        elif xo == 824:  # srawi
            n = rB
            v = s32(r[rD])
            res = v >> n
            self.ca = int(v < 0 and (res << n) != v)
            r[rA] = res & M64
            if rc:
                self.rc(rA)
        elif xo == 794:  # srad
            n = r[rB] & 127
            v = s64(r[rD])
            res = v >> min(n, 63)
            self.ca = int(v < 0 and (res << min(n, 63)) != v)
            r[rA] = res & M64
        elif xo in (826, 827):  # sradi
            n = rB | ((w >> 1) & 1) << 5
            v = s64(r[rD])
            res = v >> n
            self.ca = int(v < 0 and (res << n) != v)
            r[rA] = res & M64
        elif xo == 986:  # extsw
            r[rA] = s32(r[rD]) & M64
            if rc:
                self.rc(rA)
        elif xo == 922:  # extsh
            r[rA] = sext16(r[rD] & 0xFFFF) & M64
            if rc:
                self.rc(rA)
        elif xo == 954:  # extsb
            v = r[rD] & 0xFF
            r[rA] = (v - 256 if v & 0x80 else v) & M64
            if rc:
                self.rc(rA)
        elif xo == 26:  # cntlzw
            v = r[rD] & M32
            r[rA] = 32 - v.bit_length()
        elif xo == 58:  # cntlzd
            r[rA] = 64 - (r[rD] & M64).bit_length()
        elif xo == 339:  # mfspr
            spr = ((w >> 16) & 31) | (((w >> 11) & 31) << 5)
            if spr == 8:
                r[rD] = self.lr
            elif spr == 9:
                r[rD] = self.ctr
            elif spr == 1:
                r[rD] = self.ca << 29
            else:
                r[rD] = 0
        elif xo == 467:  # mtspr
            spr = ((w >> 16) & 31) | (((w >> 11) & 31) << 5)
            if spr == 8:
                self.lr = r[rD]
            elif spr == 9:
                self.ctr = r[rD]
            elif spr == 1:
                self.ca = (r[rD] >> 29) & 1
        elif xo == 19:  # mfcr / mfocrf
            v = 0
            for i in range(8):
                v = (v << 4) | self.cr[i]
            r[rD] = v
        elif xo == 144:  # mtcrf
            fxm = (w >> 12) & 0xFF
            for i in range(8):
                if fxm & (0x80 >> i):
                    self.cr[i] = (r[rD] >> (28 - 4 * i)) & 15
        elif xo in (
            598,
            854,
            86,
            278,
            246,
            54,
            982,
            1014,
            470,
            4,
        ):  # sync,eieio,dcbf,dcbt,dcbtst,dcbst,icbi,dcbz,dcbi,tw
            if xo == 1014:  # dcbz
                a = ea() & ~127
                m.write(a, bytes(128))
        elif xo == 20:  # lwarx
            r[rD] = m.r32(ea())
        elif xo == 150:  # stwcx.
            m.w32(ea(), r[rD])
            self.cr[0] = 2
        else:
            raise NotImplementedError("x31 xo %d at 0x%x (%08x)" % (xo, pc, w))

    def vmx(self, w, vD, vA, vB, pc):
        xo = w & 0x7FF
        if xo == 1220:  # vxor
            self.v[vD] = self.v[vA] ^ self.v[vB]
        elif xo == 1156:  # vor
            self.v[vD] = self.v[vA] | self.v[vB]
        elif xo == 1028:  # vand
            self.v[vD] = self.v[vA] & self.v[vB]
        else:
            # unsupported vector op: zero
            self.v[vD] = 0
