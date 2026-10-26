#!/usr/bin/env python3
"""
Decode Huawei HSS/IMS export files (MemExpFile_HSS9860_*) into a CSV with:
    IMSI, MSISDN, IMPU, IMPI

Usage:
    python script.py .                      (current folder)
    python script.py C:\\path\\to\\folder -o subscribers.csv
    python script.py . --keep-sip           (keep "sip:" prefix on IMPU)
"""
import argparse
import csv
import glob
import os
import re

IMSI_PREFIX = "6500101"          # Malawi IMSI prefix (15 digits)
MSISDN_PREFIX = "2658"           # Malawi MSISDN prefix (12 digits)
DOMAIN_SUFFIX = "mnc001.mcc650.3gppnetwork.org"


def parse_file(path):
    """Yield one dict per <SUBBEGIN ... <SUBEND block (streamed line by line)."""
    sub = None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()

            if line.startswith("<SUBBEGIN"):
                sub = {"imsi": None, "msisdn": None, "impus": set(), "impis": set()}
                continue

            if line.startswith("<SUBEND"):
                if sub is not None:
                    yield sub
                sub = None
                continue

            if sub is None:
                continue

            if line.startswith("IMSI="):
                sub["imsi"] = line[5:].rstrip(";").strip()
            elif line.startswith("MSISDN="):
                sub["msisdn"] = line[7:].rstrip(";").strip()
            elif line.startswith("IMPU="):
                sub["impus"].add(line[5:].rstrip(";").strip())
            elif line.startswith("IMPI="):
                sub["impis"].add(line[5:].rstrip(";").strip())


def build_row(sub, keep_sip):
    imsi, msisdn = sub["imsi"], sub["msisdn"]
    if not imsi or not msisdn:
        return None
    if not (re.fullmatch(r"\d{15}", imsi) and imsi.startswith(IMSI_PREFIX)):
        return None
    if not (re.fullmatch(r"\d{12}", msisdn) and msisdn.startswith(MSISDN_PREFIX)):
        return None

    # IMPU: <imsi>@ics.<domain>
    impu = next((u for u in sub["impus"]
                 if re.fullmatch(rf"(sip:)?{imsi}@ics\..+", u)),
                f"sip:{imsi}@ics.{DOMAIN_SUFFIX}")
    # IMPI: <imsi>@ims.<domain>
    impi = next((i for i in sub["impis"]
                 if re.fullmatch(rf"{imsi}@ims\..+", i)),
                f"{imsi}@ims.{DOMAIN_SUFFIX}")

    if not keep_sip and impu.startswith("sip:"):
        impu = impu[4:]
    return [imsi, msisdn, impu, impi]


def main():
    ap = argparse.ArgumentParser(description="Convert HSS IMS export files to CSV")
    ap.add_argument("folder", nargs="?", default=".",
                    help="Folder with MemExpFile_HSS9860_* files (default: current)")
    ap.add_argument("-o", "--output", default="ims_subscribers.csv")
    ap.add_argument("--pattern", default="MemExpFile_HSS9860_*")
    ap.add_argument("--keep-sip", action="store_true",
                    help="Keep the 'sip:' prefix on the IMPU column")
    args = ap.parse_args()

    files = sorted(p for p in glob.glob(os.path.join(args.folder, args.pattern))
                   if os.path.isfile(p))
    if not files:
        raise SystemExit(f"No files matching {args.pattern} in {args.folder}")

    seen = set()
    written = skipped = dupes = 0

    with open(args.output, "w", newline="", encoding="utf-8-sig") as out:
        w = csv.writer(out)
        w.writerow(["IMSI", "MSISDN", "IMPU", "IMPI"])

        for path in files:
            print(f"Reading {os.path.basename(path)} ...")
            for sub in parse_file(path):
                row = build_row(sub, args.keep_sip)
                if row is None:
                    skipped += 1
                    continue
                if row[0] in seen:
                    dupes += 1
                    continue
                seen.add(row[0])
                w.writerow(row)
                written += 1

    print(f"\nDone. Rows written: {written} | duplicates skipped: {dupes} "
          f"| filtered out: {skipped}")
    print(f"Output: {os.path.abspath(args.output)}")


if __name__ == "__main__":
    main()
