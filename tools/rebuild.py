#!/usr/bin/env python3
"""
Rebuild a NAND image from an original dump and staged modifications.

Reads the GPT from the original dump to discover partition layout. If the
Docker named volume 'contra-rootfs' exists, repacks the rootfs from it.
Otherwise falls back to a host staging directory (for Linux users).

Usage:
    python3 tools/rebuild.py [original.bin] [--staging-dir DIR] [--output FILE]

Defaults:
    original:    firmware/dumps/nand_original.bin
    staging-dir: firmware/staging/
    output:      build/firmware/nand.bin
"""

import argparse
import os
import sys

from lib.gpt import LBA_SIZE, parse_gpt
from lib.nand import FILENAME_MAP, copy_region, write_file_at
from lib.squashfs import SquashFSError, repack_rootfs, volume_exists


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(script_dir)

    parser = argparse.ArgumentParser(
        description="Rebuild a NAND image from original dump and staged modifications."
    )
    parser.add_argument(
        "original",
        nargs="?",
        default=os.path.join(project_dir, "firmware", "dumps", "nand_original.bin"),
        help="Path to original NAND dump (default: firmware/dumps/nand_original.bin)",
    )
    parser.add_argument(
        "--staging-dir",
        default=os.path.join(project_dir, "firmware", "staging"),
        help="Staging directory with modifications (default: firmware/staging/)",
    )
    parser.add_argument(
        "--output",
        default=os.path.join(project_dir, "build", "firmware", "nand.bin"),
        help="Output NAND image path (default: build/firmware/nand.bin)",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.original):
        print(f"Error: original dump not found: {args.original}")
        sys.exit(1)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    image_size = os.path.getsize(args.original)
    total_sectors = image_size // LBA_SIZE

    print("=== Rebuilding NAND image ===")
    print(f"Original: {args.original} ({image_size} bytes, {total_sectors} sectors)")
    print(f"Staging:  {args.staging_dir}/")
    print(f"Output:   {args.output}")
    print()

    # Parse GPT from original dump
    partitions = parse_gpt(args.original)
    if not partitions:
        print("Error: no partitions found in GPT")
        sys.exit(1)

    # Handle rootfs repacking — prefer Docker volume, fall back to host dir
    build_firmware_dir = os.path.dirname(args.output)
    staged_rootfs_squashfs = os.path.join(build_firmware_dir, "rootfs.squashfs")

    repacked_rootfs = None
    staged_rootfs_dir = os.path.join(args.staging_dir, "rootfs")

    if volume_exists():
        print("Repacking rootfs from Docker volume...")
        try:
            repack_rootfs(staged_rootfs_squashfs)
        except SquashFSError as e:
            print(f"Error: {e}")
            sys.exit(1)
        repacked_rootfs = staged_rootfs_squashfs
    elif os.path.isdir(staged_rootfs_dir):
        print("Repacking rootfs from host directory (fallback)...")
        try:
            repack_rootfs(staged_rootfs_squashfs)
        except SquashFSError as e:
            print(f"Error: {e}")
            sys.exit(1)
        repacked_rootfs = staged_rootfs_squashfs
    else:
        print("Warning: no staged rootfs found (no Docker volume, no host directory)")
        print("  Rootfs partition will be copied from original dump")

    if repacked_rootfs:
        # Validate size against rootfs partition
        rootfs_part = next((p for p in partitions if p.name == "rootfs"), None)
        if rootfs_part:
            actual_size = os.path.getsize(staged_rootfs_squashfs)
            max_size = rootfs_part.size_bytes
            pct = actual_size * 100 // max_size
            print(f"  -> rootfs size: {actual_size} / {max_size} bytes ({pct}% of partition)")
            if actual_size > max_size:
                print(f"Error: rebuilt rootfs ({actual_size} bytes) exceeds partition ({max_size} bytes)")
                sys.exit(1)
        print()

    # Step 1: Create zero-filled image
    print(f"Creating {image_size}-byte image...")
    with open(args.output, "wb") as f:
        f.write(b"\x00" * image_size)

    # Step 2: Copy GPT header + partition table (sectors 0-33) from original
    first_partition_lba = partitions[0].first_lba if partitions else 34
    gpt_sectors = 34  # protective MBR + GPT header + partition entries

    print(f"Copying GPT header and partition table (sectors 0-{gpt_sectors - 1})...")
    copy_region(args.original, args.output, 0, 0, gpt_sectors * LBA_SIZE)

    # Step 3: Copy pre-partition area (between GPT and first partition)
    if first_partition_lba > gpt_sectors:
        gap = first_partition_lba - gpt_sectors
        print(f"Copying pre-partition area (sectors {gpt_sectors}-{first_partition_lba - 1})...")
        copy_region(
            args.original, args.output,
            gpt_sectors * LBA_SIZE, gpt_sectors * LBA_SIZE,
            gap * LBA_SIZE,
        )

    # Step 4: Write each partition
    for p in partitions:
        filename = FILENAME_MAP.get(p.name, f"{p.name}.img")
        staged_file = os.path.join(args.staging_dir, filename)

        # Use repacked rootfs from build dir if available
        if p.name == "rootfs" and repacked_rootfs:
            print(f"Writing {p.name} at sector {p.first_lba} (repacked: {repacked_rootfs})...")
            write_file_at(repacked_rootfs, args.output, p.offset_bytes)
        elif os.path.isfile(staged_file):
            print(f"Writing {p.name} at sector {p.first_lba} (staged: {staged_file})...")
            write_file_at(staged_file, args.output, p.offset_bytes)
        else:
            print(f"Writing {p.name} at sector {p.first_lba} (original)...")
            copy_region(
                args.original, args.output,
                p.offset_bytes, p.offset_bytes,
                p.size_bytes,
            )

    # Step 5: Copy backup GPT from end of original image
    # Backup GPT is the last 33 sectors (32 entry sectors + 1 header sector)
    last_partition = partitions[-1]
    backup_gpt_start = last_partition.last_lba + 1
    backup_gpt_size = (total_sectors - backup_gpt_start) * LBA_SIZE

    if backup_gpt_size > 0:
        print(f"Copying backup GPT (sector {backup_gpt_start} to end)...")
        copy_region(
            args.original, args.output,
            backup_gpt_start * LBA_SIZE, backup_gpt_start * LBA_SIZE,
            backup_gpt_size,
        )

    print()
    print("=== Done ===")
    print(f"Output: {args.output}")
    print(f"Size:   {os.path.getsize(args.output)} bytes")


if __name__ == "__main__":
    main()
