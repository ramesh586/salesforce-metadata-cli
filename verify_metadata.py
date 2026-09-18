#!/usr/bin/env python3
"""
verify_metadata.py
------------------
Check whether a list of Salesforce metadata TYPES has any members (data)
in one or more orgs, using the Salesforce CLI's Metadata API listing.

It NEVER handles credentials. It shells out to `sf`, which must already
have each org authorized:

    sf org login web --alias my-org

Usage
-----
    # one org, types from a file (one type per line, '#' comments allowed)
    python3 verify_metadata.py --types types.txt --org my-org

    # inline comma-separated types
    python3 verify_metadata.py --types ContractType,DocumentGenerationSetting --org my-org

    # several orgs -> comparison matrix
    python3 verify_metadata.py --types types.txt --org my-org,uat-sandbox,prod

Options
-------
    --types        File path OR comma-separated list of metadata type names. (required)
    --org          One or more sf org aliases/usernames, comma-separated.     (required)
    --workers      Parallel sf calls per org (default 5).
    --csv PATH     Also write results to a CSV file.
    --api-version  Metadata API version passed to sf (default 65.0).

Exit code is 0 on success, 1 on a fatal setup error (sf missing, org not
authorized, no types, etc.).
"""

from __future__ import annotations  # lazy annotations -> works on Python 3.7+

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def run_sf(args: list[str]) -> dict:
    """Run an `sf ... --json` command and return the parsed JSON.
    Raises RuntimeError with a readable message on failure."""
    proc = subprocess.run(
        ["sf", *args, "--json"],
        capture_output=True,
        text=True,
    )
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


def load_types(spec: str) -> list[str]:
    """Accept either a file path (one type per line) or a comma-separated list."""
    if os.path.isfile(spec):
        with open(spec, encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh]
        types = [ln for ln in lines if ln and not ln.startswith("#")]
    else:
        types = [t.strip() for t in spec.split(",") if t.strip()]
    # de-dupe, preserve order
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


def count_members(org: str, mtype: str, api_version: str) -> tuple[str, int | None, str]:
    """Return (type, member_count_or_None, note).
    count None means the listing errored for that type."""
    try:
        data = run_sf([
            "org", "list", "metadata",
            "--metadata-type", mtype,
            "--target-org", org,
            "--api-version", api_version,
        ])
    except RuntimeError as e:
        return (mtype, None, str(e))
    result = data.get("result") or []
    if isinstance(result, dict):  # defensive: some sf versions wrap single results
        result = [result]
    return (mtype, len(result), "")


def scan_org(org: str, types: list[str], workers: int, api_version: str) -> dict[str, tuple[int | None, str]]:
    results: dict[str, tuple[int | None, str]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(count_members, org, t, api_version): t for t in types}
        for fut in as_completed(futures):
            mtype, count, note = fut.result()
            results[mtype] = (count, note)
    return results


def verdict(count: int | None) -> str:
    if count is None:
        return "ERROR"
    return "Has data" if count > 0 else "No data"


def print_single(org: str, types: list[str], res: dict[str, tuple[int | None, str]]) -> None:
    w = max([len("Metadata type")] + [len(t) for t in types])
    print(f"\nOrg: {org}\n")
    print(f"{'Metadata type'.ljust(w)}  | Verdict   | Members")
    print(f"{'-' * w}--+-----------+--------")
    has = no = err = 0
    for t in types:
        count, note = res[t]
        v = verdict(count)
        has += v == "Has data"
        no += v == "No data"
        err += v == "ERROR"
        cnt = "-" if count is None else str(count)
        line = f"{t.ljust(w)}  | {v.ljust(9)} | {cnt}"
        if note:
            line += f"   ({note})"
        print(line)
    print(f"\nSummary: {has} has data, {no} no data" + (f", {err} error(s)" if err else "") + ".\n")


def print_matrix(orgs: list[str], types: list[str], allres: dict[str, dict]) -> None:
    w = max([len("Metadata type")] + [len(t) for t in types])
    ow = [max(len(o), 9) for o in orgs]
    header = "Metadata type".ljust(w) + "  | " + " | ".join(o.ljust(ow[i]) for i, o in enumerate(orgs))
    print("\n" + header)
    print("-" * len(header))
    for t in types:
        cells = []
        for i, o in enumerate(orgs):
            count, _ = allres[o][t]
            label = "ERROR" if count is None else ("Has data" if count > 0 else "No data")
            if count not in (None, 0):
                label += f" ({count})"
            cells.append(label.ljust(ow[i]))
        print(t.ljust(w) + "  | " + " | ".join(cells))
    print()


def write_csv(path: str, orgs: list[str], types: list[str], allres: dict[str, dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        header = ["metadata_type"]
        for o in orgs:
            header += [f"{o}__verdict", f"{o}__members"]
        writer.writerow(header)
        for t in types:
            row = [t]
            for o in orgs:
                count, _ = allres[o][t]
                row += [verdict(count), "" if count is None else count]
            writer.writerow(row)
    print(f"CSV written to {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Verify whether Salesforce metadata types have data in one or more orgs.")
    ap.add_argument("--types", required=True, help="File path (one type per line) OR comma-separated type list.")
    ap.add_argument("--org", required=True, help="One or more sf org aliases/usernames, comma-separated.")
    ap.add_argument("--workers", type=int, default=5, help="Parallel sf calls per org (default 5).")
    ap.add_argument("--csv", help="Optional path to also write results as CSV.")
    ap.add_argument("--api-version", dest="api_version", default="65.0", help="Metadata API version (default 65.0).")
    args = ap.parse_args()

    if shutil.which("sf") is None:
        die("the Salesforce CLI ('sf') was not found on PATH. Install it: https://developer.salesforce.com/tools/salesforcecli")

    types = load_types(args.types)
    if not types:
        die("no metadata types found in --types input.")

    orgs = [o.strip() for o in args.org.split(",") if o.strip()]
    if not orgs:
        die("no orgs given in --org.")

    for o in orgs:
        verify_org_authorized(o)

    allres: dict[str, dict] = {}
    for o in orgs:
        print(f"Scanning {len(types)} type(s) against '{o}' ...", file=sys.stderr)
        allres[o] = scan_org(o, types, args.workers, args.api_version)

    if len(orgs) == 1:
        print_single(orgs[0], types, allres[orgs[0]])
    else:
        print_matrix(orgs, types, allres)

    if args.csv:
        write_csv(args.csv, orgs, types, allres)


if __name__ == "__main__":
    main()
