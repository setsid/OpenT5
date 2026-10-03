# The cross-zone search index

`opent5.index` searches across every configured zone at once, so a modder can ask
"which zone has this asset, this string, this script function" and get an answer in
milliseconds. The whole dump is parsed once into a cache on disk; every later search
reads the cache, never the zones.

## What it indexes

For each zone the build records:

- **every asset** in the asset list: type, name, file offset and size, plus the assets
  loaded inside others (an image inside a material, say), keyed `inline:<type>:<name>`;
- **searchable text**: a rawfile's source (scripts inflated), every stringtable cell,
  every localize value (keyed by its localize name), and the entity string of a
  `map_ents` asset or a clipMap.

Menus are listed by name; they carry no source text in a zone, so there is no menu body
to search.

## The cache

The cache is a single SQLite database (`index.sqlite3`) built with the standard-library
`sqlite3`. SQLite was chosen over a pickle or JSON per zone because it is one file, needs
no dependency, writes under a single transaction, and indexes the asset-name column so a
names lookup is quick without loading any text.

A zone's rows are keyed by its **absolute path**, and reused while the file's **mtime and
size** are both unchanged. Change a zone (or drop a new one in) and only that zone is
re-parsed; remove one from the dump and its rows are pruned. A schema-version row lets an
old cache be wiped and rebuilt automatically.

### Where the cache lives

By default under the per-user cache directory, never in the repository and never in a
game folder:

1. `$OPENT5_CACHE_DIR` if set;
2. otherwise `$XDG_CACHE_HOME/opent5`, or `~/.cache/opent5`.

`cache_dir` on the API and the `Store.open(cache_dir)` argument override it.

## The build

`build(zones, store, rebuild=False, jobs=None, progress=None)` brings the cache up to
date. It reuses unchanged zones, re-parses the rest in a process pool
(`concurrent.futures.ProcessPoolExecutor`), and prunes zones that left the dump. The
worker opens and parses one zone and never raises: a zone that will not open or parse
comes back with its `error` recorded and is skipped, so one bad zone cannot stop the
build. `progress(done, total, path, status)` is called once per parsed zone (status
`indexed` or `failed`). The job count defaults to `min(cpu, 6)` and is overridable with
`jobs=` or `$OPENT5_INDEX_JOBS`.

## Searching

```python
from opent5 import env
from opent5.index import Store, Index, build

with Store.open() as store:
    build(env.all_zones(), store)
    result = Index(store).search("Nuketown")
    for hit in result.hits:
        print(hit.zone, hit.type_name, hit.asset_name, hit.where, hit.snippet)
```

`Index.search(term, kinds=None, regex=False, case=False, names_only=False,
type_name=None, zone=None, limit=200)`:

- `names_only=True` is the fast path over asset names alone;
- `kinds` keeps only some match kinds (`name`, `rawfile`, `cell`, `localize`,
  `entities`); `type_name` keeps one asset type; `zone` is a glob over zone names;
- `regex` treats the term as a regular expression; `case` makes it case-sensitive;
- results are ranked (an exact name match first, then a prefix, then substrings, then
  text hits) and capped at `limit`; `SearchResult.total` is the count before the cap.

A `Hit` carries the zone, asset type and name, the asset key (`asset_ref`), the match
kind, and where it matched: `line` for a rawfile or entity line, `row`/`column` for a
stringtable cell, the value for a localize entry.

Each full-text search scans the stored text, which is small next to the zones because
only the text types are kept. A plain, case-insensitive search pre-filters in SQL with
`LIKE` so only the blocks that could match are scanned; a regex or case-sensitive search
scans in Python.

The module-level `search(term, zones=None, cache_dir=None, ensure=True, rebuild=False,
**options)` is a convenience that opens the cache, optionally updates it, and searches in
one call.

## Command line

```sh
opent5 search TERM [--names] [--type T] [--kind K] [--regex] [--case] \
                   [--zone GLOB] [--limit N] [--rebuild] [--json]
opent5 index build [--rebuild] [--json]
opent5 index status [--json]
```

`search` updates the index first (changed zones only, unless `--rebuild`) and then
searches, so the first run after a change pays for that zone and the rest is instant.

## Measured on the stand-in dump

178 zones (disc, title update, DLC 1 to 5, English and French):

| | |
|---|---|
| Cold build (6 jobs) | about 47 s, 0 zones failed |
| Warm build (all reused) | about 0.34 s (roughly 140x faster) |
| Cache size | about 466 MB (95 MB of searchable text) |
| Assets indexed | 125,327 top-level, 375,444 inline |
| Query, warm | 55 to 230 ms typical (a very broad term that matches hundreds of thousands of lines takes about 1 s) |

Example hits: `Nuketown` finds `mp_nuked` (and its localize entries); `level.gameEnded`
returns the script files and line numbers that read it; the name `mp_nuked` resolves to
every zone that holds an asset by that name.
