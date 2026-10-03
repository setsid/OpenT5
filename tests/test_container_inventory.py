"""The zone inventory, on synthetic zones."""

import random

from opent5.container import inventory
from opent5.container.fastfile import (
    ZONE_SIZE_PREFIX,
    split_content,
    with_declared_size,
    write_fastfile,
)
from test_container_zone import header_for, zone_content, zone_file


def test_a_good_zone_is_counted(tmp_path):
    body = bytearray(zone_content(random.Random(3), 30_000))
    # The XAssetList straight after the prefix: 7 strings, 42 assets.
    body[ZONE_SIZE_PREFIX : ZONE_SIZE_PREFIX + 16] = bytes.fromhex(
        "00000007ffffffff0000002affffffff"
    )
    path = tmp_path / "good.ff"
    path.write_bytes(zone_file(with_declared_size(bytes(body))))

    row = inventory.inspect(path, "here")

    assert row.opens and row.verbatim_identical and row.recompressed_identical
    assert (row.script_strings, row.assets) == (7, 42)
    assert row.declared_size_ok and row.padding_rule_ok and row.prefix_alone
    assert row.terminators == 4
    assert not row.signed
    assert row.error == ""


def test_a_truncated_zone_is_reported_as_an_incomplete_transfer(tmp_path):
    content = zone_content(random.Random(4), 30_000)
    data = zone_file(content)
    path = tmp_path / "cut.ff"
    path.write_bytes(data[: len(data) // 2])

    row = inventory.inspect(path, "here")

    assert not row.opens
    assert row.error.startswith("incomplete transfer?")


def test_the_writer_split_is_what_the_inventory_expects(tmp_path):
    content = zone_content(random.Random(5), 200_000)
    path = tmp_path / "split.ff"
    path.write_bytes(write_fastfile(header_for(), [(p, None) for p in split_content(content)]))

    row = inventory.inspect(path, "", recompress=False)

    assert row.odd_bodies == {}
    assert row.recompressed_identical is None


def test_render_and_summary(tmp_path):
    path = tmp_path / "z.ff"
    path.write_bytes(zone_file(zone_content(random.Random(6), 10_000)))

    rows = inventory.run([path], {"tmp": tmp_path}, jobs=1)

    assert rows[0].folder == "tmp"
    assert "| z.ff | tmp |" in inventory.render(rows)
    assert inventory.summary(rows)["recompressed_identical"] == 1
