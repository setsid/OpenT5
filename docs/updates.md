# Updates

OpenT5 checks the project's GitHub Releases for a newer version, downloads it, verifies
it, and offers to restart into it. This is the only network access the app makes;
everything else (opening, parsing, editing, saving, exporting) works offline and never
opens a connection. `tests/test_no_network.py` enforces that.

Code: `src/opent5/update/` (no Qt), `src/opent5/gui/updates.py` (menu items, the status
strip), `tools/keygen.py`, `tools/release.py`, `tools/update_fake_server.py`.

## What happens

On every start, a worker thread (the window is never blocked):

1. Removes `OpenT5.exe.old` if an earlier update left one.
2. If **Help > Check for Updates on Start** is ticked (the default), sends one request:
   `GET https://api.github.com/repos/{REPO}/releases?per_page=30`.
3. Ignores drafts, pre-releases (by GitHub's flag or a `-suffix` in the tag) and tags
   that are not `vMAJOR.MINOR.PATCH`. Picks the highest version above the running one
   (`opent5.__version__`, baked into the exe) that has not failed verification before.
   Equal or lower versions are never offered: no downgrades.
4. Downloads the release's `OpenT5.manifest.json` and `OpenT5.manifest.json.minisig`,
   and verifies the signature with the public key baked into the app.
5. Downloads the exe named in the manifest to `%TEMP%\OpenT5-update\`, checking its
   size and SHA-256 against the manifest as it arrives.
6. Shows a strip in the status bar: "Version X is ready. Restart to update.", with
   **What's New** (the release notes, shown as plain text: no links are opened and
   nothing remote is loaded) and **Restart to Update**. Nothing modal appears.

**Help > Check for Updates Now** (also in the command palette) runs the same check and
reports the result in the status bar. Unticking **Check for Updates on Start** means no
request is made at start. There is no telemetry.

### What is sent

Each request is a plain HTTPS GET with exactly two chosen headers,
`User-Agent: OpenT5/<version>` and `Accept`; Python adds `Host`,
`Accept-Encoding: identity` and `Connection: close`. No cookies, no credentials, no
machine or user identifiers. Certificates and host names are verified with the system's
trust store. Proxy settings are ignored, so a request goes straight to GitHub or not at
all. Redirects are followed by hand, at most three, and only to these hosts:

| Host | Why |
| --- | --- |
| `api.github.com` | the release list and asset downloads |
| `release-assets.githubusercontent.com` | where GitHub redirects asset downloads (observed 2026-10-03) |
| `objects.githubusercontent.com` | GitHub's earlier asset host, kept in case a redirect still uses it |

Any other host, any plain-HTTP URL or any other port is refused. Limits: 4 MiB for the
release list, 16 KiB for the manifest, 4 KiB for the signature, the manifest's size for
the exe (at most 1 GiB); 15 to 30 s per connect or read, and an overall bound per
transfer.

### When things go wrong

| Situation | What the app does |
| --- | --- |
| offline, DNS failure, timeout, TLS failure | logs it; tries again on the next start |
| HTTP 403 or 429 (rate limited) | logs it; tries again on the next start |
| HTTP 404 (no such repository, or a **private** repository) | logs it; tries again on the next start |
| a release without its manifest or signature (still uploading) | logs it; tries again on the next start |
| signature, version, size or SHA-256 mismatch | deletes the download, shows "The update to X failed verification and was discarded", and remembers X so it is never tried again |
| `REPO` or `PUBLIC_KEY` empty | no request at all; logs "update check disabled" |

None of these interrupts the person. Only a manual check reports "up to date" or the
error in the status bar. The remembered failures live in the settings
(`updates/failed_versions`); publishing a fixed release under a new version number is
the way past one.

GitHub answers 404 for a private repository when asked without credentials, so while
the repository is private every check ends quietly in "not-found". That is expected:
the check starts working the moment the repository (or just its releases) is public.
Unauthenticated API requests are limited to 60 an hour per IP address; one per start is
well inside that.

### Restart: swapping the exe

Windows will not overwrite a running exe but will rename one. **Restart to Update**:

1. closes the window first (asking about unsaved edits as usual; cancelling stops
   here);
2. copies the verified download next to the exe as `OpenT5.exe.new` and checks its
   size and SHA-256 again (the temp folder is not trusted);
3. renames the running `OpenT5.exe` to `OpenT5.exe.old`;
4. renames `OpenT5.exe.new` to `OpenT5.exe`;
5. starts the new `OpenT5.exe` and exits.

If any step fails, the steps already taken are undone in reverse order (the original
exe gets its own name back), the window reappears, and a message says what failed and
that the previous version is still in place. The next start deletes `OpenT5.exe.old`
(retrying for a few seconds while the old process finishes exiting). Run from source
(`npm run gui`), there is no exe to swap; the button says so and does nothing.

Checked on Windows 2026-10-03 with the built exe: writing to the running exe is refused
(PermissionError), renaming it works, the new exe starts and runs, the `.old` file
cannot be deleted while the old process runs and is deleted once it exits.

## Threat model, in plain terms

What the signature protects against:

- **A tampered download** (a hostile network, a compromised proxy or CDN, a corrupted
  file): the exe must match the SHA-256 in a manifest signed by the release key.
- **Someone who gains control of the GitHub account or repository** but not the
  release key: they can upload files, but cannot sign a manifest the app accepts.
- **Swapping in an old, genuine release** (to bring back a fixed bug): the app never
  installs a version lower than or equal to its own, and the signed manifest (and the
  signed trusted comment) name the version, which must equal the release tag.

What it does not protect against:

- **Theft of the private key together with its passphrase.** Whoever has both can sign
  anything. Keep it offline (below).
- **Withholding updates.** Someone who can block or alter the connection can stop the
  app learning about a new release. The app then just stays on its current version.
- **Malware already running as the user.** It could replace the exe directly; nothing
  an updater does helps against that.

### The test override

`OPENT5_UPDATE_TEST_URL` points the check at a local fake release server, so the tests
and the exe smoke test run with no internet. It is honoured only for exactly
`http://127.0.0.1:<port>[/path]`; any other value disables checking for that run. With
it set (and only then), the public key comes from `OPENT5_UPDATE_TEST_PUBKEY`, so tests
sign with a throwaway key and the release key is never needed.

Does this weaken anything? Using it needs two environment variables in the app's own
process and a server on the same machine's loopback address, which together already
mean code running as the user (who could simply replace the exe). On top of that,
an update found in test mode is **never installed**: Restart to Update refuses it. So
the override can make the app download and verify a file, never run one. Nothing
remote can turn it on.

## The dependency decision: PyNaCl (libsodium)

Ed25519 signing and verification go through PyNaCl 1.5.0, which wraps libsodium
(`opent5/update/ed25519.py` is a thin wrapper), pinned exactly with its two
dependencies, cffi 2.1.1 and pycparser 3.0. An earlier pure-Python implementation passed
every RFC 8032 test vector, but those vectors prove correct signing, not complete
rejection, and rejection is the verifier's only job. libsodium's
`crypto_sign_verify_detached` refuses:

- non-canonical S (S >= L), so a signature cannot be re-encoded into a second valid one;
- non-canonical public key encodings (y >= p);
- small-order public keys and small-order R, which otherwise let one signature verify
  against many messages.

`tests/test_update_crypto.py` checks each of these against this module: all eight
small-order encodings as the public key with crafted signatures, small-order R under a
real key, S + kL for several k, S at and above L, non-canonical key encodings, plus the
RFC 8032 vectors and single-bit corruptions. The tests were seen to fail with a verifier
that accepts everything.

Verification handles only public data. Signing runs only on the release machine, with
libsodium's constant-time implementation.

The file formats are minisign's (signature, public key, and the passphrase-encrypted
secret key: scrypt from `hashlib`, XOR, BLAKE2b-256 checksum). The scrypt parameters
match libsodium's for the same opslimit/memlimit (checked against PyNaCl), and
interoperability was checked against the minisign 0.11 binary on 2026-10-03:
`minisign -V` verifies OpenT5's signatures, OpenT5 verifies signatures minisign made
with an OpenT5 key, and OpenT5 decrypts a key `minisign -G` generated. So anyone can
check a release independently:

    minisign -V -P <PUBLIC_KEY> -m OpenT5.manifest.json
    sha256sum OpenT5.exe        # compare with the manifest

## Keys

    npm run keygen

asks for a passphrase twice (at least 12 characters) and writes:

- `~/.opent5-release/opent5.key`: the secret key, encrypted with the passphrase
  (scrypt with minisign's own limits, about 1 GiB of memory and a few seconds), file
  mode 600, folder mode 700. It refuses to overwrite an existing key or to write
  inside the repository.
- `~/.opent5-release/opent5.pub`: the public key.

It prints the line to paste into `src/opent5/update/__init__.py` (`PUBLIC_KEY = "..."`)
and the `.env` line `OPENT5_RELEASE_KEY=...` (`.env` is never committed;
`.env.example` has a placeholder). The secret key never enters the repository, CI or
any server; only `tools/release.py` on the release machine reads it.

**Back it up now**: copy `opent5.key` to two offline places kept apart (for example two
USB sticks, one off site), and keep the passphrase somewhere separate (a password
manager). The key file is useless without the passphrase, and the passphrase without
the file.

**If the key is lost** (or the passphrase forgotten), installed copies can never
verify another release: they will report each new one as failed verification. Every
user would have to download and install a new exe by hand, built with a new key. If
the key is **stolen**, make a new key, publish a release signed with the old key whose
only change is the new `PUBLIC_KEY` (so installs move to it), and say so publicly.

## Setting REPO

`src/opent5/update/__init__.py`:

    REPO = "owner/name"        # the GitHub repository whose Releases are checked
    PUBLIC_KEY = "RWQ..."      # from npm run keygen

Both empty (as shipped until they are set) means no request is ever made.

## Releasing

    npm run version -- 0.2.0                 # the one version source, plus pyproject/package.json
    # write docs/releases/0.2.0.md (the release notes), commit, push
    npm run release -- --dry-run             # everything except publishing
    npm run release                          # the same, then gh release create

`tools/release.py`:

1. refuses when the working tree is dirty, the tag `vX.Y.Z` exists (locally, on
   origin, or as a GitHub release), HEAD is not pushed, `REPO` or `PUBLIC_KEY` is
   empty, the notes file is missing, or the signing key does not match `PUBLIC_KEY`
   (a dry run only warns about an empty `REPO` or `PUBLIC_KEY` and missing notes, and
   skips the checks that need GitHub);
2. unlocks the signing key (asks for the passphrase) before anything slow;
3. copies the tracked source to `C:\o5\rel\src` and runs `packaging\build.bat` there
   (through `cmd.exe` from WSL), with its own PyInstaller work folder (`O5_WORK`), so it
   never touches another build in `C:\o5\src`;
4. runs `tools/exe_smoke.py` on the built exe: every view on mp_nuked and zombietron
   (from `OPENT5_ZONES`), and `--update` (below); both must pass;
5. writes `OpenT5.exe`, `OpenT5.manifest.json` and `OpenT5.manifest.json.minisig` to
   `dist/release-X.Y.Z/`, then verifies them with the app's own code and baked-in key;
6. runs `gh release create vX.Y.Z <the three files> --notes-file ... --target <HEAD>`
   (not in a dry run, which prints the command instead).

`--allow-dirty` and `--exe PATH` (reuse a built exe) are accepted only with `--dry-run`.

## Tests and the exe smoke test

`npm run test` includes, with no real network (about 5 s together):

- `test_update_crypto.py`: RFC 8032 vectors; malleability and small-order rejection;
  scrypt parameters; key encryption and wrong passphrase; signature valid, tampered
  file, tampered trusted comment, wrong key, wrong key id; legacy minisign signatures.
- `test_update_check.py`, against a fake release server on 127.0.0.1: ready; highest
  eligible picked; tampered exe, wrong size, tampered manifest, tampered comment, wrong
  key and manifest version mismatch all discarded; a remembered failure not retried;
  missing assets retried later; 404, 403, 429, 500; connection refused (offline); DNS
  failure; timeout; empty `REPO` and empty `PUBLIC_KEY` make no request; the override
  refuses everything but `http://127.0.0.1:<port>`; production URL rules; a redirect
  off the allowed host refused; headers sent.
- `test_update_swap.py`: swap and relaunch of a fake exe; failure injected at each step
  (staging, moving the running exe aside, moving the new one in, starting it) with
  rollback; staged copy checked; a failed rollback reported; `.old` and stray `.new`
  removed on next start, with retries.
- `test_update_gui.py`: menu and palette entries; the setting defaults on and, off,
  means no check; quiet failures; the ready strip and plain-text notes; failures
  remembered; manual check messages; restart from source and in test mode.
- `test_update_release.py`: keygen (mode 600, outside the repo, no overwrite, passphrase
  checks), the version bump, the release refusals, manifest signing.
- `test_no_network.py`: the static scan, the runtime check of open, parse, edit, save,
  export and the GUI's open and save with sockets made to fail, and the host list.

`tools/exe_smoke.py EXE --update` runs the built exe with `--update-selftest`, pointed
(through the override) at a fake release server on 127.0.0.1 offering a newer release
signed with a throwaway key and a tampered copy. The exe must find, download and verify
the first, reject the second leaving nothing behind, and leave itself unchanged.
