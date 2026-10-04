# Device (hardware) test checklist

Every build sent to the device goes through `tools/stage_build.py`, which sets a fresh mtime,
runs the offline staged-map gate, and prints a filled-in copy of this checklist. This file is
the template and the why.

## Why this exists

RPCS3 caches the decrypted disc in `dev_hdd1\caches\BLES01031_BLES01031\cache.dat` (~1.68 GB).
`Copy-Item` preserves a file's mtime, so staging an older-dated build does **not** invalidate
the cache and RPCS3 loads the **stale** map. This produced two false conclusions before it was
found: a correct o_blocks5 rendered as Nuketown, and a working clip read as "no collision".
A stale cache is indistinguishable from the build under test, so every result taken without
clearing it is void.

## The protocol (every device build)

1. **Clear the cache.** Close RPCS3. Delete or rename `dev_hdd1\caches\BLES01031_BLES01031`.
2. **Copy** the staged `mp_nuked.ff` to the load path. (`stage_build.py` has already set a
   fresh mtime, so the copy carries a current timestamp.)
3. **Verify the file.** Confirm the file at the load path has the sha1 printed by
   `stage_build.py` and a current timestamp.
4. **Check the marker.** Launch and look for the named **visible in-map marker**. If it is
   absent, you are looking at a stale load - go back to step 1. Every test build must carry a
   marker for exactly this reason; `stage_build.py` refuses to stage one without.
5. **Run the test** at the named landmark (described in words, not raw coordinates).

## Offline guards (before it ever reaches the device)

- `tools/verify_staged_map.py STAGED.ff --base BASE.ff` - asserts the staged zone is the
  intended map (GfxWorld + clipMap signature), not the base or a wrong/stale file.
- The converter fails loud and never writes a zone it cannot reparse exactly; the emulated
  loader oracle (`tools/convert_map.py`) must be clean; the 178-zone round-trip must pass.

## Reporting a result

State the build label and sha1, confirm the cache was cleared and the marker was visible
(so the result is trustworthy), then the outcome at the landmark. A result that does not
confirm cache-cleared + marker-seen is not reliable and should not drive a conclusion.
