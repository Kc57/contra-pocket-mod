"""
NAND image I/O operations and firmware validation.

Provides functions for extracting partitions, copying regions, writing files
into NAND images, and validating Contra Pocket firmware dumps.
"""

import os

from .gpt import GPT_HEADER_LBA, GPT_HEADER_SIGNATURE, LBA_SIZE, parse_gpt

# Expected NAND image size: 128 MiB
EXPECTED_IMAGE_SIZE = 134_217_728

# Partition names expected in a valid Contra Pocket firmware dump
EXPECTED_PARTITIONS = ["uboot", "trust", "boot", "rootfs", "userdata"]

# Known magic bytes for partition verification
MAGIC_CHECKS = {
    "uboot": (b"LOADER", "LOADER"),
    "trust": (b"TOS", "TOS"),
    "boot": (b"ANDROID!", "ANDROID!"),
    "rootfs": (b"hsqs", "hsqs (SquashFS)"),
}

# Map partition names to output filenames
FILENAME_MAP = {
    "uboot": "uboot.img",
    "trust": "trust.img",
    "boot": "boot.img",
    "rootfs": "rootfs.squashfs",
    "userdata": "userdata.img",
}


def validate_firmware(image_path: str) -> tuple[bool, list[str]]:
    """Validate a NAND dump as Contra Pocket firmware.

    Checks file size, GPT signature, and expected partition names.

    Returns:
        (valid, errors) — True with empty list if valid, False with error messages if not.
    """
    errors = []

    if not os.path.isfile(image_path):
        return False, [f"File not found: {image_path}"]

    # Check file size
    size = os.path.getsize(image_path)
    if size != EXPECTED_IMAGE_SIZE:
        errors.append(
            f"Unexpected file size: {size} bytes "
            f"(expected {EXPECTED_IMAGE_SIZE} bytes / 128 MiB)"
        )

    # Check GPT signature
    try:
        with open(image_path, "rb") as f:
            f.seek(GPT_HEADER_LBA * LBA_SIZE)
            sig = f.read(len(GPT_HEADER_SIGNATURE))
            if sig != GPT_HEADER_SIGNATURE:
                errors.append(
                    f"Invalid GPT signature: expected {GPT_HEADER_SIGNATURE!r}, "
                    f"got {sig!r}"
                )
    except OSError as e:
        return False, [f"Cannot read file: {e}"]

    # If GPT signature is bad, skip partition check
    if errors:
        return False, errors

    # Check expected partition names
    try:
        partitions = parse_gpt(image_path)
        found_names = {p.name for p in partitions}
        for name in EXPECTED_PARTITIONS:
            if name not in found_names:
                errors.append(f"Missing expected partition: {name}")
    except (ValueError, OSError) as e:
        errors.append(f"Failed to parse GPT: {e}")

    return len(errors) == 0, errors


def extract_partition(image_path: str, output_path: str, offset: int, size: int):
    """Extract a region from a NAND image file to a separate file."""
    with open(image_path, "rb") as src, open(output_path, "wb") as dst:
        src.seek(offset)
        remaining = size
        while remaining > 0:
            chunk = src.read(min(remaining, 1024 * 1024))
            if not chunk:
                break
            dst.write(chunk)
            remaining -= len(chunk)


def verify_magic(filepath: str, partition_name: str) -> tuple[bool, str]:
    """Check if a partition file starts with the expected magic bytes.

    Returns (ok, message).
    """
    if partition_name not in MAGIC_CHECKS:
        return True, ""

    expected_bytes, label = MAGIC_CHECKS[partition_name]
    with open(filepath, "rb") as f:
        actual = f.read(len(expected_bytes))

    # TOS check: strip nulls for comparison
    actual_stripped = actual.rstrip(b"\x00")
    expected_stripped = expected_bytes.rstrip(b"\x00")

    if actual_stripped == expected_stripped:
        return True, f"magic '{label}' verified"
    else:
        return False, f"expected magic '{label}', got {actual.hex()}"


def copy_region(src_path: str, dst_path: str, src_offset: int, dst_offset: int, size: int):
    """Copy a region from one file to another (dst must exist and be writable)."""
    with open(src_path, "rb") as src, open(dst_path, "r+b") as dst:
        src.seek(src_offset)
        dst.seek(dst_offset)
        remaining = size
        while remaining > 0:
            chunk = src.read(min(remaining, 1024 * 1024))
            if not chunk:
                break
            dst.write(chunk)
            remaining -= len(chunk)


def write_file_at(file_path: str, dst_path: str, dst_offset: int):
    """Write the contents of a file into dst at the given offset."""
    file_size = os.path.getsize(file_path)
    with open(file_path, "rb") as src, open(dst_path, "r+b") as dst:
        dst.seek(dst_offset)
        remaining = file_size
        while remaining > 0:
            chunk = src.read(min(remaining, 1024 * 1024))
            if not chunk:
                break
            dst.write(chunk)
            remaining -= len(chunk)
