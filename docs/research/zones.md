# Zones: inventory, container rules, round-trip

Track P1. Every zone on this machine, opened, counted and rebuilt, and the
container rules that the rebuild depends on. Every rule below is measured
on the files; where the ELF was read the VMA is given; anything else is
marked INFERRED.

Regenerate the table with

    .venv/bin/python tools/inventory.py --also <update>/USRDIR/french

which reads OPENT5_ZONES, OPENT5_PATCH_ZONES and OPENT5_DLC_ZONES (the last
recursively) from `.env`, and writes the table between the markers below.
The same code is `opent5.container.inventory.run`.

## Result

Every zone on this machine opens, carries a console signature, and
rebuilds byte for byte both ways: carrying its original chunks, and
deflating every chunk afresh. That is 178 zones: the 50 disc zones, the 16
installed update zones, and the 112 console HDD zones (dlc1 to dlc5 and the
HDD's own update folders, english and french). The counts are in the
generated block at the end. Two
zones failed before this work, and each had a container cause, found from
bytes and fixed:

- patch.ff did not rebuild byte-identically: the deflate memLevel was 8,
  the original encoder used 9 (section 1).
- creek_1.ff did not open: the read ring is 0x60000 bytes, not 0x80000, and
  creek_1.ff has the only size field in the set that falls at its end
  (section 2).

Runtime of the full inventory with the recompression check, 4 processes:
248 s wall and 15 min CPU for the first run over 95 zones (82 base and
update plus 13 DLC; 2.7 GB of .ff, 4.8 GB inflated). The generated block
gives the time of the latest run. The per-zone cost is dominated by the level 9
recompression (about 30 s for a 90 MB single-player zone in one process);
`--no-recompress` skips it.

## How the columns are measured

- **Ver**: the u32 big-endian at file 0x08. 473 (0x1d9) everywhere.
- **Signed**: the 256 bytes at 0x3c decode as an RSA-PSS (SHA-256, 8-byte
  salt) encoding under the console key recovered from t5mp.elf
  (`fastfile.console_signature_of`). A decode cannot be produced by chance,
  so it says the field came from the key holder; it does not prove the
  signature matches the file, which needs the signed message.
- **Opens**: every chunk decrypts and inflates, and the stream ends with
  one zero size field per stream (four).
- **Chunks**, **Inflated**: chunk count and the length of the decompressed
  zone (the XFile stream).
- **Assets**, **Script strings**: from the XAssetList the loader reads raw
  straight after the 36-byte prefix (docs/research/xfile.md, section 2):
  content 0x24 scriptStringCount, 0x28 scriptStrings pointer, 0x2c
  assetCount, 0x30 assets pointer. Example, mp_nuked.ff content 0x24:
  `00000204 ffffffff 00000211 ffffffff` = 516 strings, 529 assets.
- **Round-trip (verbatim)**: `Zone.open(file).build()` equals the file
  byte for byte. Untouched chunks carry their original deflate streams, so
  this checks the framing, nonce chain, ring gaps, terminators and padding.
- **Round-trip (recompressed)**: the same with every chunk deflated afresh
  by Python's zlib (1.2.11) at level 9, memLevel 9. This is the stronger
  claim: the original encoder's output is reproducible, so an edited chunk
  is compressed the way the original tools would have.

## Container rules established here

### 1. Deflate settings: level 9, memLevel 9 (cause of the patch.ff failure)

The old repack deflated at Python's default memLevel 8. patch.ff then
differed first at file 0x66a6, which is the first byte of chunk 3's payload
(size field at 0x66a2, stored size 4705, inflated 49088). Chunk 3 decrypted
ends `...57f1d19a5090ff03`. Brute force over level 0-9, memLevel 1-9,
strategy 0-4 and window 9-15 on chunks 1, 3 and 4 of patch.ff:

| chunk | stored | settings that reproduce it exactly |
|--:|--:|---|
| 1 | 9721 | level 9 with memLevel 7, 8 or 9 |
| 3 | 4705 | level 9, memLevel 9 only |
| 4 | 5063 | level 9 with memLevel 7 or 9 |

memLevel sets deflate's literal buffer and so where it closes a block;
the chunks that broke were those where 8 and 9 split blocks differently.
Level 9, memLevel 9, default strategy, 32K window reproduces every chunk of
every zone in the table (the recompressed column), so the original tools
used zlib's maximum memLevel. Nothing indicates sync or full flushes, stored
blocks or a different zlib: each chunk is one complete raw deflate stream
(it ends with the final-block bit set, nothing left over after it).

`level_that_rebuilds` used to try ten levels per chunk on every read; the
read path no longer compresses at all, and the function tries level 9
first.

### 2. The read ring is 0x60000 bytes from file offset 0 (cause of the creek_1.ff failure)

creek_1.ff did not open: chunk 415's size field was read at 0xb9ffff as
`60000091` (1,610,612,881 bytes). The bytes there:

    0xb9fffb  dc 52 6a b7 | 60 | 00 00 91 60 c8 97 ...
                            ^0xb9ffff  ^0xba0000

Chunk 414 ends at 0xb9ffff. Skipping the single byte 0x60 puts the field
at 0xba0000, giving 0x9160 = 37216 bytes, and every remaining chunk of the
zone then inflates. 0xba0000 = 31 x 0x60000.

The old rule (a 0x80000 buffer counted from file offset 0) never skips
there, because 0xba0000 is not a multiple of 0x80000. Every size field in
all zones that lies within three bytes of a 0x1000 boundary was tested both
ways (skip or not, judged by whether the next six fields stay in range and
end on four zero terminators): 51 such fields, of which only creek_1's
needs the skip; for example fullahead.ff 0x4bfffe, khe_sanh.ff 0x168fffe and
pow.ff 0x554fffe straddle 0x10000 boundaries and must not be skipped.
Searching buffer sizes 0x10000-0x200000 and origins, a 0x60000 ring from
offset 0 fits all 51 (as do some others, so the bytes alone do not single
it out).

The ELF does: in t5mp.elf, VMA 0x231ea8-0x231ec4 reduces the file read
position modulo 0x60000 (`mulhwu` by 0xaaaaaaab, shift right 18 gives
pos / 0x60000; then pos - (q<<19 - q<<17)) and adds the ring's base, and
VMA 0x231f7c-0x231fa8 caps each read at 0x30000 (`lis r5,3`, compared with
0x2ffff). The skip itself in the chunk reader was not located: INFERRED
that the reader skips to the ring boundary when fewer than four bytes are
left, as the writer evidently did. Confirm by finding the size-field read
that consumes this ring.

The gap byte is not zero: creek_1.ff carries 0x60 at 0xb9ffff, which is
writer garbage. A recompressed repack of creek_1.ff matches the original in
every byte except that one when the gap is zero-filled, so the reader keeps
gap bytes (`Chunk.skipped`) and the writer puts them back where the layout
before them is unchanged; a new gap is zero-filled.

### 3. Terminators and padding

Every zone ends its chunk stream with four zero size fields (one per
stream), then zeros. The file length is the end of the fourth terminator
plus 0x30, rounded up to 0x20 (`fastfile.padded_length`): padding runs
from 48 bytes (common_mp.ff: end 0x34a21f0, length 0x34a2220) to 79
(mp_nuked.ff: end 0x22aa6f1, length 0x22aa740), and every padding byte is
zero, in all zones of the table (the inventory checks it per zone). The old
writer padded to 0x40 and needed the original length passed in; this rule
makes the length derivable.

### 4. Chunking

Chunk 0 is the 36-byte prefix alone in every zone (`prefix alone` in the
summary). Every chunk between it and the last inflates to exactly 0xBFC0
(49,088) bytes, in every zone: the inventory counts inner chunks of any
other size (`zones with odd chunk bodies`) and finds none. The last chunk
holds the remainder. So the original writer flushes a chunk every
0xC000 - 0x40 bytes of content (the code previously assumed 0x8000 - 0x40,
taken from T6). `fastfile.split_content` and the editor's re-split use 0xBFC0.

The largest stored (compressed) chunk is 49,098 bytes (code_post_gfx.ff,
frontend.ff and river.ff); the reader's limit stays 0x10000. Whether the
console has a hard limit on inflated chunk size is not established;
INFERRED that 0xBFC0 is safe because every retail chunk uses it, and an
edited zone never produces a larger chunk.

## What saving does

`opent5.container.zone.Zone.open(path)` gives `.header` (bytearray, 0x13c)
and `.content` (bytearray, the inflated zone). `.save(path)` /
`.build()`:

- untouched chunks keep their original deflate stream; only changed ones
  are deflated (level 9, memLevel 9);
- chunks matching from the front are kept, and so are chunks matching from
  the back once the edit is past; a same-length middle is re-deflated on
  the original boundaries, a resized middle is re-split into 0xBFC0 pieces;
- the prefix stays a chunk of its own and its length field (content 0x00)
  is recomputed as len(content) - 36;
- every nonce is recomputed (they chain through SHA-1 of each chunk), the
  0x60000 ring rule is applied, four terminators and the padding rule are
  written, and the signature at 0x3c is copied unchanged. An edited zone
  therefore loads only on a client with the signature check patched out.

`verify(path, expected=...)` reopens a file and checks all chunks inflate,
four terminators, zero padding, the 36-byte first chunk, the declared
length, and optionally the content.

## DLC

The DLC map zones come from the console HDD, in OPENT5_DLC_ZONES
(`.../hdd/dlc1..dlc5/{english,french}`, plus the HDD's own update folders),
searched recursively. All 112 open and round-trip; none failed. The
`dlcN.edat.off` files beside them are licence files, not zones, and are not
read.

The `_load.ff` zones (704 and 1,056 bytes) are real zones too: two chunks,
the prefix and one small body.

<!-- INVENTORY START -->

Generated by tools/inventory.py over 178 zones in 268 s (6 processes).

- zones: 178
- signed: 178
- open: 178
- verbatim identical: 178
- recompressed identical: 178
- declared size ok: 178
- padding rule ok: 178
- four terminators: 178
- prefix alone: 178
- ring gaps: 1
- zones with odd chunk bodies: 0
- max stored: 49098
- errors: 0
- seconds: 268


| Zone | Folder | Size | Ver | Signed | Opens | Chunks | Inflated | Assets | Script strings | Round-trip (verbatim) | Round-trip (recompressed) |
|---|---|--:|--:|---|---|--:|--:|--:|--:|---|---|
| code_post_gfx.ff | disc | 2,650,496 | 473 | yes | yes | 183 | 8,923,153 | 6,899 | 154 | yes | yes |
| code_post_gfx_mp.ff | disc | 3,097,920 | 473 | yes | yes | 209 | 10,203,395 | 8,576 | 116 | yes | yes |
| common.ff | disc | 19,219,936 | 473 | yes | yes | 638 | 31,263,986 | 1,689 | 274 | yes | yes |
| common_mp.ff | disc | 55,190,048 | 473 | yes | yes | 1,950 | 95,670,787 | 7,286 | 1,145 | yes | yes |
| common_zombie.ff | disc | 55,562,048 | 473 | yes | yes | 1,725 | 84,619,615 | 3,357 | 858 | yes | yes |
| creek_1.ff | disc | 91,022,304 | 473 | yes | yes | 3,022 | 148,259,845 | 2,998 | 2,020 | yes | yes |
| cuba.ff | disc | 90,804,704 | 473 | yes | yes | 3,351 | 164,436,029 | 2,435 | 2,314 | yes | yes |
| dev.ff | disc | 65,600 | 473 | yes | yes | 10 | 432,481 | 70 | 4 | yes | yes |
| dev_mp.ff | disc | 22,400 | 473 | yes | yes | 4 | 115,307 | 52 | 0 | yes | yes |
| flashpoint.ff | disc | 89,232,192 | 473 | yes | yes | 3,151 | 154,606,636 | 2,539 | 1,959 | yes | yes |
| frontend.ff | disc | 46,909,280 | 473 | yes | yes | 1,293 | 63,420,557 | 1,320 | 323 | yes | yes |
| fullahead.ff | disc | 88,168,480 | 473 | yes | yes | 2,999 | 147,140,736 | 2,145 | 1,578 | yes | yes |
| hue_city.ff | disc | 85,194,656 | 473 | yes | yes | 3,130 | 153,563,925 | 2,321 | 2,338 | yes | yes |
| int_escape.ff | disc | 96,857,600 | 473 | yes | yes | 2,854 | 140,020,517 | 1,104 | 913 | yes | yes |
| khe_sanh.ff | disc | 91,892,480 | 473 | yes | yes | 3,174 | 155,714,887 | 2,397 | 1,498 | yes | yes |
| kowloon.ff | disc | 81,108,416 | 473 | yes | yes | 3,200 | 156,996,283 | 2,433 | 1,742 | yes | yes |
| mp_array.ff | disc | 37,907,712 | 473 | yes | yes | 1,444 | 70,788,664 | 766 | 806 | yes | yes |
| mp_cairo.ff | disc | 35,703,296 | 473 | yes | yes | 1,435 | 70,354,671 | 655 | 897 | yes | yes |
| mp_cosmodrome.ff | disc | 36,268,288 | 473 | yes | yes | 1,456 | 71,379,022 | 765 | 775 | yes | yes |
| mp_cracked.ff | disc | 37,654,464 | 473 | yes | yes | 1,488 | 72,984,741 | 758 | 739 | yes | yes |
| mp_crisis.ff | disc | 37,151,008 | 473 | yes | yes | 1,438 | 70,532,391 | 642 | 621 | yes | yes |
| mp_duga.ff | disc | 35,457,440 | 473 | yes | yes | 1,403 | 68,809,151 | 728 | 676 | yes | yes |
| mp_firingrange.ff | disc | 35,531,040 | 473 | yes | yes | 1,323 | 64,877,384 | 532 | 609 | yes | yes |
| mp_hanoi.ff | disc | 36,606,080 | 473 | yes | yes | 1,453 | 71,240,645 | 682 | 669 | yes | yes |
| mp_havoc.ff | disc | 37,258,112 | 473 | yes | yes | 1,302 | 63,856,189 | 516 | 504 | yes | yes |
| mp_mountain.ff | disc | 35,418,720 | 473 | yes | yes | 1,375 | 67,431,037 | 630 | 775 | yes | yes |
| mp_nuked.ff | disc | 36,349,760 | 473 | yes | yes | 1,404 | 68,848,457 | 529 | 516 | yes | yes |
| mp_radiation.ff | disc | 35,083,424 | 473 | yes | yes | 1,421 | 69,662,122 | 748 | 734 | yes | yes |
| mp_russianbase.ff | disc | 37,309,184 | 473 | yes | yes | 1,423 | 69,780,069 | 662 | 715 | yes | yes |
| mp_villa.ff | disc | 35,433,600 | 473 | yes | yes | 1,407 | 68,969,993 | 498 | 534 | yes | yes |
| outro.ff | disc | 5,610,464 | 473 | yes | yes | 213 | 10,367,865 | 189 | 1 | yes | yes |
| pentagon.ff | disc | 97,952,384 | 473 | yes | yes | 3,102 | 152,174,983 | 1,027 | 769 | yes | yes |
| pow.ff | disc | 92,998,720 | 473 | yes | yes | 3,126 | 153,372,498 | 2,599 | 1,589 | yes | yes |
| rebirth.ff | disc | 87,282,656 | 473 | yes | yes | 3,242 | 159,063,451 | 2,355 | 1,864 | yes | yes |
| river.ff | disc | 95,671,424 | 473 | yes | yes | 3,211 | 157,552,077 | 2,559 | 1,801 | yes | yes |
| so_narrative1_frontend.ff | disc | 1,872,352 | 473 | yes | yes | 49 | 2,315,971 | 33 | 161 | yes | yes |
| so_narrative2_frontend.ff | disc | 2,677,184 | 473 | yes | yes | 66 | 3,144,177 | 32 | 161 | yes | yes |
| so_narrative3_frontend.ff | disc | 4,020,736 | 473 | yes | yes | 94 | 4,542,837 | 35 | 161 | yes | yes |
| so_narrative4_frontend.ff | disc | 1,845,952 | 473 | yes | yes | 48 | 2,295,243 | 33 | 161 | yes | yes |
| so_narrative5_frontend.ff | disc | 1,086,368 | 473 | yes | yes | 32 | 1,516,749 | 32 | 161 | yes | yes |
| terminal.ff | disc | 3,086,752 | 473 | yes | yes | 89 | 4,316,737 | 294 | 0 | yes | yes |
| ui_mp.ff | disc | 10,949,504 | 473 | yes | yes | 764 | 37,425,473 | 670 | 0 | yes | yes |
| ui_viewer_mp.ff | disc | 24,117,920 | 473 | yes | yes | 821 | 40,249,871 | 194 | 160 | yes | yes |
| underwaterbase.ff | disc | 85,890,304 | 473 | yes | yes | 3,102 | 152,216,037 | 2,379 | 1,996 | yes | yes |
| vorkuta.ff | disc | 88,014,400 | 473 | yes | yes | 3,269 | 160,398,181 | 2,452 | 1,836 | yes | yes |
| wmd.ff | disc | 88,834,464 | 473 | yes | yes | 3,098 | 152,013,660 | 2,877 | 2,132 | yes | yes |
| wmd_sr71.ff | disc | 77,458,432 | 473 | yes | yes | 2,708 | 132,832,502 | 2,022 | 1,466 | yes | yes |
| zombie_pentagon.ff | disc | 46,211,552 | 473 | yes | yes | 1,846 | 90,524,512 | 1,057 | 861 | yes | yes |
| zombie_theater.ff | disc | 51,040,928 | 473 | yes | yes | 2,075 | 101,799,224 | 917 | 790 | yes | yes |
| zombietron.ff | disc | 85,637,664 | 473 | yes | yes | 2,839 | 139,307,622 | 2,790 | 1,241 | yes | yes |
| common_zombie_patch.ff | update | 500,640 | 473 | yes | yes | 17 | 777,344 | 137 | 163 | yes | yes |
| patch.ff | update | 85,504 | 473 | yes | yes | 16 | 695,479 | 426 | 1 | yes | yes |
| patch_mp.ff | update | 1,196,128 | 473 | yes | yes | 77 | 3,715,811 | 1,337 | 212 | yes | yes |
| patch_ui.ff | update | 1,048,864 | 473 | yes | yes | 93 | 4,513,012 | 31 | 0 | yes | yes |
| patch_ui_mp.ff | update | 4,099,840 | 473 | yes | yes | 279 | 13,611,499 | 130 | 0 | yes | yes |
| zombie_coast_patch.ff | update | 432,032 | 473 | yes | yes | 19 | 858,910 | 103 | 35 | yes | yes |
| zombie_cod5_asylum_patch.ff | update | 437,600 | 473 | yes | yes | 19 | 857,642 | 21 | 19 | yes | yes |
| zombie_cod5_factory_patch.ff | update | 433,376 | 473 | yes | yes | 19 | 853,697 | 21 | 19 | yes | yes |
| zombie_cod5_prototype_patch.ff | update | 433,760 | 473 | yes | yes | 19 | 853,968 | 21 | 19 | yes | yes |
| zombie_cod5_sumpf_patch.ff | update | 440,768 | 473 | yes | yes | 19 | 861,430 | 25 | 19 | yes | yes |
| zombie_cosmodrome_patch.ff | update | 466,432 | 473 | yes | yes | 20 | 919,212 | 131 | 35 | yes | yes |
| zombie_moon_patch.ff | update | 46,176 | 473 | yes | yes | 2 | 46,183 | 16 | 0 | yes | yes |
| zombie_pentagon_patch.ff | update | 419,104 | 473 | yes | yes | 19 | 843,849 | 98 | 35 | yes | yes |
| zombie_temple_patch.ff | update | 430,784 | 473 | yes | yes | 19 | 856,764 | 101 | 35 | yes | yes |
| zombie_theater_patch.ff | update | 409,344 | 473 | yes | yes | 19 | 847,582 | 146 | 35 | yes | yes |
| zombietron_patch.ff | update | 87,328 | 473 | yes | yes | 3 | 87,077 | 12 | 0 | yes | yes |
| zombie_cod5_asylum.ff | dlc/dlc1/english | 41,962,400 | 473 | yes | yes | 1,528 | 74,926,081 | 1,028 | 584 | yes | yes |
| zombie_cod5_asylum_load.ff | dlc/dlc1/english | 704 | 473 | yes | yes | 2 | 1,765 | 8 | 0 | yes | yes |
| zombie_cod5_factory.ff | dlc/dlc1/english | 55,082,464 | 473 | yes | yes | 2,020 | 99,092,427 | 1,064 | 594 | yes | yes |
| zombie_cod5_factory_load.ff | dlc/dlc1/english | 704 | 473 | yes | yes | 2 | 1,766 | 8 | 0 | yes | yes |
| zombie_cod5_prototype.ff | dlc/dlc1/english | 34,514,240 | 473 | yes | yes | 1,189 | 58,274,233 | 903 | 570 | yes | yes |
| zombie_cod5_prototype_load.ff | dlc/dlc1/english | 704 | 473 | yes | yes | 2 | 1,768 | 8 | 0 | yes | yes |
| zombie_cod5_sumpf.ff | dlc/dlc1/english | 53,201,568 | 473 | yes | yes | 1,922 | 94,279,903 | 991 | 562 | yes | yes |
| zombie_cod5_sumpf_load.ff | dlc/dlc1/english | 704 | 473 | yes | yes | 2 | 1,764 | 8 | 0 | yes | yes |
| zombie_cod5_asylum.ff | dlc/dlc1/french | 42,025,600 | 473 | yes | yes | 1,529 | 74,994,651 | 1,028 | 584 | yes | yes |
| zombie_cod5_asylum_load.ff | dlc/dlc1/french | 704 | 473 | yes | yes | 2 | 1,765 | 8 | 0 | yes | yes |
| zombie_cod5_factory.ff | dlc/dlc1/french | 55,224,704 | 473 | yes | yes | 2,024 | 99,283,601 | 1,064 | 594 | yes | yes |
| zombie_cod5_factory_load.ff | dlc/dlc1/french | 704 | 473 | yes | yes | 2 | 1,766 | 8 | 0 | yes | yes |
| zombie_cod5_prototype.ff | dlc/dlc1/french | 34,580,512 | 473 | yes | yes | 1,188 | 58,259,287 | 903 | 570 | yes | yes |
| zombie_cod5_prototype_load.ff | dlc/dlc1/french | 704 | 473 | yes | yes | 2 | 1,768 | 8 | 0 | yes | yes |
| zombie_cod5_sumpf.ff | dlc/dlc1/french | 53,276,864 | 473 | yes | yes | 1,922 | 94,284,200 | 991 | 562 | yes | yes |
| zombie_cod5_sumpf_load.ff | dlc/dlc1/french | 704 | 473 | yes | yes | 2 | 1,764 | 8 | 0 | yes | yes |
| mp_berlinwall2.ff | dlc/dlc2/english | 36,178,144 | 473 | yes | yes | 1,422 | 69,729,167 | 658 | 649 | yes | yes |
| mp_berlinwall2_load.ff | dlc/dlc2/english | 1,056 | 473 | yes | yes | 2 | 2,507 | 9 | 0 | yes | yes |
| mp_discovery.ff | dlc/dlc2/english | 36,730,816 | 473 | yes | yes | 1,430 | 70,146,611 | 680 | 934 | yes | yes |
| mp_discovery_load.ff | dlc/dlc2/english | 1,056 | 473 | yes | yes | 2 | 2,505 | 9 | 0 | yes | yes |
| mp_kowloon.ff | dlc/dlc2/english | 34,369,408 | 473 | yes | yes | 1,381 | 67,714,603 | 753 | 790 | yes | yes |
| mp_kowloon_load.ff | dlc/dlc2/english | 1,056 | 473 | yes | yes | 2 | 2,503 | 9 | 0 | yes | yes |
| mp_stadium.ff | dlc/dlc2/english | 33,134,144 | 473 | yes | yes | 1,416 | 69,412,176 | 818 | 612 | yes | yes |
| mp_stadium_load.ff | dlc/dlc2/english | 1,056 | 473 | yes | yes | 2 | 2,503 | 9 | 0 | yes | yes |
| zombie_cosmodrome.ff | dlc/dlc2/english | 47,958,272 | 473 | yes | yes | 1,940 | 95,166,328 | 1,162 | 775 | yes | yes |
| zombie_cosmodrome_load.ff | dlc/dlc2/english | 1,056 | 473 | yes | yes | 2 | 2,510 | 9 | 0 | yes | yes |
| mp_berlinwall2.ff | dlc/dlc2/french | 36,163,488 | 473 | yes | yes | 1,420 | 69,625,663 | 658 | 649 | yes | yes |
| mp_berlinwall2_load.ff | dlc/dlc2/french | 1,056 | 473 | yes | yes | 2 | 2,507 | 9 | 0 | yes | yes |
| mp_discovery.ff | dlc/dlc2/french | 36,704,640 | 473 | yes | yes | 1,428 | 70,043,107 | 680 | 934 | yes | yes |
| mp_discovery_load.ff | dlc/dlc2/french | 1,056 | 473 | yes | yes | 2 | 2,505 | 9 | 0 | yes | yes |
| mp_kowloon.ff | dlc/dlc2/french | 34,357,728 | 473 | yes | yes | 1,379 | 67,611,099 | 753 | 790 | yes | yes |
| mp_kowloon_load.ff | dlc/dlc2/french | 1,056 | 473 | yes | yes | 2 | 2,503 | 9 | 0 | yes | yes |
| mp_stadium.ff | dlc/dlc2/french | 33,120,800 | 473 | yes | yes | 1,413 | 69,308,672 | 818 | 612 | yes | yes |
| mp_stadium_load.ff | dlc/dlc2/french | 1,056 | 473 | yes | yes | 2 | 2,503 | 9 | 0 | yes | yes |
| zombie_cosmodrome.ff | dlc/dlc2/french | 47,963,168 | 473 | yes | yes | 1,940 | 95,157,576 | 1,162 | 775 | yes | yes |
| zombie_cosmodrome_load.ff | dlc/dlc2/french | 1,056 | 473 | yes | yes | 2 | 2,510 | 9 | 0 | yes | yes |
| mp_gridlock.ff | dlc/dlc3/english | 38,222,208 | 473 | yes | yes | 1,456 | 71,408,718 | 716 | 708 | yes | yes |
| mp_gridlock_load.ff | dlc/dlc3/english | 1,024 | 473 | yes | yes | 2 | 2,466 | 9 | 0 | yes | yes |
| mp_hotel.ff | dlc/dlc3/english | 35,997,504 | 473 | yes | yes | 1,446 | 70,929,445 | 766 | 745 | yes | yes |
| mp_hotel_load.ff | dlc/dlc3/english | 1,024 | 473 | yes | yes | 2 | 2,463 | 9 | 0 | yes | yes |
| mp_outskirts.ff | dlc/dlc3/english | 36,646,400 | 473 | yes | yes | 1,460 | 71,612,596 | 701 | 816 | yes | yes |
| mp_outskirts_load.ff | dlc/dlc3/english | 1,024 | 473 | yes | yes | 2 | 2,467 | 9 | 0 | yes | yes |
| mp_zoo.ff | dlc/dlc3/english | 35,560,544 | 473 | yes | yes | 1,498 | 73,466,035 | 679 | 727 | yes | yes |
| mp_zoo_load.ff | dlc/dlc3/english | 1,024 | 473 | yes | yes | 2 | 2,461 | 9 | 0 | yes | yes |
| zombie_coast.ff | dlc/dlc3/english | 51,769,248 | 473 | yes | yes | 1,942 | 95,236,212 | 1,187 | 732 | yes | yes |
| zombie_coast_load.ff | dlc/dlc3/english | 704 | 473 | yes | yes | 2 | 1,919 | 9 | 0 | yes | yes |
| mp_gridlock.ff | dlc/dlc3/french | 38,207,424 | 473 | yes | yes | 1,454 | 71,305,214 | 716 | 708 | yes | yes |
| mp_gridlock_load.ff | dlc/dlc3/french | 1,024 | 473 | yes | yes | 2 | 2,466 | 9 | 0 | yes | yes |
| mp_hotel.ff | dlc/dlc3/french | 35,992,064 | 473 | yes | yes | 1,444 | 70,823,931 | 766 | 745 | yes | yes |
| mp_hotel_load.ff | dlc/dlc3/french | 1,024 | 473 | yes | yes | 2 | 2,463 | 9 | 0 | yes | yes |
| mp_outskirts.ff | dlc/dlc3/french | 36,637,856 | 473 | yes | yes | 1,458 | 71,509,092 | 701 | 816 | yes | yes |
| mp_outskirts_load.ff | dlc/dlc3/french | 1,024 | 473 | yes | yes | 2 | 2,467 | 9 | 0 | yes | yes |
| mp_zoo.ff | dlc/dlc3/french | 35,545,664 | 473 | yes | yes | 1,496 | 73,362,531 | 679 | 727 | yes | yes |
| mp_zoo_load.ff | dlc/dlc3/french | 1,024 | 473 | yes | yes | 2 | 2,461 | 9 | 0 | yes | yes |
| zombie_coast.ff | dlc/dlc3/french | 51,759,264 | 473 | yes | yes | 1,941 | 95,210,071 | 1,187 | 732 | yes | yes |
| zombie_coast_load.ff | dlc/dlc3/french | 704 | 473 | yes | yes | 2 | 1,919 | 9 | 0 | yes | yes |
| mp_area51.ff | dlc/dlc4/english | 37,104,512 | 473 | yes | yes | 1,448 | 71,002,540 | 803 | 891 | yes | yes |
| mp_area51_load.ff | dlc/dlc4/english | 1,024 | 473 | yes | yes | 2 | 2,458 | 9 | 0 | yes | yes |
| mp_drivein.ff | dlc/dlc4/english | 37,755,712 | 473 | yes | yes | 1,437 | 70,466,225 | 738 | 724 | yes | yes |
| mp_drivein_load.ff | dlc/dlc4/english | 1,024 | 473 | yes | yes | 2 | 2,459 | 9 | 0 | yes | yes |
| mp_golfcourse.ff | dlc/dlc4/english | 39,124,192 | 473 | yes | yes | 1,456 | 71,375,376 | 711 | 550 | yes | yes |
| mp_golfcourse_load.ff | dlc/dlc4/english | 1,024 | 473 | yes | yes | 2 | 2,462 | 9 | 0 | yes | yes |
| mp_silo.ff | dlc/dlc4/english | 37,166,528 | 473 | yes | yes | 1,485 | 72,819,251 | 748 | 651 | yes | yes |
| mp_silo_load.ff | dlc/dlc4/english | 1,024 | 473 | yes | yes | 2 | 2,456 | 9 | 0 | yes | yes |
| zombie_temple.ff | dlc/dlc4/english | 53,441,504 | 473 | yes | yes | 2,009 | 98,565,853 | 1,355 | 723 | yes | yes |
| zombie_temple_load.ff | dlc/dlc4/english | 1,024 | 473 | yes | yes | 2 | 2,462 | 9 | 0 | yes | yes |
| mp_area51.ff | dlc/dlc4/french | 37,086,592 | 473 | yes | yes | 1,446 | 70,899,036 | 803 | 891 | yes | yes |
| mp_area51_load.ff | dlc/dlc4/french | 1,024 | 473 | yes | yes | 2 | 2,458 | 9 | 0 | yes | yes |
| mp_drivein.ff | dlc/dlc4/french | 37,738,752 | 473 | yes | yes | 1,435 | 70,362,721 | 738 | 724 | yes | yes |
| mp_drivein_load.ff | dlc/dlc4/french | 1,024 | 473 | yes | yes | 2 | 2,459 | 9 | 0 | yes | yes |
| mp_golfcourse.ff | dlc/dlc4/french | 39,104,544 | 473 | yes | yes | 1,453 | 71,269,862 | 711 | 550 | yes | yes |
| mp_golfcourse_load.ff | dlc/dlc4/french | 1,024 | 473 | yes | yes | 2 | 2,462 | 9 | 0 | yes | yes |
| mp_silo.ff | dlc/dlc4/french | 37,164,608 | 473 | yes | yes | 1,483 | 72,715,747 | 748 | 651 | yes | yes |
| mp_silo_load.ff | dlc/dlc4/french | 1,024 | 473 | yes | yes | 2 | 2,456 | 9 | 0 | yes | yes |
| zombie_temple.ff | dlc/dlc4/french | 53,107,488 | 473 | yes | yes | 2,001 | 98,163,925 | 1,355 | 723 | yes | yes |
| zombie_temple_load.ff | dlc/dlc4/french | 1,024 | 473 | yes | yes | 2 | 2,462 | 9 | 0 | yes | yes |
| zombie_moon.ff | dlc/dlc5/english | 46,293,536 | 473 | yes | yes | 1,957 | 95,989,814 | 1,258 | 699 | yes | yes |
| zombie_moon_load.ff | dlc/dlc5/english | 928 | 473 | yes | yes | 2 | 1,299 | 5 | 0 | yes | yes |
| zombie_moon.ff | dlc/dlc5/french | 46,292,768 | 473 | yes | yes | 1,956 | 95,966,628 | 1,258 | 699 | yes | yes |
| zombie_moon_load.ff | dlc/dlc5/french | 928 | 473 | yes | yes | 2 | 1,299 | 5 | 0 | yes | yes |
| common_zombie_patch.ff | dlc/english | 500,640 | 473 | yes | yes | 17 | 777,344 | 137 | 163 | yes | yes |
| patch.ff | dlc/english | 85,504 | 473 | yes | yes | 16 | 695,479 | 426 | 1 | yes | yes |
| patch_mp.ff | dlc/english | 1,196,128 | 473 | yes | yes | 77 | 3,715,811 | 1,337 | 212 | yes | yes |
| patch_ui.ff | dlc/english | 1,048,864 | 473 | yes | yes | 93 | 4,513,012 | 31 | 0 | yes | yes |
| patch_ui_mp.ff | dlc/english | 4,099,840 | 473 | yes | yes | 279 | 13,611,499 | 130 | 0 | yes | yes |
| zombie_coast_patch.ff | dlc/english | 432,032 | 473 | yes | yes | 19 | 858,910 | 103 | 35 | yes | yes |
| zombie_cod5_asylum_patch.ff | dlc/english | 437,600 | 473 | yes | yes | 19 | 857,642 | 21 | 19 | yes | yes |
| zombie_cod5_factory_patch.ff | dlc/english | 433,376 | 473 | yes | yes | 19 | 853,697 | 21 | 19 | yes | yes |
| zombie_cod5_prototype_patch.ff | dlc/english | 433,760 | 473 | yes | yes | 19 | 853,968 | 21 | 19 | yes | yes |
| zombie_cod5_sumpf_patch.ff | dlc/english | 440,768 | 473 | yes | yes | 19 | 861,430 | 25 | 19 | yes | yes |
| zombie_cosmodrome_patch.ff | dlc/english | 466,432 | 473 | yes | yes | 20 | 919,212 | 131 | 35 | yes | yes |
| zombie_moon_patch.ff | dlc/english | 46,176 | 473 | yes | yes | 2 | 46,183 | 16 | 0 | yes | yes |
| zombie_pentagon_patch.ff | dlc/english | 419,104 | 473 | yes | yes | 19 | 843,849 | 98 | 35 | yes | yes |
| zombie_temple_patch.ff | dlc/english | 430,784 | 473 | yes | yes | 19 | 856,764 | 101 | 35 | yes | yes |
| zombie_theater_patch.ff | dlc/english | 409,344 | 473 | yes | yes | 19 | 847,582 | 146 | 35 | yes | yes |
| zombietron_patch.ff | dlc/english | 87,328 | 473 | yes | yes | 3 | 87,077 | 12 | 0 | yes | yes |
| common_zombie_patch.ff | dlc/french | 500,640 | 473 | yes | yes | 17 | 777,590 | 137 | 163 | yes | yes |
| patch.ff | dlc/french | 86,848 | 473 | yes | yes | 16 | 697,330 | 426 | 1 | yes | yes |
| patch_mp.ff | dlc/french | 1,197,216 | 473 | yes | yes | 77 | 3,717,638 | 1,337 | 212 | yes | yes |
| patch_ui.ff | dlc/french | 1,048,800 | 473 | yes | yes | 93 | 4,513,012 | 31 | 0 | yes | yes |
| patch_ui_mp.ff | dlc/french | 4,099,904 | 473 | yes | yes | 279 | 13,611,506 | 130 | 0 | yes | yes |
| zombie_coast_patch.ff | dlc/french | 432,032 | 473 | yes | yes | 19 | 858,910 | 103 | 35 | yes | yes |
| zombie_cod5_asylum_patch.ff | dlc/french | 437,600 | 473 | yes | yes | 19 | 857,642 | 21 | 19 | yes | yes |
| zombie_cod5_factory_patch.ff | dlc/french | 433,376 | 473 | yes | yes | 19 | 853,697 | 21 | 19 | yes | yes |
| zombie_cod5_prototype_patch.ff | dlc/french | 433,760 | 473 | yes | yes | 19 | 853,968 | 21 | 19 | yes | yes |
| zombie_cod5_sumpf_patch.ff | dlc/french | 440,768 | 473 | yes | yes | 19 | 861,430 | 25 | 19 | yes | yes |
| zombie_cosmodrome_patch.ff | dlc/french | 466,432 | 473 | yes | yes | 20 | 919,212 | 131 | 35 | yes | yes |
| zombie_moon_patch.ff | dlc/french | 46,144 | 473 | yes | yes | 2 | 46,178 | 15 | 0 | yes | yes |
| zombie_pentagon_patch.ff | dlc/french | 419,104 | 473 | yes | yes | 19 | 843,849 | 98 | 35 | yes | yes |
| zombie_temple_patch.ff | dlc/french | 430,784 | 473 | yes | yes | 19 | 856,764 | 101 | 35 | yes | yes |
| zombie_theater_patch.ff | dlc/french | 409,344 | 473 | yes | yes | 19 | 847,582 | 146 | 35 | yes | yes |
| zombietron_patch.ff | dlc/french | 87,328 | 473 | yes | yes | 3 | 87,077 | 12 | 0 | yes | yes |

<!-- INVENTORY END -->
