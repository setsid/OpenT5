"""Update checks against the project's GitHub Releases: the app's ONLY network access.

On start (and on "Check for Updates Now") a worker thread asks GitHub for the release
list, picks the highest final release newer than this build, downloads its exe,
manifest and minisign signature, and verifies all three before offering a restart. See
docs/updates.md for the behaviour, the threat model and the release steps.

Only opent5.update.net opens connections, and only to ALLOWED_HOSTS (plus 127.0.0.1
when the test override is set); tests/test_no_network.py enforces both.
"""

from __future__ import annotations

#: "owner/name" of the GitHub repository whose releases are checked. Empty disables checking.
REPO = "setsid/OpenT5"

#: The release signing public key (the base64 line `npm run keygen` prints). Empty disables
#: checking, since nothing could be verified.
PUBLIC_KEY = "RWSpUEfdF7q9tnP+cq9yBCwr+U6AfYFPe+2tP7hNeaEN6EXoWr1ZgEdq"

#: The GitHub REST API, which lists releases and serves asset downloads.
API_HOST = "api.github.com"
#: Where the API redirects asset downloads to. GitHub moved release assets from
#: objects.githubusercontent.com to release-assets.githubusercontent.com in 2025
#: (observed 2026-10-03: a release download redirects to the latter); both are GitHub's.
ASSET_HOSTS = ("release-assets.githubusercontent.com", "objects.githubusercontent.com")
#: Every host the app may connect to, in production. Nothing else, ever.
ALLOWED_HOSTS = (API_HOST, *ASSET_HOSTS)
#: The modules allowed to use network-capable APIs (tests/test_no_network.py).
NETWORK_MODULES = ("opent5.update.net",)

#: Test override: a base URL replacing https://api.github.com. Honoured ONLY when it is
#: http://127.0.0.1:<port>[/path]; anything else disables checking for that run.
TEST_URL_ENV = "OPENT5_UPDATE_TEST_URL"
#: With the override set (and only then), the public key to verify against instead of
#: PUBLIC_KEY. Updates found in test mode are never installed.
TEST_KEY_ENV = "OPENT5_UPDATE_TEST_PUBKEY"

#: Release asset names. The manifest names the exe asset; these two are fixed.
MANIFEST_ASSET = "OpenT5.manifest.json"
SIGNATURE_ASSET = "OpenT5.manifest.json.minisig"

#: Size limits (bytes) for what is downloaded.
MAX_LIST_BYTES = 4 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024
MAX_SIGNATURE_BYTES = 4 * 1024
MAX_EXE_BYTES = 1024 * 1024 * 1024
