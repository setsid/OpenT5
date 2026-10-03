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

## 7. Variant b2: an edit that changes blockSize[4]

Same entry, value "OPENT5 REMAP OK" padded with trailing spaces to 140 characters: +0x80 bytes
(`tools/remap_oracle.py OUT FIXTURE 140`). The largest VIRTUAL alignment met after the edit is
16 (masks seen after VIRTUAL 0x9b2fa: 0 x13481, 1 x37, 3 x9804, 7 x1749, 15 x19), so a multiple
of 0x80 cannot be absorbed by padding.

| Item | Value |
|---|---|
| Relayout | [0, 0x9b307): +0; [0x9b307, end): +0x80; no alignment changed the shift (0 changes) |
| Pointers remapped | 20680 of 47476 (fields: 805 in TEMP, 19875 in VIRTUAL), all to-pointer |
| Header fields changed | size at content 0x0: 0x009bb0df -> 0x009bb15f; blockSize[4] at 0x18: 0x00276c61 -> 0x00276ce1. blockSize[0] (TEMP, 0xa98) and blockSize[6] (PHYSICAL, 0x6f8e00) unchanged |
| Content length | 0x9bb103 -> 0x9bb183 |

Validation (same checks as section 4): the loader consumes all 0x9bb183 bytes; final positions
[0,0,0,0,0x276ce1,0,0x6f8e00] equal the new header; 47476 conversions, 0 value mismatches; VIRTUAL
image identical after moving allocations back (34835 pointer words mapped back: the 19875 file
pointers plus pointers the loader writes for inline data, which also move; 0 unexplained bytes);
TEMP identical after mapping 14 such words; PHYSICAL byte-identical; all 47476 pointed-to windows
equal after mapping (47380 raw-identical, the rest contain a moved pointer or the edited
string); asset 4162 loads "OPENT5 REMAP OK" plus 125 spaces. `Zone.open` round trip and `verify()`
pass. Output `out/hwtest/b2_blocksize/code_post_gfx_mp.ff`, sha1
7e00990c394ee225716b90ef41419245d1332155. Fixture: `tests/fixtures/remap_code_post_gfx_mp_b2.json`.

Other dependencies on absolute VIRTUAL positions. Checked:
- No stored size or count spans the localize region: each LocalizeEntry is two string pointers
  (structs-content.md section 5) and owns nothing else.
- The old VIRTUAL size 0x00276c61 occurs in the content only at 0x18 (the header). The old
  content size 0x009bb0df occurs only at 0x0. The full length 0x009bb103 does not occur.
- Everything the loader leaves in memory, once mapped back, equals the original. A stored
  absolute position would show up as an unexplained byte, and there were none.
- Alignment relative to the block base is preserved modulo 0x80, which covers every mask the
  loader applies after the edit (largest 16, vertex shader programs).
- Not covered: what the post-load functions the harness stubs out do at runtime (asset
  registration, script string remap, RSX offset conversion). They act on pointers the loader
  has already resolved, so they should not need block positions. INFERRED; the hardware test
  will confirm it.

## 8. Product remap (`src/opent5/xfile/remap.py`)

The product remap does what the oracle does, using only the parser's event log (no
emulator). The parse logs every READ, STRING, DEFER, TAIL, PUSH, POP, ALLOC, INSERT and
POINTER event (`src/opent5/xfile/events.py`). In code_post_gfx_mp it logs 45620 OFFSET and
1856 ALIAS_REF pointers. The emulated loader converts the same counts: 45620 to-pointer and
1856 to-alias (section 3).

Algorithm (`remap(xfile, content, edits)`; an edit is `Edit(offset, old_length, data)`, or
`string_edit` for a NUL-terminated string):

1. Locate each edit's READ / STRING (or TAIL, same length only) by file offset. An edit
   may not cross a read boundary, a string must keep exactly one NUL, and an edit may not
   overlap a rewritten pointer field.
2. Replay the log with the loader's rules: push and pop (TEMP rewinds on pop), alloc with
   its mask, reads, strings, deferred reservations, and InsertPointer (align 4, +4 in
   VIRTUAL). The first replay checks every logged position. The second uses the edited
   sizes. Each event that takes block memory becomes an allocation: block, logged start
   and size, new start and size.
3. Map every OFFSET / ALIAS_REF target through the allocation that held it when the
   pointer was read, keeping the offset inside the allocation:
   - Non-TEMP blocks: a bisect, since those blocks only grow.
   - TEMP: the latest TEMP allocation before the pointer event.
   - A target one byte past an allocation keeps its distance. A target in padding raises
     `RemapError`.
4. Write:
   - changed pointer fields, at their original file offsets;
   - the size fields declared by `SIZE_FIELDS[type]` (the extension point; `@size_rule`
     registers one; rawfile `len` is the built-in example);
   - the splices;
   - each `blockSize[b]`, moved by the change in that block's final position (for TEMP, by
     the change in its high-water mark);
   - `size = len - 36`.
5. `compare(before, after)` is the parse-level check. The reparse must be exact, every
   pointer must have the same kind, and every OFFSET / ALIAS_REF must have the same
   logical target: the same allocation (by order in its block) and the same offset
   inside it. `remap(..., check=True)` runs it.

Evidence (`tests/test_xfile_remap.py`, 25 tests, about 40 s with the zones):

- (b) and (b2) are reproduced byte for byte from the retail code_post_gfx_mp.ff
  (sha1 43ac2325... and 7e00990c...). The 1599 and 20680 pointer rewrites and the VIRTUAL
  relayout runs equal the oracle fixtures.
- Synthetic zones cover string grow and shrink across 4- and 16-byte alignments with
  pointers to moved data, a rawfile buffer resize (`len` 11 -> 18), and rejected edits.
- Random single resizes (deltas from -5, -2, -1, 1, 3, 4, 5, 8, 13, 16, 17, 127, 128, 129,
  300) of localize values, rawfile names and stringtable cells in code_post_gfx_mp and
  patch_mp: every reparse is exact with the same logical targets.
- Six edits at once per zone, mixing the three types: the same checks pass.
- A sample was also checked with the emulated loader (`tools/remap_oracle.py`,
  `check_remap`). The edits were the six mixed resizes (-5, +3, +17, +129, +4, -1),
  applied once to patch_mp and once to code_post_gfx_mp:
  - patch_mp: 29355 pointers rewritten. The VIRTUAL shift goes -16, -13, -12, +5, +4,
    +133, +137, +144 as the alignments change it. blockSize[4] goes 0x2c46d1 -> 0x2c4761.
  - code_post_gfx_mp: 21918 pointers rewritten. blockSize[4] goes 0x276c61 -> 0x276cf1.
  - In both, the loader read the whole file, the final block positions equal the new
    header, and every converted pointer equals the emulator-derived mapping
    (0 mismatches). The VIRTUAL images are identical after mapping back, with
    0 unexplained bytes.

Limits:

- Edits change sizes only. An edit that changes counts, markers or the number of
  loader reads needs the type's write side to re-emit the asset; that is not a byte
  splice.
- Deferred (LARGE_RUNTIME / PHYSICAL_RUNTIME) data may only change in place.

## 9. Results in RPCS3 (reported by the user, 2026-10-03)

Run in RPCS3 with the signature-patched multiplayer client, each file replacing the disc's
`USRDIR/english/code_post_gfx_mp.ff` in turn (retail sha1 `d658721a79bc541f51d2fd2ff28c7098ce703872`
restored afterwards).

| Test | File sha1 | Change | Result |
|---|---|---|---|
| a | `b728d86d0a4caadcef512612f6ed8eabec272331` | same-size value, "OPENT5 EDIT1" | button shows OPENT5 EDIT1; rest of menu normal; no crash |
| b | `43ac232529ae3f337e73a16987c163f1faa1e88d` | +3 bytes, 1,599 pointers remapped | button shows OPENT5 REMAP OK; rest of menu normal; no crash |
| b2 | `7e00990c394ee225716b90ef41419245d1332155` | +0x80 bytes, 20,680 pointers remapped, VIRTUAL block size grown | button shows OPENT5 REMAP OK; rest of menu normal; no crash |

This settles the item marked INFERRED in section 7: the post-load work the emulator skips (asset
registration, script-string remap, RSX offset conversion) accepts remapped zones, including one
whose header block size changed. The product remap (section 8) reproduces b and b2 byte for byte.
Console hardware was not tested.
