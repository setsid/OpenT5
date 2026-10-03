# R6: the online .wad container

The `.wad` is the bundle of online text the MP client downloads from online
storage after sign-in. The PS3 TU13 build fetches one file,
`online_tu13_mp_<language>.wad`. It is a small container of zlib-compressed
text files. It is not a fastfile, holds no zones or rawfile assets, and has
no signature and no encryption.

Implementation: `src/opent5/container/wad.py`. Tests: `tests/test_wad.py`.

## Evidence base

| Sample | Bytes | Provenance |
|---|---|---|
| `online_tu13_mp_english.wad` | 42556 | retail, as served |
| `retail-wad.bin` | 42556 | byte-identical to the above (`cmp` reports no difference), so not a second independent sample |
| `online_tu13_mp_english.base.wad` | 41025 | a third-party rebuild: different members, same header |
| `online_tu13_mp_english.new.wad` | 42308 | a third-party rebuild: different motd, same header |
| `retail-wad-frame.bin` | 42609 | not a wad (see below) |

There is only one independent retail wad. The two rebuilds come from another
packer, so they confirm the table and stream layout (their sizes and offsets
differ from retail and still parse), but their header words were copied from
retail and say nothing new about those fields. To make up for that, every
field below is also checked against the parser in the decrypted MP
executable, `t5mp.elf`, at 0x6bccf0 to 0x6bd138. Addresses are virtual
addresses. Strings sit at file offset = VMA - 0x10000.

## Layout

Everything is big-endian.

| Offset | Type | Field | Retail value | Evidence |
|---|---|---|---|---|
| 0x00 | u32 | magic | `54 33 77 ab` | All four samples. ELF 0x6bcdf4/0x6bce00: `lis r26,0x5433; ori r11,r26,0x77ab`, then `cmpw` against the first word of the buffer |
| 0x04 | u32 | timestamp | `51 9a d6 8b` = 1369101963 = 2013-05-21 02:06:03 UTC | All four samples (copied in the rebuilds). The parser does not read it, except to byte-swap it (0x6bcf0c) |
| 0x08 | u32 | entry count | `00 00 00 03` | All four samples. ELF 0x6bcf88 `lwz r8,8(r25)` is the loop bound, re-read at 0x6bcff0 |
| 0x0c | u32 | version | `00 00 00 0d` (13) | All four samples. ELF 0x6bcf80 `lwz r11,12(r25)`, stored at 0x215190c |
| 0x10 | entry[count] | table | | see below |

An entry is 44 bytes. The loop advances by `addi r29,r29,44` (0x6bcff4).

| Entry offset | Type | Field | Evidence |
|---|---|---|---|
| +0x00 | char[32] | name, NUL-padded | Retail 0x10: `6d6f74642d6d702e74787400` "motd-mp.txt" followed by zero bytes up to 0x30. Every byte after the NUL is zero in all 9 names across the 3 parseable samples. ELF: name pointer = entry+0 (`addi r31,r29,-40`), passed to the string compare at 0x4dd8c0 |
| +0x20 | u32 | compressed size | Retail 0x30: `00000235` = 565. ELF: `addi r9,r29,-8`, loaded at 0x6bd034, stored as zlib `avail_in` |
| +0x24 | u32 | size (inflated) | Retail 0x34: `0000063a` = 1594, which equals the inflated length of all 9 members in the 3 samples. **The parser never reads this field** |
| +0x28 | u32 | offset from file start | Retail 0x38: `00000094`. ELF 0x6bd02c: `lwz r7,0(r29)`, added to the buffer base, stored as zlib `next_in` |

Retail table:

| # | Name | Compressed | Size | Offset | Raw bytes (size, size, offset) |
|---|---|---|---|---|---|
| 0 | motd-mp.txt | 565 | 1594 | 0x94 | `00000235 0000063a 00000094` at 0x30 |
| 1 | playlists_mp.info | 24741 | 124636 | 0x2c9 | `000060a5 0001e6dc 000002c9` at 0x5c |
| 2 | contracts.info | 17102 | 162716 | 0x636e | `000042ce 00027b9c 0000636e` at 0x88 |

The base rebuild has the same order with `00000125 000004b3 00000094`,
`00006098 0001e6d5 000001b9` and `00003df0 0002698a 00006251`.

### Streams, alignment and padding

- The first stream starts right after the table: 0x10 + 3 * 44 = 0x94 in all three samples.
- The streams follow one another in table order with no padding. For each
  entry, offset + compressed size = the next entry's offset. Offsets such as
  0x2c9 show there is no alignment.
- The last stream ends exactly at the end of the file (0x636e + 0x42ce =
  42556), and the same holds for both rebuilds. There is no trailer.

### Members are zlib streams, not zones

- Every stream starts `78 da`: a zlib header with the maximum-compression
  flag.
- Every stream ends with the Adler-32 of its inflated data. For example,
  retail motd ends `37684de3` and `adler32` of its text is 0x37684de3.
- Each stream inflates fully, with no bytes left over.
- The ELF calls `inflateInit2_(strm, 15, "1.2.3", 48)` at 0x6bd054. r4 = 15
  means a zlib-wrapped stream with a 32 KiB window, and the string "1.2.3" is
  at 0x940220. It then calls `inflate` (0x508d30) and `inflateEnd`
  (0x508a20).
- `zlib.compress(data, 9)` from Python reproduces all three retail streams
  byte for byte. Levels 7 and 8 also reproduce the motd.
- The members are plain CRLF text: the motd, the playlist script and the
  contract definitions. None of them starts with `IWff`. The retail motd ends
  with the bytes `82 c5 81 49 22 0d 0a`, so the members are not guaranteed to
  be ASCII. The module treats them as bytes.

### Checksums and signature

The container has no checksum and no signature. The only integrity check is
the Adler-32 inside each zlib stream. The parser does not even look at the
return value of `inflate`. Both rebuilt wads have different content under
the same header, which fits with nothing being signed. Whether those rebuilds
were accepted by a retail console is not evidence held in this repository:
**INFERRED** that an edited wad loads, and a console test would confirm it.
Online storage may check size or hash at the transport level. That is outside
this container.

## How the client uses it (t5mp.elf)

- **Name.** Built at 0x6bc878 with `sprintf(buf, "%s%s.wad", "online_tu13_mp_", language)`.
  The strings are at 0x940200 and 0x940210.
- **Download buffer.** The buffer is at 0x2141908. Its length, 0x10000, is
  set by `lis r5,1` at 0x6bc8e8 and stored at +252 of the request at
  0x6bc8fc. **A wad larger than 65536 bytes cannot be received.** Retail is
  42556.
- **Magic.** The parser accepts `0x543377AB`. It also accepts the
  byte-swapped `0xAB773354` (`xoris r31,r9,0xab77; cmpwi 0x3354` at
  0x6bce18). In that case it swaps the three header words in place at
  0x6bcef4 to 0x6bcf78. Entry fields are never swapped, so a little-endian
  wad would fail at its first entry. The module rejects one with a clear
  error. Any other magic skips the parse.
- **Inflating.** Each member is inflated into a 0x40000-byte buffer
  (`lis r20,4` at 0x6bcfd0, used as `avail_out`). The buffer is zeroed with
  a memset of the same length at 0x6bd010 before each entry. The consumers
  receive only the buffer pointer, so they read the member as a
  NUL-terminated string. Effective limit: 0x3ffff bytes per member. The
  stored size field is unused here.
- **Dispatch by name.** The parser compares names case-insensitively. The
  routine at 0x4dd8c0 folds `A`-`Z` by adding 32 to both sides before
  comparing (0x4dd900 to 0x4dd91c).
  - `contracts.info` (0x940258) goes to 0x657480.
  - `playlists_mp.info` (0x940268) goes to 0x4433a0.
  - `motd-mp.txt` (0x940280) goes to 0x43c390.
  - `liveblurb-mp-<language>.txt` (format at 0x940290, filled from the same
    language name) goes to 0x6622e8.
  - Any other name is inflated and ignored. Table order does not matter to
    the client.
- **Version.** It is stored at 0x215190c. The function at 0x6bc950 returns
  true only if the wad has been parsed and the u32 at 0xb64e30 is >= the
  version. What 0xb64e30 holds is **INFERRED** to be a content or
  update level gate. Tracing its writer would confirm it.

## retail-wad-frame.bin

This file is 42609 bytes, 53 more than the wad. It begins
`6d a6 00 00 01 d5 d6 74 6a ce 18 82`. It does not contain the wad's 16-byte
header anywhere, and zlib level 9 makes it larger (42630 bytes), so it is
high-entropy throughout. **INFERRED:** it is the wad as carried in an
encrypted transport frame from the online service. It is not a container
format, and the module does not read it.

## The module

`opent5.container.wad`:

- `read_entries(bytes)` parses the header and table without inflating.
- `read_wad(bytes)` returns a `Wad(timestamp, version, members)`. Each
  `Member` keeps its inflated `data` and its original `stored` stream.
- `write_wad(wad, level=9)` rebuilds the file:
  - It recomputes every size and offset.
  - It reuses the original stream for any member whose data is unchanged.
    The repack is therefore byte-identical even if another compressor made
    the source.
  - It compresses changed or new members at level 9.
  - It refuses names of 32 bytes or more, duplicate names, members over
    0x3ffff bytes and files over 0x10000 bytes. `enforce_limits=False` lifts
    the two size limits.
- `Wad.with_member` and `Wad.without_member` edit, add and remove members.
- `unpack(wad, dir)` and `pack(dir, wad)` work through a directory. They
  write `wad.json`, which records the order, timestamp, version and original
  streams. Unpacking refuses member names that are paths.
- `python -m opent5.container.wad list|unpack|pack`.

Verified results:

- Retail repacks byte-identically, both from the original streams and from
  fresh level-9 compression.
- Both rebuilt samples repack byte-identically.
- A modified retail wad round-trips.

There are 24 tests in total. The synthetic ones need no game files. The
real-file tests skip when `OPENT5_WADS` (default `~/bo1-ram`) does not hold
the samples.

## Open items

- **Timestamp.** It is INFERRED to be a build time: the value decodes to a
  plausible TU13-era date, and the parser ignores it. A second retail wad
  from a different build would confirm it.
- **Version.** What the gate at 0xb64e30 compares the version against is
  INFERRED.
- **Size field.** It is unused by this parser (confirmed). Whether any other
  code reads it is INFERRED no. A search for other users of the
  0x2141908 buffer found only the fetch at 0x6bc8f8 and the parser.
- **Other titles.** Whether other titles, or the PC build, share this layout
  has not been checked.
