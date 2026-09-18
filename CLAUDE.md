# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Three stdlib-only Python 3.7+ CLIs that query the Salesforce Metadata API via the `sf` CLI:

- **`verify_metadata.py`** — checks whether a list of metadata **types** has any members (data)
  in one or more orgs.
- **`report_metadata.py`** — a *structural* report: for each type, its file extension, folder
  (DX directory name), and whether members are a `BUNDLE`, live `IN-FOLDER` (foldered, nestable),
  or are `FLAT`. Reads the org's describeMetadata once (`sf org list metadata-types`) — not per type.
- **`retrieve_first_member.py`** — takes a **single** type and one org, finds the first member of
  that type, and retrieves it (metadata format) into a local folder (default `components/`).

## Running it

```bash
# single org, types from a file
python3 verify_metadata.py --types types.txt --org my-org

# inline comma-separated types
python3 verify_metadata.py --types ContractType,DocumentGenerationSetting --org my-org

# multiple orgs -> comparison matrix, optionally to CSV
python3 verify_metadata.py --types types.txt --org my-org,my-other-org --csv result.csv

# structural report (extension / folder / bundle vs. in-folder vs. flat)
python3 report_metadata.py --types types.txt --org my-org --csv report.csv --json report.json

# retrieve the first member of one type into ./components
python3 retrieve_first_member.py --type ApexClass --org my-org
```

`--workers N` controls parallel `sf` lookups per org (default 5); lower it on busy orgs hitting API limits.
All three scripts accept `--api-version` to pin the Metadata API version; `verify_metadata.py` defaults
it to `65.0`, while `report_metadata.py` and `retrieve_first_member.py` leave it unset (falling back to
the `sf`/org default) unless passed explicitly.

There is no build step, no dependencies, and no test suite. Verify changes by running the script against
an authorized org.

## Auth model (important)

The script **never handles credentials**. It shells out to the Salesforce CLI (`sf`), which must already
have each org authorized by alias:

```bash
sf org login web --alias my-org   # one-time per org
sf org list                       # see what's authorized
```

`--org` values are `sf` aliases/usernames. If an org isn't authorized the script dies with the exact
`sf org login web` command to run.

## Architecture

All three scripts are standalone (no shared import) and share the same `run_sf` / `die` / `load_types`
helpers, copied into each so any one can run on its own.

`report_metadata.py` calls `describe_metadata()` once to build a `{xmlName: describeObject}` catalog,
then `classify()` maps each requested type to a row. Classification, derived from the describe object:
`BUNDLE` = in `KNOWN_BUNDLES` or (no `suffix`, not `inFolder`, and has a real `directoryName`);
`IN-FOLDER` = `inFolder` true; else `FLAT`. Types absent from the org's describe (feature-gated / misspelled) become `UNKNOWN` rows,
never a fatal error. The `dx_supported` column is a describe-presence proxy for DX/source-format
support (`yes` when the type is in the describe, `no` when `UNKNOWN`) — no extra call.
Renderers: `print_table` / `write_csv` / `write_json`.

`verify_metadata.py`, structured as a pipeline of small pure-ish functions:

- `run_sf(args)` — the single choke point for all `sf` calls. Always appends `--json`, parses JSON even
  on non-zero exit (sf emits JSON on most errors), and raises `RuntimeError` with a one-line message.
  Any new Salesforce interaction should go through this.
- `count_members(org, mtype, api_version)` — runs `sf org list metadata --metadata-type <T>
  --api-version <V>`; returns `(type, count | None, note)` where `None` means that one type's listing
  errored (bad type name, etc.). A failed type is captured as a per-type ERROR, not a fatal exit.
  `--api-version` defaults to `65.0` (`--api-version` flag) rather than the `sf`/org default, so results
  are stable across environments with different default API versions.
- `scan_org` — fans `count_members` across a `ThreadPoolExecutor` (the `--workers` pool).
- `verdict(count)` maps count -> `"Has data" | "No data" | "ERROR"`; `print_single`, `print_matrix`,
  `write_csv` are the three renderers (single org = table, multiple orgs = matrix).
- `die(msg)` = stderr + `exit(1)`, reserved for fatal setup errors (missing `sf`, unauthorized org,
  empty type list). Per-type failures never call `die`.

`retrieve_first_member.py` is single-type, single-org (both flags reject comma lists). `first_member()`
runs `sf org list metadata --metadata-type <T>`, sorts the `fullName`s for a stable "first", and dies if
the type has zero members. `retrieve_member()` runs `sf project retrieve start --metadata <T>:<member>
--target-metadata-dir <dir>` — deliberately *not* the source-format `--output-dir`, which requires a DX
project. That command drops a single `unpackaged.zip`; the function extracts it with stdlib `zipfile`,
strips the archive's leading `unpackaged/` prefix so files land directly under the folder
(e.g. `components/classes/Foo.cls`), then removes the zip. Flags: `--type`, `--org`, `--member`
(override the auto-picked first), `--output-dir` (default `components`), `--api-version`.
`sf` can report status 0 / success true for a retrieve even when the Metadata API refused to
serialize the component — that only shows up in `result.messages[].problem`, so `retrieve_member()`
checks it explicitly and also treats a zip containing only `package.xml` as a failure, rather than
reporting a hollow success.

Data inputs (edit freely, they are just sample inputs):
- `types.txt` — one metadata type per line; blank lines and `#` comments ignored.
- `orgnames.txt` — reference list of org aliases (not read by the script; for the user's convenience).
- `report.csv` — a sample `report_metadata.py --csv` output committed for reference; regenerated by re-running the script, not hand-edited.
- `extension.csv` — currently empty; not actively used.

## Known limits

Folder-based types (Report, Dashboard, Document, EmailTemplate) list at the folder level via this API.
The types shipped in `types.txt` (config / Industries types) work directly.
