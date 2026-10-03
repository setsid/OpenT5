# R7: the .pak container (streamed images and sound)

Status: complete for what an editor needs. Every field below is read from the files, and the
loader's use of each is taken from `t5mp.elf`. Code: `src/opent5/container/pak.py`; tool
`tools/pak_all.py`; tests `tests/test_pak.py` (synthetic), `tests/test_pak_edit.py`
(synthetic zone and paks), `tests/test_pak_zones.py` (real files). textures.md 2.4 and 2.5
gave the first outline (slot table, part records); this document completes it and corrects
two details (section 7).

Conventions. Disc folder = `PS3_GAME/USRDIR/english` (and `french`), update folder =
`dev_hdd0/game/BLES01031/USRDIR/english`. ELF addresses are virtual addresses as the
disassembly shows them; for the read-only data used here the file offset is the address
minus 0x10000 (string `%s.pak` at address 0x8f4ee0 is at file offset 0x8e4ee0). Zone offsets
are offsets in the inflated zone (`.zone`), as in textures.md.

## 1. Summary

| Question | Answer | Evidence |
|---|---|---|
| Layout | 0x1c-byte header, u32 start-sector table, zero padding to the header size, then entries, each on a 0x800 boundary and zero-padded to the next | 2, 4; all 54 paks |
| Field 0x08 | A per-build value (0 sound and images_low, 1 MP maps, 2 SP/zombie maps and frontend, 3 ui_mp, 7 common, 0x11 img_patch). The loader stores it in a global that no code reads; it plays no part in finding an entry | 2.2 |
| Entry sizes | Not stored. The reader asks for a byte count (for images, the part record's size); an entry's span is from its start to the next entry in file order | 3, 4 |
| Table order | Ascending in 52 of 54 paks; the other two are img_patch.pak (update folder and its copy). img_patch.pak is laid out in two regions: resident data (single-part images and mip tails) first, streamed levels after; the table keeps the order the images were added | 2.3 |
| Compression | None. Entries are raw GPU texture data (DXT blocks, swizzled texels) exactly as in a DDS level; sound packs hold raw sound data | 3 |
| Checksums | None in the pak. The loader does not check the magic, does not hash entries, and reads `start << 11` for `size` bytes. GfxImage +0x6c is a name hash, not a content hash | 2.1, 6 |
| Trailing data | None: the last entry ends the file; bytes after the start table are zero | 4 |
| Lookup | `<zone>.pak` (the zone's own name plus `.pak`) through the same content-folder lookup as the zone; on the disc every map's pak sits beside its `.ff` | 5 |
| Round trip | 54 of 54 paks on this machine (11.9 GB) rewritten byte-identical from the parsed header and entries | 8 |
| Edits | Same-size part replacement keeps every offset; other sizes re-lay the file out in its original order. The zone does not change for a same-size image | 9 |

## 2. Header

All values big-endian.

| Off | Size | Field | mp_nuked | images_low | common | img_patch | snd.all |
|---|---|---|---|---|---|---|---|
| 0x00 | 4 | magic `pak2` | | | | | |
| 0x04 | u32 | build time, unix seconds | 0x4ca3e2ee (2010-09-30 01:07 UTC) | 0x4ca3e97a | 0x4ca3e97a | 0x4e2e1cd6 (2011-07-26) | 0x4ca3bd5b |
| 0x08 | u32 | build value (2.2) | 1 | 0 | 7 | 0x11 | 0 |
| 0x0c | u32 | entry count | 0x805 | 0x2fbc | 0x89e | 0x100 | 0xf5c |
| 0x10 | u32 | sector size | 0x800 | 0x800 | 0x800 | 0x800 | 0x800 |
| 0x14 | u32 | header bytes | 0x2800 | 0xc000 | 0x2800 | 0x800 | 0x4000 |
| 0x18 | u32 | 0 | 0 | 0 | 0 | 0 | 0 |
| 0x1c | u32[count] | start sector of each entry | 5, 0x45, 0x55 ... | 0x18, 0x19, 0x1b ... | 5, 0xd, 0xf ... | 0xd90, 0xf90, 0x1010 ... | 8, 0xc7, 0xd2 ... |

```
images_low.pak 00000000: 7061 6b32 4ca3 e97a 0000 0000 0000 2fbc  pak2L..z....../.
               00000010: 0000 0800 0000 c000 0000 0000 0000 0018  ................
img_patch.pak  00000000: 7061 6b32 4e2e 1cd6 0000 0011 0000 0100  pak2N...........
               00000010: 0000 0800 0000 0800 0000 0000 0000 0d90  ................
mp_nuked.pak   00002010: 0001 4796 0001 47a6 0001 47aa 0001 47ea  (last starts)
               00002030: 0000 0000 0000 0000 0000 0000 0000 0000  (zero to 0x2800)
```

Header bytes = round_up(0x1c + 4 x count, 0x800) in all 54 paks (mp_nuked: 0x1c + 0x2014 =
0x2030 -> 0x2800; images_low: 0xbf0c -> 0xc000; empty paks such as `terminal.pak`: 0x800).
The bytes between the table and the header end are zero in all 54. The first entry starts
exactly at the header end in all 54 (no gap).

### 2.1 How the loader reads the header

The pak streaming code is at 0x3c8c00..0x3cbc00 in `t5mp.elf`; its pak records are an array
of 292-byte structs at 0x1609334 (`lis r5,353; addi r0,r5,-27852`, 0x3c91bc). After opening:

- 0x3c9288..0x3c92b0: reads 2048 bytes (`li r5,2048; bl 0x3cba00; cmpwi r3,2048`); a short
  read is an error.
- 0x3c93e4..0x3c93f8: loads +0x14 (`lwz r6,20(r11)`) and +0x0c (`lwz r8,12(r11)`); if
  +0x14 > 0x800 the rest of the header is read (0x3c97b8). So +0x14 sizes the header read.
- Header buffers come from one pool of 0x3c000 bytes (`ori r28,r26,49152`, compare at
  0x3c978c; a second check against 0x3b800 at 0x3c91f8 prints an error with the size in
  KB): the headers of all open paks together must fit in 240 KiB. An editor that added
  entries would have to respect this; one that replaces entries does not change the header
  size.
- 0x3c96a0: +0x0c (count) stored in the record at +272; 0x3c96b0: +0x10 stored at +268.
- 0x3c96d8..0x3c96e0: +0x08 passed to 0x231dd8, which only stores it at 0xb5be44
  (`stw r3,-16828(r9)`). No `lis`/`addi` or `lis`/load pair anywhere in the ELF reads that
  address back (scan of every instruction; the only reference is the store), so the value
  is kept and not used.
- No check of the magic: the bytes `pak2` (either byte order) do not occur in the ELF, and no
  instruction builds 0x7061 or 0x6b32 as an immediate.

### 2.2 Field 0x08

| Value | Paks |
|---|---|
| 0 | images_low, snd.all, snd.english, snd.french, snd_dev.* |
| 1 | the 14 MP map paks, outro, so_narrative1..5_frontend, terminal |
| 2 | the SP campaign paks, frontend, zombie_pentagon, zombie_theater, zombietron, and mp_havoc |
| 3 | ui_mp |
| 7 | common |
| 0x11 | img_patch |

It is not the pak slot of textures.md 2.4 (images_low is slot 1, common 3, ui_mp 4, img_patch
9) and the loader does not use it (2.1). INFERRED: a build-tool value (category or flags); it
would be confirmed by the tool that wrote the paks, which is not available. The writer keeps
it unchanged.

### 2.3 The order of the start table

52 of 54 paks have strictly ascending starts. img_patch.pak (update folder, and its copy in
the WAD dump, the other two) does not: entries 0..56 start at 0xd90 and rise, entry 57 starts at sector 1.
Sorting its entries by start shows two regions:

| Region | Sectors | Entries | Capacities (sectors) |
|---|---|---|---|
| A | 1 .. 0xd8f | 69 | 128 (x24), 256 (x1), 64 (x1), 1..3 (x43) |
| B | 0xd90 .. 0x2804 | 187 | 512, 128, 32, ... in groups of two or three |

Region A ends exactly where region B begins (sector 3472 = 0xd90). patch_mp's 25 single-part
images (mode 2: the compass maps, 512x512, 0x40000 bytes = 128 sectors, and
`loadscreen_mp_discovery2`, 1024x1024 DXT1, 0x80000 = 256 sectors) are all in region A
(for example `compass_map_mp_berlinwall2`, slot 9 entry 57, start sector 1). The 1..3-sector
entries are the sizes of mip tails (0x580, 0xb00, 0x1580 bytes), each listed in the table
right after a group of region-B level parts of matching format (entries 66, 67, 68 =
512 + 128 + 32 sectors = 1024, 512, 256 DXT5 levels, then entry 69, 3 sectors = a DXT5
64x64 7-level tail). So the table is in the order the images were added, and the file holds
what the base game keeps in images_low.pak (always loaded: tails and single-part images)
first and the streamed levels after. INFERRED: that the split is by residency (the patch
cannot change images_low.pak, so its tails live here); confirmed by sizes only.

The reader does not depend on the order: an entry's span is from its start to the next
start in file order (section 4), and the writer keeps file order (section 9).

## 3. Entries

No per-entry header, size, compression or checksum:

- mp_nuked.pak entry 0 (start 5, offset 0x2800) begins `fbde 34a5 55a0 0000`, a DXT1 block;
  entry 702 (offset 0x2fdc000) begins `11ad 0a6b 9e98 d898`, a DXT1 block of
  `~-gmp_nuked_townsign_c`. Decoded with the DDS rules of textures.md they are the images
  (textures.md 5; section 10 here).
- Part sizes. A streamed image's part records (textures.md 2.4) give each part's byte size.
  Modelled as "part k holds the levels from its own width down to twice the previous part's
  width, padded to 128 bytes" (`tx.face_size(fmt, w_k, h_k, mips_k - mips_k-1)`), the size
  matches the record for 15 341 of 15 341 parts in the nine examined zones (mp_nuked,
  mp_firingrange, zombie_theater, common_mp, code_post_gfx_mp, ui_mp, patch, patch_mp,
  patch_ui_mp: 4 549 streamed images). A compressed store could not match the GPU layout
  size this exactly.
- Padding. After each part's last byte, the bytes to the next sector boundary are zero:
  3 292 padded entries of images_low.pak checked, 0 non-zero; mp_nuked.pak, common.pak and
  ui_mp.pak parts used by these zones are whole sectors (2 053 + 1 993 + 943 entries).
- Contiguity. For every entry whose size is known from a part record, the next entry starts
  at exactly round_up(size, 0x800): mp_nuked 2 052 of 2 052 gaps, mp_firingrange 2 626,
  zombie_theater 2 974, images_low 4 046, common 1 993, ui_mp 943. No holes.

How the loader reads an entry (0x3c9038..0x3c908c): the entry index is compared with the
header count (`lwz r7,12(r6); cmplw cr1,r10,r7; bge` to the error at 0x3c9258); the file
position is `start << 11` (`rlwinm r11,r3,11,0,20`, a fixed 0x800, not the +0x10 field) plus
the request's own offset (`lwz r4,260(r31)`). The size comes from the request. Two
consequences: a start must be below 2^21 (the shift is 32-bit), and an entry may grow as long
as the part records ask for the new size.

The pak start word can also carry a pak number: 0x3c9484..0x3c963c ORs the record's
+280 byte, shifted to bits 31..24, into each start that is not 0xffffffff and stores it into
another pak's table. This is an overlay of one pak's table on another's (entries of -1 are
skipped). None of the 54 paks has a 0xffffffff start, and the editor does not use it.

## 4. Sizes, spans and the end of the file

Because sizes are not stored, the reader (`opent5.container.pak.Pak`) gives each entry a
*capacity*: from its start to the next start in file order, or to the end of the file for
the last one. With no holes and zero padding (section 3) the capacity is the part size
rounded up to 0x800. The last entry ends the file in all 54 paks (mp_nuked: last start
0x14812 x 0x800 + 0x800 = 0xa409800 = 172 005 376 bytes, the file size). For an image the
part record remains the authority for how many bytes are meaningful.

## 5. Which file the game opens

Strings and the code that formats them (0x266788, the pak-open request handler):

| Address | String | Use |
|---|---|---|
| 0x8f4ed0 | `img_patch` | 0x26683c: `strcmp(name, "img_patch")` |
| 0x8f4ee0 | `%s.pak` | 0x266900: file name for any other request: the requested name plus `.pak` |
| 0x8f4eb8 | `%s/%s%s` | 0x26692c: base folder / language folder / file name |
| 0x8f4ea8 | `%s.all.pak` | 0x266950: request type 3 (sound): `snd.all.pak` |
| 0x8f4ec0 | `%s.%s.pak` | 0x2669cc: sound, with the language: `snd.english.pak` |
| 0x8f4b40 | `/patch/%s%s%s` | 0x26686c: img_patch is looked for under `/patch/` first, then the normal folder (0x266a78) |
| 0x92a9f8 | `%s/%s/%s.pak` | 0x55b740: DLC content packs (with `%s.edat` at 0x92a9f0) |

The base folder comes from 0x55bdf8, which looks the zone name up in the DLC content-pack
table and returns an empty string for base-game zones; the zone's own `.ff` path is built
the same way (0x261000..0x261068: `%s/%s%s%s` with the same base, the zone name and `.ff`).
The language-folder strings are runtime globals (0x1a12154..0x1a12160, set at 0x55e2c8 from
one buffer), so the ELF alone does not prove that the `.ff` folder and the `.pak` folder are
the same string. The files do: on the disc every map's pak is beside its zone with the same
name (`english/mp_nuked.ff` + `english/mp_nuked.pak`: 43 of the 48 English paks; the other
five are images_low and the sound packs, which belong to no zone), and no zone has its pak
elsewhere. INFERRED therefore: the game opens `<zone>.pak` from the folder of `<zone>.ff`;
the demo in docs/demo-pak.md confirms it when the edited texture appears.

Consequence for a user: an edited level pak must be copied into the same game folder as the
zone, with the zone's name (`mp_nuked.pak` beside `mp_nuked.ff`). A pak-only edit leaves the
`.ff` byte-identical (section 9), so copying the pak alone is enough; copying both does no
harm.

Shared paks. images_low.pak (slot 1), common.pak (3) and ui_mp.pak (4) are opened by name for
every zone (`images_low.pak` at 0x92aa98, `common.pak` at 0x92aae8). Each of their entries
belongs to one image (7 007 shared-pak entries referenced by the nine zones, none by two
image names), but the same image can be in several zones: 580 of those entries are used by
two of the nine zones and 50 by three. Writing a shared pak changes that image in every
zone that uses it.

## 6. GfxImage fields tied to the pak

| Off | Field | Rule | Evidence |
|---|---|---|---|
| 0x28 | u32 LE | total streamed bytes / (4 x part count) | 4 549 of 4 549 streamed images (e.g. mp_nuked `~-gus_art_wall_vinylsiding_white_c`: total 0x2ab00, 4 parts, `b02a0000` = 0x2ab0) |
| 0x30 | u32 LE | total streamed bytes = last part's cumulative size | 4 549 of 4 549 |
| 0x34 | 4 x 12 | part records (textures.md 2.4) | 15 341 part sizes match section 3 |
| 0x64 | u8 | part count | |
| 0x6c | u32 | name hash | below |

+0x6c is not a content hash: 428 image names appear in more than one of the nine zones, with
different pak entries (`p_us_pictureframes_painting_set01_n`: mp_nuked.pak entries 30..32,
zombie_theater.pak entries 591..593), and in every case the hash is the same. For 375 of
4 549 streamed images (the generated `~$black-r&$white-r&...` names) it equals R_HashString
of the name (djb2 with XOR, case-folded, seed 0; OpenAssetTools,
src/Common/Game/T5/CommonT5.h); for the rest it is presumably the same hash of a source
name that the zone does not keep (INFERRED; would be confirmed by the linker). Replacing an
image's pixels at the same size changes none of these fields, so the zone stays as it was.

## 7. Corrections to textures.md

- 2.5 said the GfxImage part record is the authority for size because img_patch's table
  is not ascending. The span to the next start in file order (section 4) is also exact; the
  record is still what says how many bytes are meaningful.
- The ELF addresses quoted in textures.md 2.4 (`images_low.pak` 0x91aa98, `common.pak`
  0x91aae8, `img_patch` 0x8e4ed0, `%s/%s/%s.pak` 0x91a9f8) are file offsets; the virtual
  addresses are 0x10000 higher.
- +0x28 is now explained (section 6).

## 8. Inventory and round trip of every pak on the machine

Searched recursively: the disc USRDIR (english and french), the update USRDIR, the HDD/DLC
root of `.env` (`/home/cbolland/bo1-zones/hdd`: no paks; `dlc1..5.edat.off` there are 336-byte
NPD licence headers, not paks), and the WAD dump folder. A wider search of the Windows user
folder found no other file starting with `pak2`. DLC map paks exist only inside the
unextracted DLC package. 54 paks, 11.9 GB.

`tools/pak_all.py` opens each pak, rebuilds the header from the parsed fields and the start
table from the layout, writes every entry from its span, hashes the stream, and compares with
the file's sha1. Result: 54 of 54 byte-identical; all 54 follow the header-size rule, have
zero bytes after the table, no gap before the first entry and no shared starts.

| Where | File | Bytes | Built (UTC) | 0x08 | Entries | Header | Table order | Round trip | sha1 |
|---|---|---|---|---|---|---|---|---|---|
| disc | english/common.pak | 368,805,888 | 2010-09-30 01:35 | 0x7 | 2206 | 0x2800 | ascending | identical | `530521d957c0` |
| disc | english/creek_1.pak | 317,384,704 | 2010-09-30 00:34 | 0x2 | 3333 | 0x3800 | ascending | identical | `8cb9ea7b8882` |
| disc | english/cuba.pak | 439,445,504 | 2010-09-30 00:37 | 0x2 | 5174 | 0x5800 | ascending | identical | `f1e7c597f823` |
| disc | english/flashpoint.pak | 373,727,232 | 2010-09-30 00:40 | 0x2 | 4240 | 0x4800 | ascending | identical | `dbb8a42b618e` |
| disc | english/frontend.pak | 49,203,200 | 2010-09-30 00:28 | 0x2 | 761 | 0x1000 | ascending | identical | `858e8185b457` |
| disc | english/fullahead.pak | 285,227,008 | 2010-09-30 00:42 | 0x2 | 3224 | 0x3800 | ascending | identical | `fd8ad4368a50` |
| disc | english/hue_city.pak | 349,048,832 | 2010-09-30 00:44 | 0x2 | 4046 | 0x4000 | ascending | identical | `506e53b3679b` |
| disc | english/images_low.pak | 145,166,336 | 2010-09-30 01:35 | 0x0 | 12220 | 0xc000 | ascending | identical | `49d0a9618a0c` |
| disc | english/int_escape.pak | 287,827,968 | 2010-09-30 00:46 | 0x2 | 3553 | 0x3800 | ascending | identical | `cd61ca5a1e38` |
| disc | english/khe_sanh.pak | 303,167,488 | 2010-09-30 00:48 | 0x2 | 2824 | 0x3000 | ascending | identical | `e1e35ee339b4` |
| disc | english/kowloon.pak | 329,981,952 | 2010-09-30 00:50 | 0x2 | 4448 | 0x4800 | ascending | identical | `ebc07c89a9e8` |
| disc | english/mp_array.pak | 216,434,688 | 2010-09-30 00:52 | 0x1 | 3318 | 0x3800 | ascending | identical | `d66982ba6fdc` |
| disc | english/mp_cairo.pak | 199,393,280 | 2010-09-30 00:54 | 0x1 | 2957 | 0x3000 | ascending | identical | `ccddae7071b1` |
| disc | english/mp_cosmodrome.pak | 179,122,176 | 2010-09-30 00:55 | 0x1 | 2735 | 0x3000 | ascending | identical | `c86dfd2db08c` |
| disc | english/mp_cracked.pak | 192,548,864 | 2010-09-30 00:57 | 0x1 | 2892 | 0x3000 | ascending | identical | `dc136715ecf2` |
| disc | english/mp_crisis.pak | 228,622,336 | 2010-09-30 00:58 | 0x1 | 2868 | 0x3000 | ascending | identical | `0fe683b22028` |
| disc | english/mp_duga.pak | 192,446,464 | 2010-09-30 01:00 | 0x1 | 3101 | 0x3800 | ascending | identical | `0acd38d35abf` |
| disc | english/mp_firingrange.pak | 234,217,472 | 2010-09-30 01:01 | 0x1 | 2627 | 0x3000 | ascending | identical | `e3d93f811410` |
| disc | english/mp_hanoi.pak | 226,029,568 | 2010-09-30 01:03 | 0x1 | 3171 | 0x3800 | ascending | identical | `3c17242e42eb` |
| disc | english/mp_havoc.pak | 196,093,952 | 2010-09-30 01:05 | 0x2 | 1927 | 0x2000 | ascending | identical | `5412d7bc7413` |
| disc | english/mp_mountain.pak | 167,041,024 | 2010-09-30 01:06 | 0x1 | 2620 | 0x3000 | ascending | identical | `6e6135f9cb85` |
| disc | english/mp_nuked.pak | 172,005,376 | 2010-09-30 01:07 | 0x1 | 2053 | 0x2800 | ascending | identical | `5930c6c683d9` |
| disc | english/mp_radiation.pak | 182,685,696 | 2010-09-30 01:09 | 0x1 | 2864 | 0x3000 | ascending | identical | `e7b3b4e6ef7c` |
| disc | english/mp_russianbase.pak | 238,538,752 | 2010-09-30 01:10 | 0x1 | 3274 | 0x3800 | ascending | identical | `d0199cc8862d` |
| disc | english/mp_villa.pak | 157,915,136 | 2010-09-30 01:11 | 0x1 | 2306 | 0x2800 | ascending | identical | `52c01d7a6577` |
| disc | english/outro.pak | 346,112 | 2010-09-30 01:12 | 0x1 | 6 | 0x800 | ascending | identical | `3065383f2f6f` |
| disc | english/pentagon.pak | 264,208,384 | 2010-09-30 01:14 | 0x2 | 3506 | 0x3800 | ascending | identical | `eee0bd8db804` |
| disc | english/pow.pak | 321,034,240 | 2010-09-30 01:15 | 0x2 | 3417 | 0x3800 | ascending | identical | `9f0633447e8a` |
| disc | english/rebirth.pak | 430,360,576 | 2010-09-30 01:17 | 0x2 | 5261 | 0x5800 | ascending | identical | `ebb8e83dae13` |
| disc | english/river.pak | 369,065,984 | 2010-09-30 01:19 | 0x2 | 3440 | 0x3800 | ascending | identical | `2c13e1c76fab` |
| disc | english/snd.all.pak | 612,390,912 | 2010-09-29 22:27 | 0x0 | 3932 | 0x4000 | ascending | identical | `e67d0e64a094` |
| disc | english/snd.english.pak | 355,721,216 | 2010-09-29 22:26 | 0x0 | 11875 | 0xc000 | ascending | identical | `9d03d8b9ee64` |
| disc | english/snd_dev.all.pak | 2,048 | 2010-09-29 22:28 | 0x0 | 0 | 0x800 | ascending | identical | `84d51db4783d` |
| disc | english/snd_dev.english.pak | 2,048 | 2010-09-29 22:27 | 0x0 | 0 | 0x800 | ascending | identical | `90ed2165401c` |
| disc | english/so_narrative1_frontend.pak | 124,928 | 2010-09-30 01:20 | 0x1 | 8 | 0x800 | ascending | identical | `fdd0bd5452f7` |
| disc | english/so_narrative2_frontend.pak | 124,928 | 2010-09-30 01:20 | 0x1 | 8 | 0x800 | ascending | identical | `b72cef5ad3d9` |
| disc | english/so_narrative3_frontend.pak | 124,928 | 2010-09-30 01:20 | 0x1 | 8 | 0x800 | ascending | identical | `9134cf3c76ee` |
| disc | english/so_narrative4_frontend.pak | 124,928 | 2010-09-30 01:21 | 0x1 | 8 | 0x800 | ascending | identical | `1b34592d32c3` |
| disc | english/so_narrative5_frontend.pak | 124,928 | 2010-09-30 01:21 | 0x1 | 8 | 0x800 | ascending | identical | `c3479b75ccab` |
| disc | english/terminal.pak | 2,048 | 2010-09-30 00:29 | 0x1 | 0 | 0x800 | ascending | identical | `1930416ea7ea` |
| disc | english/ui_mp.pak | 326,051,840 | 2010-09-30 01:11 | 0x3 | 1709 | 0x2000 | ascending | identical | `856ec998dcea` |
| disc | english/underwaterbase.pak | 312,334,336 | 2010-09-30 01:23 | 0x2 | 3412 | 0x3800 | ascending | identical | `a7cb848443a7` |
| disc | english/vorkuta.pak | 329,818,112 | 2010-09-30 01:24 | 0x2 | 4319 | 0x4800 | ascending | identical | `25fb2282a78e` |
| disc | english/wmd.pak | 347,129,856 | 2010-09-30 01:26 | 0x2 | 4314 | 0x4800 | ascending | identical | `8554cc1e7e28` |
| disc | english/wmd_sr71.pak | 326,567,936 | 2010-09-30 01:29 | 0x2 | 3384 | 0x3800 | ascending | identical | `5f68950de6d8` |
| disc | english/zombie_pentagon.pak | 267,083,776 | 2010-09-30 01:31 | 0x2 | 3884 | 0x4000 | ascending | identical | `cfbf18062386` |
| disc | english/zombie_theater.pak | 239,200,256 | 2010-09-30 01:33 | 0x2 | 3018 | 0x3000 | ascending | identical | `7982dea029ef` |
| disc | english/zombietron.pak | 383,639,552 | 2010-09-30 01:35 | 0x2 | 4575 | 0x4800 | ascending | identical | `27ad624fca1d` |
| disc | french/snd.all.pak | 612,390,912 | 2010-09-30 00:19 | 0x0 | 3932 | 0x4000 | ascending | identical | `12d66315f75e` |
| disc | french/snd.french.pak | 309,811,200 | 2010-09-30 00:18 | 0x0 | 10275 | 0xa800 | ascending | identical | `af661547cae4` |
| disc | french/snd_dev.all.pak | 2,048 | 2010-09-30 00:21 | 0x0 | 0 | 0x800 | ascending | identical | `93bc4504e893` |
| disc | french/snd_dev.french.pak | 2,048 | 2010-09-30 00:19 | 0x0 | 0 | 0x800 | ascending | identical | `26661a5d968a` |
| update | english/img_patch.pak | 20,981,760 | 2011-07-26 01:48 | 0x11 | 256 | 0x800 | not ascending | identical | `ddb0bd5e5310` |
| wads | img_patch.pak | 20,981,760 | 2011-07-26 01:48 | 0x11 | 256 | 0x800 | not ascending | identical | `ddb0bd5e5310` |

Time: 180 s for all 54 (files already in the page cache).

## 9. Editing

`Pak.replace(index, data)` records new bytes; `Pak.write(path)` writes the header, then every
entry in the original file order, each from its span (unchanged) or the new bytes plus zero
padding (edited). If the new bytes take the same number of sectors, the layout is unchanged;
otherwise every later entry (in file order) moves and the start table is rewritten
(`tests/test_pak.py`, also with img_patch's interleaved order). The entry count never changes,
so the header size and the loader's 240 KiB header pool are not affected. `Pak.write` refuses
to write over the file it read.

Streamed images through the edit API (`opent5.edit`, docs/edit-api.md):

- `doc.replace_image(key, rgba | dds)` on a streamed image encodes the full mip chain once at
  the image's own width, height, format and mip count, then splits it into each part's mip
  range (section 3 rule) and checks every part's size against its record.
- Same size only. A different width or height would change the part records, +0x28/+0x30,
  the CellGcmTexture, and the mip-tail part in images_low.pak, and the part count is already
  4 (the maximum) for most images; refused with the expected size.
- The zone does not change (the GfxImage fields are the same), so a pak-only edit saves a
  `.ff` that is byte-identical to the source and keeps its console signature.
- Shared paks. A mode-1 image's mip tail (64x64 or 32x32 and smaller) is in images_low.pak;
  some images have every part in common.pak or ui_mp.pak. By default only parts in the
  level's own pak are written; the shared parts are left as they were and the change's
  `detail` says so (the old picture then shows only at the distance where the tail mips are
  drawn). An image with no part in the level pak is refused unless `allow_shared=True`. With
  `allow_shared=True` the shared pak is written too, under its own name beside the zone, with
  a note that it changes the image in every zone that uses it (section 5).
- `doc.save(path)` writes `<stem of path>.pak` (and any shared pak) into the zone's output
  folder, never over a source pak or into a game folder; with verify it re-opens each written
  pak, checks header fields, every entry not edited byte-identical over its whole span, every
  edited entry holding the new bytes with zero padding, and decodes each edited image from
  the written files, expecting exactly the pixels the pending edit decodes to. Results are in
  `SaveReport.details["paks"]` and `["streamed_images_decoded"]`.

## 10. Demo

docs/demo-pak.md: `~-gmp_nuked_townsign_c` (the "Welcome to NUKETOWN Population" sign and the
fallout-shelter panels, DXT1 512x512) replaced with an "OPENT5 PAK" pattern. mp_nuked.pak
entries 702, 703 and 704 changed (512, 256, 128 levels); the other 2 050 entries
byte-identical; the zone byte-identical to the disc file.

## 11. Open points

- Field 0x08: purpose unknown (unused by the MP loader).
- Folder of the pak: same as the zone's by layout and by the shared lookup code; the
  language-folder globals are set at run time (section 5). The RPCS3 demo settles it.
- The slot numbers of part records against the loader's pak records (index = group x 4 +
  slot, 0x3caa8c) are not traced; the slot-to-file table stays the one established by sizes
  in textures.md 2.4 (slot 2 still unseen).
- Streamed cube and volume images: none are streamed in the examined zones; refused.
- DLC paks (`%s/%s/%s.pak` with `.edat`): not on this machine.
