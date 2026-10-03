# Resizing an inline string: pointer remap, verified with the game's own loader

Status: CONFIRMED for one edit (hardware test zone (b)): code_post_gfx_mp, localize asset 4162,
value "PLAYER MATCH" -> "OPENT5 REMAP OK" (+3 bytes). The algorithm is general for any edit that
changes the length of data loaded into one block; only this instance has been run end to end.

Tool: `tools/remap_oracle.py` (oracle tool, not product code). Fixture for the product remapper:
`tests/fixtures/remap_code_post_gfx_mp.json`. Output: `out/hwtest/b_resized/code_post_gfx_mp.ff`.

## 1. The edit

Disc zone `code_post_gfx_mp.ff` (sha1 d658721a79bc541f51d2fd2ff28c7098ce703872, content length
0x9bb103). Localize asset 4162 at content offset 0x34357f:

```
0034357f: ffffffff ffffffff                  value -1, name -1 (both inline)
00343587: 504c41594552204d4154434800         "PLAYER MATCH\0"   -> "OPENT5 REMAP OK\0"
00343594: 4d5055495f504c415945525f4d415443485f4341505300   "MPUI_PLAYER_MATCH_CAPS\0"
```

The 8-byte LocalizeEntry header goes to TEMP (block 0, rewound after every asset); both strings
go to VIRTUAL (block 4) with no alignment (structs-content.md section 5). The value string sits at
VIRTUAL offset 0x9b2fa (635642). So the edit moves file bytes after 0x343594 by +3 and VIRTUAL
positions after 0x9b307 by a shift that the next alignments may change.

## 2. Algorithm

1. Trace the original zone with the loader itself. The scratch PowerPC interpreter (`.oracle/r2b`
   emu.py / ppc.py) runs t5mp.elf's loader over the decompressed content (only the file reader
   0x233558 and string reader 0x2335d0 are replaced). Hooks record:
   - every VIRTUAL position change: DB_AllocStreamPos 0x26aca0 (mask), DB_IncStreamPos 0x26acc0
     (size, plus the file offset of the bytes when the advance follows a read into that position),
     DB_InsertPointer 0x26acd8 (align 4, reserve 4, no file bytes). These are the only writers of
     the position global (281<<16)+10420 in the loader (disassembly: stores at 0x26aaec..0x26ad90);
   - every offset-pointer conversion: DB_ConvertOffsetToPointer 0x26add8 (`*p = base + off`) and
     DB_ConvertOffsetToAlias 0x26ada0 (`*p = *(base + off)`; the extra `lwz r0,0(r9)` at 0x26adcc
     tells them apart). The scratch emu.py labels these two the other way round; the oracle uses
     the addresses. These two are the only readers of the block table (281<<16)+10416 in the
     loader, so no conversion is inlined elsewhere;
   - every read (destination address, size, file offset). A converted field's address is mapped to
     its file offset at the moment of conversion, through the most recent read covering it (TEMP
     memory is reused, so resolving after the run gives wrong answers).
2. Replay. The VIRTUAL op list is replayed from 0 with the same masks; every logged position is
   checked (70838 ops, no drift) and the end equals header blockSize[4] = 0x276c61. It is replayed
   again with the edited string's advance 3 larger. Each advance is one allocation
   (old start, old length, new start, new length).
3. Map. A pointer value v with block (v-1)>>29 = 4 and offset t is mapped through the allocation
   containing t, keeping t's offset inside it: new = ((4<<29) | (new_start + t - old_start)) + 1.
   A target in padding would be an error (none occurred: gap_hits 0). Pointers into other blocks
   are unchanged (their positions do not depend on VIRTUAL).
4. Write. Rewrite every changed pointer field in place, then replace the string (insertion after
   the rewrites, so earlier file offsets stay valid), set blockSize[4] to the new end, and save
   with `Zone.save` (which derives the u32 at content offset 0 = len - 36, re-chunks the middle,
   rebuilds the nonce chain; the RSA signature at 0x3c is carried unchanged). TEMP's size is
   unchanged because the header did not grow; blocks 2/3 are empty in this zone.

## 3. Evidence

| Item | Value |
|---|---|
| Conversions in the original | 47476, all to block 4 (VIRTUAL); 45620 to-pointer, 1856 to-alias |
| Pointers whose value changed | 1599, all DB_ConvertOffsetToPointer; fields: 226 in TEMP (localize headers), 1373 in VIRTUAL |
| First / last remapped field | file 0x343b04 `8009b6f3` -> `8009b6f6` (asset 4191, localize); file 0x99efed `800bbf04` -> `800bbf07` (asset 8556, emblemset) |
| Alias slots (InsertPointer) | 5, all before the edit; no alias pointer needed remapping |
| VIRTUAL relayout | [0, 0x9b307): +0; [0x9b307, 0xc3d1c): +3; [0xc3d1c, end): +0 |
| Alignments met while shifted | 7555 with mask 0 (strings; shift stays 3); 1 with mask 3 at old 0xc3d19 (shift 3 -> 0) |
| blockSize[4] | 0x276c61 before and after (the shift is absorbed, see below) |
| Content length | 0x9bb103 -> 0x9bb106; size field 0x009bb0df -> 0x009bb0e2 |

Alignment case. The shift is absorbed by the first 4-aligned VIRTUAL allocation after the edit:
a 16-byte sub-struct of material asset 8228 (name "code_warning_file", inline at file 0x373f1c
`636f64655f7761726e696e675f66696c6500`; the 16 bytes at 0x373f2e `a0ab10416370ea0000000000ffffffff`).
Originally the name ends at VIRTUAL 0xc3d19 and the align pads 3 bytes to 0xc3d1c; after the edit
the name ends at 0xc3d1c, already aligned, so padding drops to 0 and everything after is where it
was. Between the edit and that point VIRTUAL holds only localize strings (mask 0). This edit
therefore exercises a moved region of 166 KB and 1599 pointers, a padding change, but not a
change of blockSize[4]. An edit whose delta is a multiple of 4 would keep the shift through
mask-3 alignments and reach the end of the block (INFERRED from the replay rule; not run).

Shared string. One other pointer targets the edited string: localize asset 6187
(MENU_PLAYER_MATCH_CAPS) at file 0x35ad04 has value `8009b2fb` (VIRTUAL 0x9b2fa). The string
start does not move, so the pointer is unchanged, and that entry also reads "OPENT5 REMAP OK".

## 4. Validation (offline, emulated loader over the edited content)

| Check | Result |
|---|---|
| Bytes consumed | 0x9bb106 = whole content |
| Final block positions vs new header | [0,0,0,0,0x276c61,0,0x6f8e00] = header (TEMP rewound to 0; its size 0xa98 unchanged) |
| Conversions | 47476, same order and function; every value equals the mapped original (0 mismatches) |
| VIRTUAL image | each allocation moved back and each pointer word mapped back: identical to the original image except the grown string (1373 pointer words mapped back, 0 unexplained bytes) |
| PHYSICAL (6) and TEMP (0) images | byte-identical to the original |
| Pointed-to content, all 47476 pointers | 47475 raw-identical 16-byte windows; the one difference is the shared string pointer above; all 47476 equal after mapping and unmap to the original target |
| Localize asset 4162 | loads "OPENT5 REMAP OK" |
| Container | `Zone.open(out)` content == edited content; `verify()` passes (4 terminators, size field, chunk 0 = 36-byte prefix); header bytes 0..0x13c identical to the original (signature carried) |

Output: `out/hwtest/b_resized/code_post_gfx_mp.ff`, 3097952 bytes,
sha1 43ac232529ae3f337e73a16987c163f1faa1e88d (210 chunks, 71 carried verbatim). Two runs gave the
same sha1.

## 5. Fixture

`tests/fixtures/remap_code_post_gfx_mp.json`, numbers only: the edit (string file offset, insert
offset, length), old and new VIRTUAL sizes and content lengths, `relayout_runs` ([old VIRTUAL
offset, delta] breakpoints), `pointers` ([old file offset of field, old value, new value] for
every changed pointer, 1599 rows), conversion counts per block, the alignment change, and the
output sha1 as five u32. A product remapper given the same edit should produce the same pointer
list and a byte-identical .ff.

## 6. Limits

- Only pointers the loader converts are seen. That is every offset pointer by construction (the
  loader must convert a pointer for it to be valid), but only on code paths this zone exercises.
- Edits that grow TEMP data (an asset header) or data in PHYSICAL / deferred blocks need the same
  replay for those blocks; the tool handles VIRTUAL only.
- Hardware/RPCS3 behaviour is untested here; the signature is void, so the zone loads only on a
  client with the signature check patched out.
