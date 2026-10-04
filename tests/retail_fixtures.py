"""Pinned retail zone inputs for tests, resolved by sha1 - never the live zone.

The .env ``OPENT5_ZONES`` mp_nuked is the LIVE RPCS3 file, which is overwritten by
every device test (it is whatever build was last staged, e.g. o_blocks5), so a test
that reads it is non-deterministic and can assert against the wrong map. Geometry,
collision and path-node tests must pin their input to a known retail backup instead.

``retail_zone(name)`` returns the pinned backup only when it is present AND hashes to
the expected retail sha1. A present-but-wrong-hash file is a hard failure (the backup
was corrupted or replaced); an absent backup skips the test (the data is not on this
machine), matching how the zone-marked tests already behave.

Place the backups in tests/fixtures/ (gitignored, ~36MB each), named ``<zone>.ff.retail``.
The fallback path is the pristine d_pak copy in the hwtest tree.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: Retail sha1 of each zone we pin. mp_nuked is the PS3 BLES01031 retail fastfile.
RETAIL_SHA1 = {
    "mp_nuked": "6d5a4a0e5c031c0d8fcd555413730b014e9c0c5d",
}

#: Where a backup may live, in order: the committed-location fixtures dir, then the
#: pristine d_pak copy kept in the hwtest tree.
def _candidates(name: str) -> list[Path]:
    paths = [FIXTURES / f"{name}.ff.retail"]
    env_dir = os.environ.get("OPENT5_RETAIL_FIXTURES")
    if env_dir:
        paths.insert(0, Path(env_dir) / f"{name}.ff.retail")
    paths.append(Path("/mnt/c/Users/bolst/Desktop/opent5-hwtest/nuked/d_pak") / f"{name}.ff")
    return paths


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def retail_zone(name: str) -> Path:
    """The pinned retail backup for ``name``, or skip if no backup is present.

    Fails the test if a backup is present but does not match the expected retail sha1,
    so a corrupted or swapped-in file can never pass as retail.
    """
    want = RETAIL_SHA1.get(name)
    if want is None:
        pytest.skip(f"no pinned retail sha1 for {name}")
    seen: list[Path] = []
    for path in _candidates(name):
        if not path.is_file():
            continue
        seen.append(path)
        got = _sha1(path)
        if got == want:
            return path
        pytest.fail(
            f"retail fixture {path} sha1 {got[:12]} != expected {want[:12]} for {name}: "
            "the backup was changed or is not retail. Restore the pristine copy."
        )
    pytest.skip(
        f"no retail backup for {name} (looked in {', '.join(str(p) for p in _candidates(name))})"
    )
