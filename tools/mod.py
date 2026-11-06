#!/usr/bin/env python3
"""
Contra Pocket firmware modding tool.

Unified script that guides the user through firmware extraction, modification,
and rebuilding. Supports interactive mode (guided prompts) and headless mode
via a YAML config file.

Usage:
    python3 tools/mod.py                        # interactive mode
    python3 tools/mod.py --config config.yaml   # headless from config
    python3 tools/mod.py --dry-run              # show actions without executing
"""

import argparse
import glob
import os
import shutil
import subprocess

import time
from contextlib import contextmanager

import rich.box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.table import Table

from lib.gpt import LBA_SIZE, parse_gpt
from lib.nand import (
    EXPECTED_IMAGE_SIZE,
    FILENAME_MAP,
    copy_region,
    validate_firmware,
    write_file_at,
)
from lib.squashfs import (
    VOLUME_NAME,
    SquashFSError,
    ensure_volume,
    repack_rootfs,
    stage_copy_in,
    stage_copy_tree,
    stage_exists,
    stage_rename,
    unsquash_rootfs,
)

# ---------------------------------------------------------------------------
# Console & project paths
# ---------------------------------------------------------------------------

console = Console()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)

DEFAULT_FIRMWARE_DUMP = os.path.join(PROJECT_DIR, "firmware", "dumps", "nand_original.bin")
DEFAULT_OUTPUT_DIR = os.path.join(PROJECT_DIR, "build", "firmware")
DEFAULT_LAUNCHER_BIN = os.path.join(PROJECT_DIR, "build", "apps", "game")
DEFAULT_ROM_SOURCE = os.path.join(PROJECT_DIR, "ROMS", "fzip")
DOCKER_COMPOSE_FILE = os.path.join(PROJECT_DIR, "docker", "compose.yaml")

# Paths within staged rootfs
ROOTFS_GAME_BIN = os.path.join("usr", "bin", "game")
ROOTFS_GAME_ORIGINAL = os.path.join("usr", "bin", "game_original")
ROOTFS_FBA_ROMS = os.path.join("usr", "lib", "libretro", "games", "fzip")


def _relpath(path: str) -> str:
    """Return a project-relative path string for display."""
    return os.path.relpath(path, PROJECT_DIR)


@contextmanager
def _timed():
    """Context manager that yields a callable returning elapsed seconds."""
    start = time.monotonic()
    yield lambda: time.monotonic() - start


def _has_mods(config: dict) -> bool:
    """Check if any modifications are selected."""
    mods = config.get("mods", {})
    if mods.get("install_launcher"):
        return True
    rom_cfg = mods.get("roms", {})
    if isinstance(rom_cfg, dict) and rom_cfg.get("source_dir"):
        return True
    return False


# ---------------------------------------------------------------------------
# Minimal YAML reader/writer (stdlib only, handles our simple config format)
# ---------------------------------------------------------------------------

def yaml_load(path: str) -> dict:
    """Parse a simple YAML file (up to two levels of nesting)."""
    config = {}
    section = None       # top-level dict key (2-space indent)
    subsection = None    # second-level dict key (4-space indent)

    with open(path) as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            # 4-space indent = second level
            if line.startswith("    ") and section is not None and subsection is not None:
                key, _, value = stripped.partition(":")
                config[section][subsection][key.strip()] = _yaml_cast(value.strip())
                continue

            # 2-space indent = first level
            if line.startswith("  ") and section is not None:
                key, _, value = stripped.partition(":")
                key = key.strip()
                value = value.strip()
                if not value:
                    subsection = key
                    config[section][key] = {}
                else:
                    subsection = None
                    config[section][key] = _yaml_cast(value)
                continue

            # Top level
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()

            if not value:
                section = key
                subsection = None
                config[key] = {}
            else:
                section = None
                subsection = None
                config[key] = _yaml_cast(value)

    return config


def _yaml_cast(value: str):
    """Cast a YAML string value to appropriate Python type."""
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.lower() in ("null", "~", ""):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    return value


def yaml_dump(config: dict, path: str):
    """Write a simple YAML config file (up to two levels of nesting)."""
    with open(path, "w") as f:
        f.write("# contra-pocket-mod configuration\n")
        for key, value in config.items():
            if isinstance(value, dict):
                f.write(f"{key}:\n")
                for k, v in value.items():
                    if isinstance(v, dict):
                        f.write(f"  {k}:\n")
                        for k2, v2 in v.items():
                            f.write(f"    {k2}: {_yaml_format(v2)}\n")
                    else:
                        f.write(f"  {k}: {_yaml_format(v)}\n")
            else:
                f.write(f"{key}: {_yaml_format(value)}\n")


def _yaml_format(value) -> str:
    """Format a Python value as a YAML string."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


# ---------------------------------------------------------------------------
# Stage functions
# ---------------------------------------------------------------------------

def stage_validate(config: dict) -> dict:
    """Validate the firmware dump. Returns partition info on success."""
    firmware_path = config["firmware_dump"]

    valid, errors = validate_firmware(firmware_path)
    if not valid:
        console.print("[red]✗[/] Firmware validation failed:")
        for err in errors:
            console.print(f"  [dim]{err}[/]")
        raise SystemExit(1)

    partitions = parse_gpt(firmware_path)
    console.print(f"[green]✓[/] Valid Contra Pocket firmware ({len(partitions)} partitions)")

    table = Table(show_header=True, show_edge=False, pad_edge=False, box=rich.box.SIMPLE, padding=(0, 2))
    table.add_column("Partition", style="cyan")
    table.add_column("Size", justify="right", style="dim")
    for p in partitions:
        table.add_row(p.name, f"{p.size_bytes / (1024 * 1024):.1f} MiB")
    console.print(table)

    return {"partitions": partitions}


def stage_extract(config: dict, firmware_info: dict, dry_run: bool = False):
    """Extract rootfs from firmware dump into the Docker named volume."""
    firmware_path = config["firmware_dump"]
    partitions = firmware_info["partitions"]

    rootfs_part = next((p for p in partitions if p.name == "rootfs"), None)
    if not rootfs_part:
        console.print("[red]✗[/] No rootfs partition found in firmware")
        raise SystemExit(1)

    if dry_run:
        console.print(f"[dim]Would extract rootfs to Docker volume[/] [cyan]{VOLUME_NAME}[/]")
        return

    ensure_volume()

    extracted_dir = os.path.join(PROJECT_DIR, "firmware", "extracted")
    os.makedirs(extracted_dir, exist_ok=True)
    tmp_squashfs = os.path.join(extracted_dir, "rootfs.squashfs")

    try:
        from lib.nand import extract_partition

        with _timed() as elapsed:
            with console.status("Extracting rootfs..."):
                extract_partition(firmware_path, tmp_squashfs, rootfs_part.offset_bytes, rootfs_part.size_bytes)
                success, detail = unsquash_rootfs(tmp_squashfs)

        if not success:
            console.print(f"[red]✗[/] Failed to extract rootfs")
            if detail:
                console.print(f"  [dim]{detail.strip()}[/]")
            raise SystemExit(1)
    except SquashFSError as e:
        console.print(f"[red]✗[/] {e}")
        raise SystemExit(1)
    finally:
        if os.path.exists(tmp_squashfs):
            os.unlink(tmp_squashfs)

    console.print(f"[green]✓[/] Rootfs extracted to Docker volume [cyan]{VOLUME_NAME}[/] [dim]({elapsed():.1f}s)[/]")


def _check_docker() -> bool:
    """Check if Docker is installed and the daemon is running."""
    if not shutil.which("docker"):
        return False
    result = subprocess.run(
        ["docker", "info"],
        capture_output=True,
    )
    return result.returncode == 0


def stage_compile(config: dict, dry_run: bool = False):
    """Compile the launcher binary using Docker toolchain."""
    if not config.get("compile_launcher"):
        return

    if dry_run:
        console.print("[dim]Would compile launcher via Docker toolchain[/]")
        return

    # Verify Docker is accessible before proceeding
    while not _check_docker():
        console.print("[red]✗[/] Docker is not accessible (is Docker Desktop running?)")
        if not Confirm.ask("  Retry?", default=True):
            raise SystemExit(1)

    console.print("[green]✓[/] Docker is running")

    # Check if image exists
    result = subprocess.run(
        ["docker", "image", "inspect", "contra-toolchain"],
        capture_output=True,
    )

    if result.returncode != 0:
        console.print("[yellow]⚠[/] Docker image 'contra-toolchain' not found")
        with console.status("Building Docker toolchain image..."):
            build_result = subprocess.run(
                ["docker", "compose", "-f", DOCKER_COMPOSE_FILE, "build"],
                cwd=PROJECT_DIR,
                capture_output=True,
            )
        if build_result.returncode != 0:
            console.print("[red]✗[/] Failed to build Docker image")
            if config.get("_verbose") and build_result.stderr:
                console.print(f"  [dim]{build_result.stderr.decode(errors='replace').strip()}[/]")
            raise SystemExit(1)
        console.print("[green]✓[/] Docker image built")

    with _timed() as elapsed:
        with console.status("Compiling launcher..."):
            compile_result = subprocess.run(
                [
                    "docker", "compose", "-f", DOCKER_COMPOSE_FILE,
                    "run", "--rm", "toolchain",
                    "make", "-C", "apps/launcher",
                ],
                cwd=PROJECT_DIR,
                capture_output=True,
            )

    if compile_result.returncode != 0:
        console.print("[red]✗[/] Compilation failed")
        if config.get("_verbose") and compile_result.stderr:
            console.print(f"  [dim]{compile_result.stderr.decode(errors='replace').strip()}[/]")
        raise SystemExit(1)
    else:
        console.print(f"[green]✓[/] Launcher compiled [dim]({elapsed():.1f}s)[/]")


def stage_mod(config: dict, dry_run: bool = False):
    """Apply selected modifications to staged rootfs."""
    mods = config.get("mods", {})

    if mods.get("install_launcher"):
        _mod_install_launcher(dry_run)

    rom_source = mods.get("roms", {})
    source_dir = rom_source.get("source_dir") if isinstance(rom_source, dict) else None
    if source_dir:
        _mod_add_roms(source_dir, dry_run)


def _mod_install_launcher(dry_run: bool = False):
    """Copy custom launcher binary into staged rootfs via Docker volume."""
    if dry_run:
        console.print(f"[dim]Would install launcher to:[/] [cyan]{ROOTFS_GAME_BIN}[/]")
        if not os.path.isfile(DEFAULT_LAUNCHER_BIN):
            console.print(f"[yellow]⚠[/] Launcher binary not yet built: [dim]{_relpath(DEFAULT_LAUNCHER_BIN)}[/]")
        return

    if not os.path.isfile(DEFAULT_LAUNCHER_BIN):
        console.print(f"[red]✗[/] Launcher binary not found: [cyan]{_relpath(DEFAULT_LAUNCHER_BIN)}[/]")
        console.print("  [dim]Build it first or enable compile_launcher in config[/]")
        raise SystemExit(1)

    # Preserve original binary
    if stage_exists(ROOTFS_GAME_BIN) and not stage_exists(ROOTFS_GAME_ORIGINAL):
        stage_rename(ROOTFS_GAME_BIN, ROOTFS_GAME_ORIGINAL)
        console.print(f"[green]✓[/] Original preserved: [cyan]{ROOTFS_GAME_BIN}[/] [dim]→[/] [cyan]{ROOTFS_GAME_ORIGINAL}[/]")

    stage_copy_in(DEFAULT_LAUNCHER_BIN, ROOTFS_GAME_BIN, mode="755")
    console.print("[green]✓[/] Launcher installed")


def _mod_add_roms(source_dir: str, dry_run: bool = False):
    """Copy FBA ROM files into staged rootfs via Docker volume."""
    if not os.path.isdir(source_dir):
        console.print(f"[red]✗[/] ROM source directory not found: [cyan]{source_dir}[/]")
        raise SystemExit(1)

    rom_files = glob.glob(os.path.join(source_dir, "*.zip"))

    if not rom_files:
        console.print(f"[yellow]⚠[/] No .zip ROM files found in: [cyan]{source_dir}[/]")
        return

    names_cfg = os.path.join(source_dir, "names.cfg")
    has_names_cfg = os.path.isfile(names_cfg)

    if dry_run:
        console.print(f"[dim]Would copy {len(rom_files)} ROM(s) to:[/] [cyan]{ROOTFS_FBA_ROMS}[/]")
        for rom in rom_files:
            console.print(f"  [dim]• {os.path.basename(rom)}[/]")
        if has_names_cfg:
            console.print("[dim]Would also copy names.cfg[/]")
        return

    with console.status("Copying ROMs to Docker volume..."):
        stage_copy_tree(source_dir, ROOTFS_FBA_ROMS, glob="*.zip")

    if has_names_cfg:
        stage_copy_in(names_cfg, os.path.join(ROOTFS_FBA_ROMS, "names.cfg"))
        console.print("[green]✓[/] names.cfg copied")

    console.print(f"[green]✓[/] {len(rom_files)} ROM(s) copied to [cyan]{ROOTFS_FBA_ROMS}[/]")


def stage_rebuild(config: dict, firmware_info: dict, dry_run: bool = False):
    """Rebuild firmware image from staged modifications."""
    firmware_path = config["firmware_dump"]
    output_dir = config["output_dir"]
    partitions = firmware_info["partitions"]

    output_nand = os.path.join(output_dir, "nand.bin")
    output_squashfs = os.path.join(output_dir, "rootfs.squashfs")

    if dry_run:
        console.print(f"[dim]Would repack rootfs from Docker volume[/] [cyan]{VOLUME_NAME}[/] [dim]→[/] [cyan]rootfs.squashfs[/]")
        console.print(f"[dim]Would rebuild NAND:[/]  [cyan]{_relpath(output_nand)}[/]")
        return

    os.makedirs(output_dir, exist_ok=True)

    # Step 1: Repack rootfs from Docker volume
    try:
        with _timed() as repack_elapsed:
            with console.status("Repacking rootfs..."):
                repack_rootfs(output_squashfs)
    except SquashFSError as e:
        console.print(f"[red]✗[/] {e}")
        raise SystemExit(1)

    rootfs_part = next((p for p in partitions if p.name == "rootfs"), None)
    if rootfs_part:
        actual_size = os.path.getsize(output_squashfs)
        max_size = rootfs_part.size_bytes
        pct = actual_size * 100 // max_size
        console.print(f"[green]✓[/] Rootfs repacked: [dim]{actual_size:,} / {max_size:,} bytes ({pct}%)[/] [dim]({repack_elapsed():.1f}s)[/]")
        if actual_size > max_size:
            console.print(f"[red]✗[/] Rootfs exceeds partition limit ({max_size:,} bytes)")
            raise SystemExit(1)

    # Step 2: Rebuild NAND image
    image_size = os.path.getsize(firmware_path)
    total_sectors = image_size // LBA_SIZE

    with _timed() as build_elapsed:
        with console.status("Building NAND image..."):
            with open(output_nand, "wb") as f:
                f.write(b"\x00" * image_size)

            first_partition_lba = partitions[0].first_lba if partitions else 34
            gpt_sectors = 34

            copy_region(firmware_path, output_nand, 0, 0, gpt_sectors * LBA_SIZE)

            if first_partition_lba > gpt_sectors:
                gap = first_partition_lba - gpt_sectors
                copy_region(
                    firmware_path, output_nand,
                    gpt_sectors * LBA_SIZE, gpt_sectors * LBA_SIZE,
                    gap * LBA_SIZE,
                )

            for p in partitions:
                if p.name == "rootfs":
                    write_file_at(output_squashfs, output_nand, p.offset_bytes)
                else:
                    copy_region(
                        firmware_path, output_nand,
                        p.offset_bytes, p.offset_bytes,
                        p.size_bytes,
                    )

            last_partition = partitions[-1]
            backup_gpt_start = last_partition.last_lba + 1
            backup_gpt_size = (total_sectors - backup_gpt_start) * LBA_SIZE

            if backup_gpt_size > 0:
                copy_region(
                    firmware_path, output_nand,
                    backup_gpt_start * LBA_SIZE, backup_gpt_start * LBA_SIZE,
                    backup_gpt_size,
                )

    nand_size = os.path.getsize(output_nand)
    nand_mib = nand_size / (1024 * 1024)
    console.print(f"[green]✓[/] NAND image rebuilt [dim]({nand_size:,} bytes / {nand_mib:.1f} MiB)[/] [dim]({build_elapsed():.1f}s)[/]")


# ---------------------------------------------------------------------------
# Gather configuration (interactive or from file)
# ---------------------------------------------------------------------------

def gather_interactive() -> dict:
    """Collect all configuration via interactive prompts."""
    console.print()
    console.print(Panel(
        "[bold]Contra Pocket Firmware Mod Tool[/]",
        style="cyan",
        expand=False,
    ))

    # --- Step 1: Firmware ---
    console.print(Rule("[bold]Step 1 — Firmware[/]"))
    firmware_dump = Prompt.ask(
        "  Firmware dump path",
        default=_relpath(DEFAULT_FIRMWARE_DUMP),
    )
    if not os.path.isabs(firmware_dump):
        firmware_dump = os.path.join(PROJECT_DIR, firmware_dump)

    valid, errors = validate_firmware(firmware_dump)
    if not valid:
        console.print("[red]✗[/] Firmware validation failed:")
        for err in errors:
            console.print(f"  [dim]{err}[/]")
        raise SystemExit(1)

    partitions = parse_gpt(firmware_dump)
    console.print(f"[green]✓[/] Valid firmware ({len(partitions)} partitions)")

    table = Table(show_header=True, show_edge=False, pad_edge=False, box=rich.box.SIMPLE, padding=(0, 2))
    table.add_column("Partition", style="cyan")
    table.add_column("Size", justify="right", style="dim")
    for p in partitions:
        table.add_row(p.name, f"{p.size_bytes / (1024 * 1024):.1f} MiB")
    console.print(table)

    # --- Step 2: Staging ---
    console.print(Rule("[bold]Step 2 — Staging[/]"))
    console.print(f"  Rootfs will be staged in Docker volume [cyan]{VOLUME_NAME}[/]")

    # --- Step 3: Compilation ---
    console.print(Rule("[bold]Step 3 — Compilation[/]"))
    launcher_exists = os.path.isfile(DEFAULT_LAUNCHER_BIN)
    if launcher_exists:
        console.print(f"[green]✓[/] Launcher binary found: [cyan]{_relpath(DEFAULT_LAUNCHER_BIN)}[/]")
        compile_launcher = Confirm.ask("  Recompile launcher?", default=False)
    else:
        console.print("[yellow]⚠[/] No launcher binary found")
        compile_launcher = Confirm.ask("  Compile launcher via Docker?", default=True)

    # --- Step 4: Modifications ---
    console.print(Rule("[bold]Step 4 — Modifications[/]"))
    install_launcher = Confirm.ask("  Install custom launcher?", default=True)

    rom_source_dir = None
    add_roms = Confirm.ask("  Add FBA ROMs?", default=False)
    if add_roms:
        rom_source_dir = Prompt.ask("  ROM source directory", default=_relpath(DEFAULT_ROM_SOURCE)).strip()
        if rom_source_dir and not os.path.isabs(rom_source_dir):
            rom_source_dir = os.path.abspath(rom_source_dir)
        if not rom_source_dir:
            rom_source_dir = None

    # Show ROM feedback immediately
    if rom_source_dir:
        rom_files = glob.glob(os.path.join(rom_source_dir, "*.zip"))
        if rom_files:
            console.print(f"  [green]✓[/] Found {len(rom_files)} ROM(s):")
            for rom in rom_files[:10]:
                console.print(f"    [dim]• {os.path.basename(rom)}[/]")
            if len(rom_files) > 10:
                console.print(f"    [dim]... and {len(rom_files) - 10} more[/]")
            if os.path.isfile(os.path.join(rom_source_dir, "names.cfg")):
                console.print("  [green]✓[/] Found names.cfg (display name mappings)")
        else:
            console.print(f"  [yellow]⚠[/] No .zip files found in: [cyan]{rom_source_dir}[/]")
            rom_source_dir = None

    # --- Step 5: Output ---
    console.print(Rule("[bold]Step 5 — Output[/]"))
    output_dir = Prompt.ask(
        "  Output directory",
        default=_relpath(DEFAULT_OUTPUT_DIR),
    )
    if not os.path.isabs(output_dir):
        output_dir = os.path.join(PROJECT_DIR, output_dir)

    config = {
        "firmware_dump": firmware_dump,
        "output_dir": output_dir,
        "compile_launcher": compile_launcher,
        "mods": {
            "install_launcher": install_launcher,
            "roms": {
                "source_dir": rom_source_dir,
            } if rom_source_dir else {},
        },
        "_firmware_info": {"partitions": partitions},
    }

    return config


def gather_from_config(config_path: str) -> dict:
    """Load configuration from a YAML file."""
    if not os.path.isfile(config_path):
        console.print(f"[red]✗[/] Config file not found: [cyan]{config_path}[/]")
        raise SystemExit(1)

    config = yaml_load(config_path)

    for key in ("firmware_dump", "output_dir"):
        if key in config and not os.path.isabs(config[key]):
            config[key] = os.path.join(PROJECT_DIR, config[key])

    if "mods" not in config:
        config["mods"] = {}

    roms = config.get("mods", {}).get("roms", {})
    if isinstance(roms, dict) and roms.get("source_dir"):
        if not os.path.isabs(roms["source_dir"]):
            roms["source_dir"] = os.path.abspath(roms["source_dir"])

    return config


# ---------------------------------------------------------------------------
# Summary, confirmation, and execution
# ---------------------------------------------------------------------------

def show_summary(config: dict, dry_run: bool = False):
    """Display a summary of planned actions in a panel."""
    output_dir = config["output_dir"]
    mods = config.get("mods", {})

    lines = []

    # Paths
    lines.append(f"[bold]Firmware[/]      [cyan]{_relpath(config['firmware_dump'])}[/]")
    lines.append(f"[bold]Docker volume[/] [cyan]{VOLUME_NAME}[/]")
    lines.append(f"[bold]Output[/]        [cyan]{_relpath(output_dir)}[/]")
    lines.append("")

    # Steps
    lines.append("[bold]Steps[/]")
    lines.append("  1. Extract rootfs to Docker volume")

    if config.get("compile_launcher"):
        lines.append("  2. Compile launcher via Docker toolchain")
    else:
        lines.append("  2. [dim]Skip compilation[/]")

    mod_items = []
    if mods.get("install_launcher"):
        mod_items.append("Install custom launcher")
    rom_cfg = mods.get("roms", {})
    if isinstance(rom_cfg, dict) and rom_cfg.get("source_dir"):
        rom_count = len(glob.glob(os.path.join(rom_cfg["source_dir"], "*.zip")))
        rom_label = f"Add {rom_count} FBA ROM(s)"
        if os.path.isfile(os.path.join(rom_cfg["source_dir"], "names.cfg")):
            rom_label += " + names.cfg"
        mod_items.append(rom_label)

    if mod_items:
        lines.append("  3. Apply modifications:")
        for m in mod_items:
            lines.append(f"     • {m}")
    else:
        lines.append("  3. [dim]No modifications selected[/]")

    lines.append("  4. Rebuild firmware image")

    # Warnings
    warnings = []

    output_nand = os.path.join(output_dir, "nand.bin")
    output_squashfs = os.path.join(output_dir, "rootfs.squashfs")
    if os.path.isfile(output_nand):
        warnings.append(f"Will overwrite: [cyan]{_relpath(output_nand)}[/]")
    if os.path.isfile(output_squashfs):
        warnings.append(f"Will overwrite: [cyan]{_relpath(output_squashfs)}[/]")

    if mods.get("install_launcher"):
        warnings.append(f"Will rename original: [cyan]{ROOTFS_GAME_BIN}[/] → [cyan]{ROOTFS_GAME_ORIGINAL}[/]")

    if warnings:
        lines.append("")
        for w in warnings:
            lines.append(f"[bold yellow]⚠[/] {w}")

    # Panel
    title = "[bold yellow]Action Summary — DRY RUN[/]" if dry_run else "[bold]Action Summary[/]"
    border_style = "yellow" if dry_run else "cyan"

    console.print()
    console.print(Panel(
        "\n".join(lines),
        title=title,
        border_style=border_style,
        padding=(1, 2),
    ))


def execute(config: dict, dry_run: bool = False, firmware_info: dict | None = None):
    """Run all stages."""
    if not dry_run:
        ensure_volume()

    # Stage 1: Validate
    console.print(Rule("[bold]1/5 Validate Firmware[/]"))
    if firmware_info:
        console.print(f"[green]✓[/] Firmware already validated ({len(firmware_info['partitions'])} partitions)")
    else:
        firmware_info = stage_validate(config)

    # Stage 2: Extract
    console.print(Rule("[bold]2/5 Extract Rootfs[/]"))
    stage_extract(config, firmware_info, dry_run)

    # Stage 3: Compile
    console.print(Rule("[bold]3/5 Compile Launcher[/]"))
    if config.get("compile_launcher"):
        stage_compile(config, dry_run)
    else:
        console.print("[dim]Skipped[/]")

    # Stage 4: Mod
    console.print(Rule("[bold]4/5 Apply Modifications[/]"))
    stage_mod(config, dry_run)

    # Stage 5: Rebuild
    console.print(Rule("[bold]5/5 Rebuild Firmware[/]"))
    stage_rebuild(config, firmware_info, dry_run)

    # Final result
    console.print()
    if dry_run:
        console.print(Panel(
            "[yellow]No changes were made[/]",
            title="[bold yellow]Dry Run Complete[/]",
            border_style="yellow",
            expand=False,
            padding=(1, 2),
        ))
    else:
        output_nand = os.path.join(config["output_dir"], "nand.bin")
        size = os.path.getsize(output_nand)
        size_mib = size / (1024 * 1024)
        console.print(Panel(
            f"[green]✓[/] [cyan]{_relpath(output_nand)}[/]\n[dim]  {size:,} bytes ({size_mib:.1f} MiB)[/]",
            title="[bold green]Complete[/]",
            border_style="green",
            expand=False,
            padding=(1, 2),
        ))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Contra Pocket firmware modding tool",
    )
    parser.add_argument(
        "--config",
        help="Path to YAML config file (skip interactive prompts)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned actions without executing",
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip confirmation prompt (for automated workflows)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show detailed output from subprocesses",
    )
    args = parser.parse_args()

    # Gather configuration
    if args.config:
        config = gather_from_config(args.config)
    else:
        config = gather_interactive()

    config["_verbose"] = args.verbose

    # Show summary and confirm
    show_summary(config, args.dry_run)

    # Warn if no modifications selected
    if not _has_mods(config):
        console.print()
        console.print("[bold yellow]⚠[/]  No modifications selected — output will be identical to original firmware.")

    # Offer to save config (interactive only, after summary)
    if not args.config:
        console.print()
        if Confirm.ask("  Save configuration to YAML?", default=False):
            config_path = Prompt.ask("  Config file path", default="mod_config.yaml")
            if not os.path.isabs(config_path):
                config_path = os.path.join(PROJECT_DIR, config_path)
            save_config = {k: v for k, v in config.items() if not k.startswith("_")}
            yaml_dump(save_config, config_path)
            console.print(f"[green]✓[/] Saved: [cyan]{_relpath(config_path)}[/]")

    # Confirm before execution
    if not args.yes:
        console.print()
        if not Confirm.ask("  Proceed?", default=True):
            console.print("[dim]Aborted.[/]")
            raise SystemExit(0)

    # Execute
    firmware_info = config.pop("_firmware_info", None)
    execute(config, args.dry_run, firmware_info=firmware_info)


if __name__ == "__main__":
    main()
