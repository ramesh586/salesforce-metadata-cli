# Metadata "has data" verifier

Checks whether a list of Salesforce metadata **types** has any members (data)
in one or more orgs, fetched directly from the org's Metadata API in seconds.

Only inputs you provide each run: **the type list** and **the org name(s)**.
Everything else (repo, branch, etc.) is irrelevant here — those only decide where
a *commit* goes, not whether the org has members of a type.

## One-time setup

1. Install the Salesforce CLI (`sf`): https://developer.salesforce.com/tools/salesforcecli
2. Authorize each org once, giving it an alias:

   ```bash
   sf org login web --alias my-org
   sf org login web --alias uat-sandbox
   sf org login web --alias prod
   ```

   Confirm what's authorized any time with: `sf org list`

No Python packages needed — standard library only.

## Usage

```bash
# types from a file, single org
python3 verify_metadata.py --types types.txt --org my-org

# inline comma-separated types
python3 verify_metadata.py --types ContractType,DocumentGenerationSetting --org my-org

# also save a CSV
python3 verify_metadata.py --types types.txt --org my-org --csv result.csv

# pin a specific Metadata API version (default 65.0)
python3 verify_metadata.py --types types.txt --org my-org --api-version 61.0
```

## Different orgs

The org is just the `--org` value, so switching orgs = changing the alias:

```bash
python3 verify_metadata.py --types types.txt --org uat-sandbox
```

Pass several aliases (comma-separated) to compare them side by side. The output
becomes a matrix of types x orgs, so you can see which org has data for which type:

```bash
python3 verify_metadata.py --types types.txt --org my-org,uat-sandbox,prod
```

Each org is checked for authorization first; if one isn't logged in, you get a
clear message telling you the exact `sf org login web` command to run. The script
never sees or stores credentials — `sf` holds the auth.

## Structural report (`report_metadata.py`)

A companion script that answers a different question: not *"does the org have data?"* but
*"how is each type structured?"* For every type it reports the **file extension**, the **folder**
(the DX directory name), and whether a member is a **BUNDLE** (a directory of files, e.g. LWC/Aura),
lives **IN-FOLDER** (foldered types like Report/Dashboard, which can nest folder-inside-folder), or
is a **FLAT** single file.

It reads the org's describeMetadata **once** (`sf org list metadata-types`) — one cheap call, not one
per type — so it works for arbitrary types, including Industries types. Same auth model: it only needs
one org you've already authorized.

```bash
# types from a file, structure read from an authorized org
python3 report_metadata.py --types types.txt --org my-org

# inline types, also write CSV and JSON
python3 report_metadata.py --types Report,LightningComponentBundle,ContractType \
    --org my-org --csv report.csv --json report.json

# pin a specific Metadata API version
python3 report_metadata.py --types types.txt --org my-org --api-version 61.0
```

Sample output:

```
Metadata type             Extension   Folder      Structure  DX supported  Bundle?  In-folder?
----------------------------------------------------------------------------------------------
Report                    .report     reports     IN-FOLDER  yes           no       yes
LightningComponentBundle  (none)      lwc         BUNDLE     yes           yes      no
ApexClass                 .cls        classes     FLAT       yes           no       no
```

The **DX supported** column is `yes` when the type appears in the org's describe (so it can be
retrieved in DX source format) and `no` for `UNKNOWN` types. It's derived from the same describe
call — no extra request.

Options:

- `--types` File path (one type per line) or comma-separated list. (required)
- `--org` A single authorized org alias/username used for the describe. (required)
- `--csv PATH` / `--json PATH` Also write the report to those files (the JSON also includes
  `has_meta_file` and `child_types`).
- `--api-version` Optional Metadata API version (e.g. `61.0`).

A type shows **UNKNOWN** when the org's describe doesn't list it — usually because that feature isn't
enabled in *that* org (not an error). Run against an org where the feature is on to classify it.

## Retrieve one component (`retrieve_first_member.py`)

A third script that answers yet another question: *"give me an actual example of this type."*
Point it at **one** type and **one** org; it finds the first member of that type and downloads it
(in metadata format) into a local folder — handy for inspecting what a type's source actually looks like.

```bash
# first member of the type -> ./components
python3 retrieve_first_member.py --type ApexClass --org my-org

# pick a specific member, save into a custom folder
python3 retrieve_first_member.py --type ContractType --org my-org \
    --member MyContract --output-dir out

# pin a specific Metadata API version
python3 retrieve_first_member.py --type LearningAchievementConfig --org my-org --api-version 61.0
```

Sample result:

```
components/package.xml
components/classes/BenefitVerificationMockDataGenerator.cls
components/classes/BenefitVerificationMockDataGenerator.cls-meta.xml
```

Options:

- `--type` A single metadata type name (no comma lists). (required)
- `--org` A single authorized org alias/username (no comma lists). (required)
- `--member` Retrieve this `fullName` instead of the auto-picked first member.
- `--output-dir` Folder to save into (default `components`).
- `--api-version` Optional Metadata API version (e.g. `61.0`).

Same auth model — it only needs one org you've already authorized. If the type has no members in the
org (or the org rejects listing it), you get a clear message instead of an empty folder.

## Notes / limits

- A type showing **No data** means the org genuinely has zero members of it
  (not an error). **ERROR** in a row means that one type's listing failed
  (e.g. a misspelled type name); the reason is printed beside it.
- Folder-based types (Report, Dashboard, Document, EmailTemplate) list at the
  folder level via this API; the listed types here and most config/Industries
  types work directly. Tell me if you need folder types and I'll extend it.
- `--workers` controls how many type lookups run in parallel per org (default 5).
  Lower it if you hit API limits on a busy org.
- `--api-version` pins the Metadata API version passed to `sf` (default `65.0`).
