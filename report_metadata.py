#!/usr/bin/env python3
"""
report_metadata.py
------------------
Produce a STRUCTURAL report for a list of Salesforce metadata TYPES:
for each type, its source-file extension, its folder (DX directory name),
and whether a member is a BUNDLE (a directory of files), lives IN-FOLDER
(folder-based, can nest folder-inside-folder), or is a FLAT single file.

Unlike verify_metadata.py (which lists members), this asks the org ONCE for
its describeMetadata (`sf org list metadata-types`) and reads the authoritative
suffix / directoryName / inFolder facts from it. That single call is the only
reliable source for arbitrary types (including Industries types).

It NEVER handles credentials. It shells out to `sf`, which must already
have the org authorized:

    sf org login web --alias my-org

Usage
-----
    # types from a file, structure read from an authorized org
    python3 report_metadata.py --types types.txt --org my-org

    # inline comma-separated types, also write CSV and JSON
    python3 report_metadata.py --types ContractType,Report,LightningComponentBundle \
        --org my-org --csv report.csv --json report.json

Options
-------
    --types        File path OR comma-separated list of metadata type names. (required)
    --org          An authorized sf org alias/username used for the describe. (required)
    --csv PATH     Also write the report to a CSV file.
    --json PATH    Also write the report to a JSON file.
    --api-version  Optional Metadata API version to pass to `sf` (e.g. 61.0).

Exit code is 0 on success, 1 on a fatal setup error (sf missing, org not
authorized, no types, describe failed).
"""

from __future__ import annotations  # lazy annotations -> works on Python 3.7+

import argparse
import csv
import json
import shutil
import subprocess
import sys

# Types whose members are a DIRECTORY of files (a "bundle"), which
# describeMetadata does not flag explicitly. Combined with the heuristic
# "no suffix and not folder-based" below to catch newer bundle types too.
KNOWN_BUNDLES = {
    "AuraDefinitionBundle",
    "LightningComponentBundle",
    "ExperienceBundle",
    "WaveTemplateBundle",
    "CommerceExperienceBundle",
    "ManagedContentTypeBundle",
}


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def run_sf(args: list[str]) -> dict:
    """Run an `sf ... --json` command and return the parsed JSON.
    Raises RuntimeError with a readable message on failure."""
    proc = subprocess.run(["sf", *args, "--json"], capture_output=True, text=True)
    out = proc.stdout.strip()
    try:
        data = json.loads(out) if out else {}
    except json.JSONDecodeError:
        raise RuntimeError(
            (proc.stderr or out or "no output").strip().splitlines()[0]
            if (proc.stderr or out)
            else "sf returned non-JSON output"
        )
    if proc.returncode != 0 and data.get("status", 0) != 0:
        msg = data.get("message") or (proc.stderr.strip().splitlines()[:1] or ["unknown sf error"])[0]
        raise RuntimeError(msg)
    return data


def load_types(spec: str) -> list[str]:
    """Accept either a file path (one type per line, '#' comments) or a comma list."""
    import os

    if os.path.isfile(spec):
        with open(spec, encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh]
        types = [ln for ln in lines if ln and not ln.startswith("#")]
    else:
        types = [t.strip() for t in spec.split(",") if t.strip()]
    seen, ordered = set(), []
    for t in types:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered


def verify_org_authorized(org: str) -> None:
    try:
        run_sf(["org", "display", "--target-org", org])
    except RuntimeError as e:
        die(
            f"org '{org}' is not authorized (or sf can't reach it): {e}\n"
            f"       Authorize it once with:  sf org login web --alias {org}"
        )


def describe_metadata(org: str, api_version: str | None) -> dict[str, dict]:
    """Return {xmlName: describeObject} for every type the org exposes."""
    args = ["org", "list", "metadata-types", "--target-org", org]
    if api_version:
        args += ["--api-version", api_version]
    data = run_sf(args)
    result = data.get("result") or {}
    objects = result.get("metadataObjects") or []
    catalog: dict[str, dict] = {}
    for obj in objects:
        name = obj.get("xmlName")
        if name:
            catalog[name] = obj
    if not catalog:
        die(f"describeMetadata for '{org}' returned no metadata types.")
    return catalog


def classify(mtype: str, obj: dict | None) -> dict:
    """Build one report row for a requested type from its describe object."""
    if obj is None:
        return {
            "type": mtype,
            "extension": "-",
            "folder": "-",
            "structure": "UNKNOWN",
            "dx_supported": "no",
            "is_bundle": "",
            "in_folder": "",
            "has_meta_file": "",
            "child_types": "",
            "note": "type not present in this org's describe (unsupported or misspelled)",
        }

    suffix = (obj.get("suffix") or "").strip()
    directory = obj.get("directoryName") or "-"
    in_folder = bool(obj.get("inFolder"))
    has_meta = bool(obj.get("metaFile"))
    children = obj.get("childXmlNames") or []

    is_bundle = mtype in KNOWN_BUNDLES or (not suffix and not in_folder and directory not in ("", "-"))

    if is_bundle:
        structure = "BUNDLE"
    elif in_folder:
        structure = "IN-FOLDER"  # folder-based; members can nest folder-inside-folder
    else:
        structure = "FLAT"

    return {
        "type": mtype,
        "extension": f".{suffix}" if suffix else "(none)",
        "folder": directory,
        "structure": structure,
        "dx_supported": "yes",  # present in the org's describe -> retrievable in DX source format
        "is_bundle": "yes" if is_bundle else "no",
        "in_folder": "yes" if in_folder else "no",
        "has_meta_file": "yes" if has_meta else "no",
        "child_types": ",".join(children),
        "note": "",
    }


COLUMNS = [
    ("type", "Metadata type"),
    ("extension", "Extension"),
    ("folder", "Folder"),
    ("structure", "Structure"),
    ("dx_supported", "DX supported"),
    ("is_bundle", "Bundle?"),
    ("in_folder", "In-folder?"),
]


def print_table(rows: list[dict]) -> None:
    widths = {}
    for key, header in COLUMNS:
        widths[key] = max([len(header)] + [len(str(r[key])) for r in rows])
    header = "  ".join(h.ljust(widths[k]) for k, h in COLUMNS)
    print("\n" + header)
    print("-" * len(header))
    for r in rows:
        line = "  ".join(str(r[k]).ljust(widths[k]) for k, _ in COLUMNS)
        if r["note"]:
            line += f"   ({r['note']})"
        print(line)
    bundles = sum(r["structure"] == "BUNDLE" for r in rows)
    folders = sum(r["structure"] == "IN-FOLDER" for r in rows)
    flat = sum(r["structure"] == "FLAT" for r in rows)
    unknown = sum(r["structure"] == "UNKNOWN" for r in rows)
    dx = sum(r["dx_supported"] == "yes" for r in rows)
    summary = f"\nSummary: {flat} flat, {folders} in-folder, {bundles} bundle"
    if unknown:
        summary += f", {unknown} unknown"
    summary += f"; {dx}/{len(rows)} DX-supported"
    print(summary + ".\n")


def write_csv(path: str, rows: list[dict]) -> None:
    fields = ["type", "extension", "folder", "structure", "dx_supported", "is_bundle", "in_folder", "has_meta_file", "child_types", "note"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in fields})
    print(f"CSV written to {path}")


def write_json(path: str, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2)
    print(f"JSON written to {path}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Structural report (extension / folder / bundle / in-folder) for Salesforce metadata types."
    )
    ap.add_argument("--types", required=True, help="File path (one type per line) OR comma-separated type list.")
    ap.add_argument("--org", required=True, help="An authorized sf org alias/username used for the describe.")
    ap.add_argument("--csv", help="Optional path to also write the report as CSV.")
    ap.add_argument("--json", dest="json_path", help="Optional path to also write the report as JSON.")
    ap.add_argument("--api-version", dest="api_version", help="Optional Metadata API version (e.g. 61.0).")
    args = ap.parse_args()

    if shutil.which("sf") is None:
        die("the Salesforce CLI ('sf') was not found on PATH. Install it: https://developer.salesforce.com/tools/salesforcecli")

    types = load_types(args.types)
    if not types:
        die("no metadata types found in --types input.")

    org = args.org.strip()
    if "," in org:
        first = org.split(",")[0].strip()
        print(f"Note: describe is per-org; using '{first}' only.", file=sys.stderr)
        org = first
    if not org:
        die("no org given in --org.")

    verify_org_authorized(org)

    print(f"Describing metadata types on '{org}' ...", file=sys.stderr)
    catalog = describe_metadata(org, args.api_version)

    rows = [classify(t, catalog.get(t)) for t in types]

    print_table(rows)
    if args.csv:
        write_csv(args.csv, rows)
    if args.json_path:
        write_json(args.json_path, rows)


if __name__ == "__main__":
    main()
