#!/usr/bin/env python3
"""
Viatom / Wellue O2Ring Binary-to-CSV Converter
===============================================
Decoding logic faithfully ported from OSCAR's viatom_loader.cpp / viatom_loader.h.

Supports three binary formats:
  1. O2Ring S  (signature 0x0301) – 10-byte header, 3-byte records @ 1 s
  2. Viatom    (sig 0x0003/0x0005/0x0006) – 40-byte header, 5-byte records @ 2-4 s
  3. POD2      (no header, directory-based detection) – 6-byte records @ ~1 s

limit. When a file ends at exactly 36,000 samples and the next file starts
within 5 minutes, they are merged into a single CSV.

Usage:
    python viatom_to_csv.py <input_dir> [output_dir]

    input_dir  – folder containing raw Viatom binary files (e.g. 2542300144/)
    output_dir – where CSVs will be written (default: input_dir)

Files that are already .csv, .pdf, .lnk, or directories are skipped automatically.
"""

import sys
import subprocess
from pathlib import Path
import viatom_session_utils as vsu

def write_csv(records, resolution_s, format_type, output_path: Path):
    """Write records to a CSV file."""
    with open(output_path, 'w', newline='') as f:
        if format_type == 'pod2':
            f.write('Time,SpO2(%),Pulse Rate(bpm),Perfusion Index(%),Battery Level,Invalid\n')
            for rec in records:
                spo2_str = '' if rec['spo2'] is None else str(rec['spo2'])
                hr_str   = '' if rec['hr'] is None else str(rec['hr'])
                pi_str   = f"{rec['pi']:.1f}"
                bat_str  = str(rec['battery_level'])
                inv_str  = '1' if rec['oximetry_invalid'] else '0'
                time_str = rec['time'].strftime('%Y-%m-%d %H:%M:%S')
                f.write(f'{time_str},{spo2_str},{hr_str},{pi_str},{bat_str},{inv_str}\n')
        else:
            # O2Ring S and standard Viatom
            f.write('Time,SpO2(%),Pulse Rate(bpm),Motion,Vibration,Invalid\n')
            for rec in records:
                spo2_str = '' if rec['spo2'] is None else str(rec['spo2'])
                hr_str   = '' if rec['hr'] is None else str(rec['hr'])
                motion_str = str(rec['motion']) if rec['motion'] is not None else '0'
                vib_str    = str(rec['vibration']) if rec['vibration'] is not None else '0'
                inv_str    = '1' if rec['oximetry_invalid'] else '0'
                time_str   = rec['time'].strftime('%Y-%m-%d %H:%M:%S')
                f.write(f'{time_str},{spo2_str},{hr_str},{motion_str},{vib_str},{inv_str}\n')


def main():
    DEFAULT_INPUT = Path(r"K:\cc\sleepanalysis\O2InsightProData\2542300144")
    DEFAULT_OUTPUT = DEFAULT_INPUT / "csv_output"

    if len(sys.argv) < 2:
        print(f"No arguments provided. Using default input: {DEFAULT_INPUT}")
        input_dir = DEFAULT_INPUT
        output_dir = DEFAULT_OUTPUT
    else:
        input_dir = Path(sys.argv[1])
        output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else input_dir

    # Load configuration
    config = vsu.get_config()
    min_duration_s = config.get('skip_short_sessions_under_mins', 60) * 60

    if not input_dir.is_dir():
        print(f"Error: '{input_dir}' is not a directory")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Cleaning old CSVs in {output_dir}...")
    for old_csv in output_dir.glob("*.csv"):
        try:
            old_csv.unlink()
        except Exception as e:
            print(f"  [WARN] Could not delete {old_csv.name}: {e}")

    candidates = sorted([
        f for f in input_dir.iterdir()
        if f.is_file() and f.suffix.lower() not in vsu.SKIP_EXTENSIONS
    ])

    print(f"Scanning {input_dir} — {len(candidates)} candidate files\n")
    print("--- Pass 1: Parsing files ---\n")

    parsed = []
    skipped = 0
    for filepath in candidates:
        result = vsu.parse_file(filepath)
        if result is not None:
            records, resolution_s, fmt = result
            start = records[0]['time']
            duration_min = len(records) * resolution_s / 60.0
            max_tag = " [MAX]" if vsu.is_max_session(records, fmt) else ""
            print(f"  [OK]   {filepath.name}  "
                  f"({fmt}, {len(records)} samples @ {resolution_s:.0f}s, "
                  f"{start.strftime('%Y-%m-%d %H:%M')}, "
                  f"{duration_min:.1f} min){max_tag}")
            parsed.append((filepath, records, resolution_s, fmt))
        else:
            skipped += 1

    if not parsed:
        print(f"\nNo files parsed. {skipped} skipped.")
        return

    parsed.sort(key=lambda x: x[1][0]['time'])

    print(f"\n--- Pass 2: Merging split sessions ---\n")
    groups = vsu.group_sessions_for_merging(parsed)

    print(f"--- Pass 3: Writing CSV files (skipping < 1h) ---\n")
    converted = 0
    merged_count = 0
    skipped_short = 0

    for group in groups:
        first_fp, first_records, first_res, first_fmt = group[0]

        if len(group) == 1:
            all_records = first_records
            resolution_s = first_res
            fmt = first_fmt
            duration_s = len(all_records) * resolution_s
            
            # For unmerged ones, we might have `_merged.dat` name if it was pre-merged by downloader!
            # if name ends in _merged.dat, remove that before generating name to avoid `foo_merged_merged.csv`.
            original_stem = first_fp.stem
            if original_stem.endswith('_merged'):
                original_stem = original_stem[:-7]
                
            out_name = vsu.generate_filename(original_stem, all_records[0]['time'], duration_s)
            
            if duration_s < min_duration_s and min_duration_s > 0:
                print(f"  [SKIP] {first_fp.name} -> {out_name} skipped < {min_duration_s//60}m ({duration_s/60:.1f} min)")
                skipped_short += 1
                continue

            duration_min = duration_s / 60.0
            print(f"  [OK]   {first_fp.name} -> {out_name}  "
                  f"({len(all_records)} samples, "
                  f"{all_records[0]['time'].strftime('%Y-%m-%d %H:%M')}, "
                  f"{duration_min:.1f} min)")
        else:
            # Reconstruct via utility
            all_records, interpolated_total, shifted_total = vsu.merge_records_with_interpolation(group)
            resolution_s = first_res
            fmt = first_fmt
            source_names = [fp.name for fp, _, _, _ in group]
            duration_s = len(all_records) * resolution_s
            
            original_stem = first_fp.stem
            out_name = vsu.generate_filename(original_stem, all_records[0]['time'], duration_s)
            
            if duration_s < min_duration_s and min_duration_s > 0:
                print(f"  [SKIP] Merge result -> {out_name} skipped < {min_duration_s//60}m ({duration_s/60:.1f} min)")
                skipped_short += 1
                continue

            duration_min = duration_s / 60.0
            total_hours = duration_min / 60.0

            files_str = " + ".join(source_names)
            extra_info = []
            if interpolated_total > 0: extra_info.append(f"{interpolated_total}s interpolated")
            if shifted_total > 0: extra_info.append(f"{shifted_total} segments shifted")
            info_str = ", ".join(extra_info) if extra_info else "seamless"

            print(f"  [MERGE] {files_str}")
            print(f"          -> {out_name}  "
                  f"({len(all_records)} samples, "
                  f"{all_records[0]['time'].strftime('%Y-%m-%d %H:%M')} to "
                  f"{all_records[-1]['time'].strftime('%H:%M')}, "
                  f"{total_hours:.1f}h, {info_str})")
            merged_count += len(group) - 1

        out_path = output_dir / out_name
        write_csv(all_records, resolution_s, fmt, out_path)
        vsu.set_file_timestamps(out_path, all_records[0]['time'])
        converted += 1

    total_files = sum(len(g) for g in groups)
    print(f"\nDone: {total_files} files -> {converted} CSVs "
          f"({merged_count} merged, {skipped_short} skipped < {min_duration_s//60}m), "
          f"{skipped} files skipped (format/read error)")

    launch_exe = config.get('launch_after', '')
    if launch_exe:
        print(f"\nLaunching followup program: {launch_exe}...")
        try:
            subprocess.Popen(launch_exe, shell=True)
        except Exception as e:
            print(f"Error launching followup program: {e}")

if __name__ == '__main__':
    main()
