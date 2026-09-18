#!/usr/bin/env python3
"""
retrieve_first_member.py
------------------------
Given a SINGLE Salesforce metadata TYPE and an org, find the first member of
that type in the org and retrieve it (in metadata format) into a local folder.

It NEVER handles credentials. It shells out to `sf`, which must already have the
org authorized:

    sf org login web --alias my-org

Usage
-----
    # retrieve the first ContractType member into ./components
    python3 retrieve_first_member.py --type ContractType --org my-org

    # pick a specific member instead of the first, into a custom folder
    python3 retrieve_first_member.py --type ContractType --org my-org \
        --member MyContract --output-dir out

Options
-------
    --type          A single metadata type name (e.g. ContractType).   (required)
    --org           An authorized sf org alias/username.               (required)
    --member        Retrieve this member's fullName instead of the first one found.
    --output-dir    Folder to save components into (default 'components').
    --api-version   Optional Metadata API version to pass to `sf` (e.g. 61.0).

Exit code is 0 on success, 1 on a fatal error (sf missing, org not authorized,
type has no members, retrieve failed).
"""

from __future__ import annotations  # lazy annotations -> works on Python 3.7+

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def run_sf(args: list[str]) -> dict:
    """Run an `sf ... --json` command and return the parsed JSON.
    Raises RuntimeError with a readable message on failure."""
    proc = subprocess.run(["sf", *args, "--json"], capture_output=True, text=True)
    out = proc.stdout.strip()
    # sf returns JSON even on most errors; try to parse regardless of exit code.
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


def verify_org_authorized(org: str) -> None:
    try:
        run_sf(["org", "display", "--target-org", org])
    except RuntimeError as e:
        die(
            f"org '{org}' is not authorized (or sf can't reach it): {e}\n"
            f"       Authorize it once with:  sf org login web --alias {org}"
        )


def first_member(org: str, mtype: str, api_version: str | None) -> str:
    """Return the fullName of the first member of `mtype` in `org`."""
    args = ["org", "list", "metadata", "--metadata-type", mtype, "--target-org", org]
    if api_version:
        args += ["--api-version", api_version]
    try:
        data = run_sf(args)
    except RuntimeError as e:
        die(f"could not list members of '{mtype}' in '{org}': {e}")
    result = data.get("result") or []
    if isinstance(result, dict):  # defensive: some sf versions wrap single results
        result = [result]
    members = [m.get("fullName") for m in result if m.get("fullName")]
    if not members:
        die(f"type '{mtype}' has no members in '{org}' (nothing to retrieve).")
    # `sf` returns members unordered; sort for a stable, predictable "first".
    members.sort()
    return members[0]


def retrieve_member(org: str, mtype: str, member: str, output_dir: str, api_version: str | None) -> list[str]:
    """Retrieve one component (Type:member) in metadata format into output_dir.

    `sf ... --target-metadata-dir` drops a single `unpackaged.zip` (source-format
    `--output-dir` needs a DX project, which we don't require). We unzip it into
    output_dir ourselves, stripping the leading `unpackaged/` folder, then remove
    the zip. Returns the list of extracted file paths."""
    args = [
        "project", "retrieve", "start",
        "--metadata", f"{mtype}:{member}",
        "--target-org", org,
        "--target-metadata-dir", output_dir,
    ]
    if api_version:
        args += ["--api-version", api_version]
    try:
        data = run_sf(args)
    except RuntimeError as e:
        die(f"retrieve of '{mtype}:{member}' failed: {e}")

    # `sf` reports status 0 / success true even when the Metadata API refused to
    # serialize the component ("Load of metadata from db failed ..."). That shows
    # up only as a per-file problem in result.messages, so surface it explicitly.
    result = data.get("result") or {}
    problems = [m for m in (result.get("messages") or []) if isinstance(m, dict) and m.get("problem")]
    if problems:
        detail = problems[0].get("problem")
        stray_zip = os.path.join(output_dir, "unpackaged.zip")
        if os.path.isfile(stray_zip):
            os.remove(stray_zip)
        die(
            f"Salesforce could not retrieve '{mtype}:{member}': {detail}\n"
            f"       (the member is listable but not retrievable via the Metadata API — "
            f"often a Data Cloud / Industries type bound to a parent object; not a permission you can grant here.)"
        )

    zip_path = os.path.join(output_dir, "unpackaged.zip")
    if not os.path.isfile(zip_path):
        die(f"retrieve reported success but no 'unpackaged.zip' was written to '{output_dir}'.")

    written: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            # Strip the archive's top-level 'unpackaged/' prefix so files land
            # directly under output_dir (e.g. output_dir/classes/Foo.cls).
            rel = name.split("/", 1)[1] if name.startswith("unpackaged/") else name
            dest = os.path.join(output_dir, rel)
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            with zf.open(name) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            written.append(dest)
    os.remove(zip_path)

    # A retrieve that yields only package.xml means nothing real came down — treat
    # it as a failure rather than reporting a hollow "success".
    components = [f for f in written if os.path.basename(f) != "package.xml"]
    if not components:
        die(
            f"retrieve produced only 'package.xml' for '{mtype}:{member}' — no component was returned. "
            f"The type may not be retrievable as standalone source via the Metadata API."
        )
    return sorted(written)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Retrieve the first member of a single Salesforce metadata type into a local folder."
    )
    ap.add_argument("--type", required=True, help="A single metadata type name (e.g. ContractType).")
    ap.add_argument("--org", required=True, help="An authorized sf org alias/username.")
    ap.add_argument("--member", help="Retrieve this fullName instead of the first member found.")
    ap.add_argument("--output-dir", dest="output_dir", default="components", help="Folder to save into (default 'components').")
    ap.add_argument("--api-version", dest="api_version", help="Optional Metadata API version (e.g. 61.0).")
    args = ap.parse_args()

    if shutil.which("sf") is None:
        die("the Salesforce CLI ('sf') was not found on PATH. Install it: https://developer.salesforce.com/tools/salesforcecli")

    mtype = args.type.strip()
    if not mtype or "," in mtype:
        die("--type takes exactly one metadata type name.")

    org = args.org.strip()
    if not org or "," in org:
        die("--org takes exactly one authorized org alias/username.")

    verify_org_authorized(org)

    member = args.member.strip() if args.member else first_member(org, mtype, args.api_version)
    print(f"Retrieving {mtype}:{member} from '{org}' into '{args.output_dir}/' ...", file=sys.stderr)

    os.makedirs(args.output_dir, exist_ok=True)
    files = retrieve_member(org, mtype, member, args.output_dir, args.api_version)

    if not files:
        die(f"retrieve reported success but wrote no files for '{mtype}:{member}'.")

    print(f"\nSaved {len(files)} file(s) under '{args.output_dir}/':")
    for f in files:
        print(f"  {f}")
    print()


if __name__ == "__main__":
    main()
