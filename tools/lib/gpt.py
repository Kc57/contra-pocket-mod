"""
GPT (GUID Partition Table) parser.

Reads GPT header and partition entries from a raw disk image.
No third-party dependencies — uses only Python's struct module.
"""

import struct
from dataclasses import dataclass

LBA_SIZE = 512
GPT_HEADER_SIGNATURE = b"EFI PART"
GPT_HEADER_LBA = 1
GPT_ENTRY_SIZE = 128


@dataclass
class Partition:
    name: str
    first_lba: int
    last_lba: int

    @property
    def sector_count(self) -> int:
        return self.last_lba - self.first_lba + 1

    @property
    def size_bytes(self) -> int:
        return self.sector_count * LBA_SIZE

    @property
    def offset_bytes(self) -> int:
        return self.first_lba * LBA_SIZE


def parse_gpt(image_path: str) -> list[Partition]:
    """Parse GPT partition entries from a raw disk image.

    Returns a list of Partition objects sorted by first_lba.
    Only returns entries with a non-empty type GUID (skips unused slots).
    """
    with open(image_path, "rb") as f:
        # Read GPT header at LBA 1
        f.seek(GPT_HEADER_LBA * LBA_SIZE)
        header_data = f.read(92)  # minimum GPT header size

        signature = header_data[0:8]
        if signature != GPT_HEADER_SIGNATURE:
            raise ValueError(
                f"Invalid GPT signature: expected {GPT_HEADER_SIGNATURE!r}, "
                f"got {signature!r}"
            )

        # Header fields we need
        partition_entry_lba = struct.unpack_from("<Q", header_data, 72)[0]
        num_entries = struct.unpack_from("<I", header_data, 80)[0]
        entry_size = struct.unpack_from("<I", header_data, 84)[0]

        # Read all partition entries
        f.seek(partition_entry_lba * LBA_SIZE)
        entries_data = f.read(num_entries * entry_size)

    partitions = []
    empty_guid = b"\x00" * 16

    for i in range(num_entries):
        offset = i * entry_size
        entry = entries_data[offset : offset + entry_size]

        type_guid = entry[0:16]
        if type_guid == empty_guid:
            continue  # unused entry

        first_lba = struct.unpack_from("<Q", entry, 32)[0]
        last_lba = struct.unpack_from("<Q", entry, 40)[0]
        # Name is UTF-16LE, 72 bytes starting at offset 56
        name = entry[56:128].decode("utf-16-le").rstrip("\x00")

        partitions.append(Partition(name=name, first_lba=first_lba, last_lba=last_lba))

    partitions.sort(key=lambda p: p.first_lba)
    return partitions
