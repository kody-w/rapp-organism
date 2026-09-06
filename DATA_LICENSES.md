# Component licenses are not flattened

The root MIT license covers newly authored catalog tooling, interface and
documentation only. A reference in this catalog does not grant rights in the
referenced work. Public visibility is not the same as permission to redistribute.

`repos.license_spdx` preserves the capture's original repository-level license
detection (`MIT`, `Apache-2.0`, `BSD-2-Clause`, `BSD-3-Clause`, `NOASSERTION`, or
unknown in the initial scope). This is not a file-level license determination.
Nested projects, bundled dependencies, assets and notices may have different
terms. Inspect the license and notices at the **carried commit**, not just the
current default branch. Empty/unknown/`NOASSERTION` values grant no implied license.

Hydrated dependency license fields remain unknown where the source did not
establish them. Current public API license observations are stored separately
from captured license detection.

The tracked projection contains selected metadata—repository identities, relative
paths, Git modes, content hashes, sizes, counts and bounded measurements—not source
bodies, excerpts, full diagnostics or licenses copied out of source files. Preserve
upstream copyright and license notices when retrieving actual components.

The exact full carrier is **WITHHELD** after disclosure and rights review;
`clear_for_exact_publication` is false. Its known hash and successful local
preservation do not clear that gate. Neither raw nor encrypted-full publication
is offered. Complete public metadata is not availability or redistribution
permission for the raw/private payload. The original is retained locally by its
custodian. Public findings expose no sensitive locators or raw diagnostics.

Simon Willison's git-scraping article and Datasette Lite are referenced, not
vendored. Datasette Lite and its browser runtime retain their upstream licenses.
