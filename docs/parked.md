# Parked defects

Issues noticed but deliberately not fixed on the branch that found them, so one concern stays
per branch. Each line: what, where, and why it is parked.

- `tests/test_convert_entities.py::test_compiled_box_matches_the_generator` fails on v0.3.0.
  The compiled box map carries entities the generator does not emit (for example a
  `mp_supplydrop_hq` script_model), so the comparison differs. Pre-existing, in the box-map
  entity path (`opent5.convert.entities` / the box-map generator), unrelated to the editor or
  the exe. Investigate separately; it does not affect the 0.3.0 editor release.
- Lint debt on v0.3.0: `ruff check src tests tools` reports pre-existing errors (the bulk in
  `tools/`), plus two E501 lines in `mapwriter.path_nodes` / `preview`. Not introduced by the
  editor work. Worth a separate formatting/lint pass.
- `tests/test_update_release.py::test_release_refuses_dirty_tree_and_existing_tag` fails on
  v0.4.0. `tools/release.preflight` runs `check_versions` before the dirty check, and the
  branch's version files are still pinned to 0.3.0, so it raises the version-mismatch refusal
  instead of the "dirty" one the test expects. Pre-existing, in the release tooling, unrelated
  to the editor. It clears once the version is bumped for the 0.4.0 release.
