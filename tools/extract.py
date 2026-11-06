#!/usr/bin/env python3
"""
Extract partitions from a NAND dump using its GPT partition table.

Reads the GPT to discover partition names and offsets (no hardcoded values),
extracts each partition to a separate file, and unsquashes the rootfs
partition into a Docker named volume (preserving symlinks).

Usage:
    python3 tools/extract.py [input.bin] [--output-dir DIR]

Defaults:
    input:      firmware/dumps/nand_original.bin
    output-dir: firmware/extracted/
"""

import argparse
import os
import sys

from lib.gpt import parse_gpt
from lib.nand import FILENAME_MAP, extract_partition, verify_magic
from lib.squashfs import VOLUME_NAME, SquashFSError, ensure_volume, unsquash_rootfs


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(script_dir)

    parser = argparse.ArgumentParser(
        description="Extract partitions from a NAND dump using its GPT."
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=os.path.join(project_dir, "firmware", "dumps", "nand_original.bin"),
        help="Path to NAND dump image (default: firmware/dumps/nand_original.bin)",
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.join(project_dir, "firmware", "extracted"),
        help="Output directory for extracted partitions (default: firmware/extracted/)",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"Error: input file not found: {args.input}")
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    print(f"=== Extracting partitions from: {args.input} ===")
    print()

    # Parse GPT from the image
    partitions = parse_gpt(args.input)

    if not partitions:
        print("Error: no partitions found in GPT")
        sys.exit(1)

    print(f"Found {len(partitions)} partitions:")
    for p in partitions:
        size_mb = p.size_bytes / (1024 * 1024)
        print(f"  {p.name:<12} sector {p.first_lba:>6} - {p.last_lba:>6}  ({size_mb:.1f} MiB)")
    print()

    # Extract each partition
    rootfs_path = None
    for p in partitions:
        filename = FILENAME_MAP.get(p.name, f"{p.name}.img")
        output_path = os.path.join(args.output_dir, filename)

        print(f"Extracting {filename} ({p.sector_count} sectors, {p.size_bytes} bytes)...")
        extract_partition(args.input, output_path, p.offset_bytes, p.size_bytes)
        ok, msg = verify_magic(output_path, p.name)
        if msg:
            prefix = "OK" if ok else "WARNING"
            print(f"  -> {prefix}: {msg}")

        if p.name == "rootfs":
            rootfs_path = output_path

    # Unsquash rootfs into Docker named volume
    if rootfs_path:
        print()
        print("=== Extracting rootfs SquashFS ===")
        ensure_volume()
        try:
            success, detail = unsquash_rootfs(rootfs_path)
            if not success:
                print(f"  -> ERROR: {detail}")
            elif detail:
                print(detail)
        except SquashFSError as e:
            print(f"  -> ERROR: {e}")

    print()
    print("=== Done ===")
    print(f"Partitions: {args.output_dir}/")
    if rootfs_path:
        print(f"Rootfs:     Docker volume '{VOLUME_NAME}'")
        print(f"  Inspect:  docker run --rm -v {VOLUME_NAME}:/staging contra-toolchain ls /staging/rootfs/")


if __name__ == "__main__":
    main()
