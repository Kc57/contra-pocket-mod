"""
SquashFS pack/unpack operations using a Docker named volume.

The rootfs is staged inside a Docker named volume ('contra-rootfs') to
preserve symlinks — macOS virtiofs bind mounts corrupt them.  All
mutations go through disposable containers that mount the volume.
"""

import os
import subprocess
from pathlib import Path

SQUASHFS_BLOCK_SIZE = 131072

# Project root is two directories up from this file (tools/lib/squashfs.py)
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])

DOCKER_IMAGE = "contra-toolchain"

# Named volume for staging rootfs (ext4 inside Docker VM — symlink-safe)
VOLUME_NAME = "contra-rootfs"
STAGING_MOUNT = "/staging"
STAGING_ROOTFS = "/staging/rootfs"


class SquashFSError(RuntimeError):
    """Raised when a SquashFS operation fails."""


# ---------------------------------------------------------------------------
# Volume lifecycle
# ---------------------------------------------------------------------------

def volume_exists() -> bool:
    """Check if the contra-rootfs Docker volume exists."""
    result = subprocess.run(
        ["docker", "volume", "inspect", VOLUME_NAME],
        capture_output=True,
    )
    return result.returncode == 0


def ensure_volume():
    """Create the Docker named volume if it doesn't already exist."""
    subprocess.run(
        ["docker", "volume", "create", VOLUME_NAME],
        capture_output=True,
        check=True,
    )


def destroy_volume():
    """Remove the Docker named volume."""
    subprocess.run(
        ["docker", "volume", "rm", VOLUME_NAME],
        capture_output=True,
        check=True,
    )


# ---------------------------------------------------------------------------
# Staging file operations (each runs a disposable container)
# ---------------------------------------------------------------------------

def _run_staging(cmd: str, extra_volumes: list[str] | None = None,
                 volume_readonly: bool = False) -> subprocess.CompletedProcess:
    """Run a shell command in a disposable container with the staging volume."""
    vol_spec = f"{VOLUME_NAME}:{STAGING_MOUNT}{':ro' if volume_readonly else ''}"
    docker_cmd = [
        "docker", "run", "--rm",
        "-v", vol_spec,
    ]
    if extra_volumes:
        for v in extra_volumes:
            docker_cmd.extend(["-v", v])
    docker_cmd.extend([DOCKER_IMAGE, "sh", "-c", cmd])
    return subprocess.run(docker_cmd, capture_output=True, text=True)


def stage_copy_in(host_path: str, rootfs_dest: str, mode: str | None = None):
    """Copy a file from the host into the staging rootfs.

    host_path:   absolute path on the host
    rootfs_dest: path relative to rootfs root (e.g. 'usr/bin/game')
    mode:        optional chmod mode string (e.g. '755')
    """
    host_path = os.path.abspath(host_path)
    parent = os.path.dirname(host_path)
    filename = os.path.basename(host_path)

    dest_dir = os.path.dirname(rootfs_dest)
    dest_file = f"{STAGING_ROOTFS}/{rootfs_dest}"

    cmd_parts = []
    if dest_dir:
        cmd_parts.append(f"mkdir -p {STAGING_ROOTFS}/{dest_dir}")
    cmd_parts.append(f"cp /src/{filename} {dest_file}")
    if mode:
        cmd_parts.append(f"chmod {mode} {dest_file}")

    result = _run_staging(
        " && ".join(cmd_parts),
        extra_volumes=[f"{parent}:/src:ro"],
    )
    if result.returncode != 0:
        raise SquashFSError(f"stage_copy_in failed: {result.stderr}")


def stage_copy_tree(host_dir: str, rootfs_dest: str, glob: str = "*"):
    """Copy files matching a glob from host_dir into the staging rootfs.

    host_dir:    absolute path to source directory on the host
    rootfs_dest: path relative to rootfs root (e.g. 'usr/lib/libretro/games/fzip')
    glob:        shell glob pattern (default '*')
    """
    host_dir = os.path.abspath(host_dir)
    dest = f"{STAGING_ROOTFS}/{rootfs_dest}"

    cmd = f"mkdir -p {dest} && cp /src/{glob} {dest}/"
    result = _run_staging(
        cmd,
        extra_volumes=[f"{host_dir}:/src:ro"],
    )
    if result.returncode != 0:
        raise SquashFSError(f"stage_copy_tree failed: {result.stderr}")


def stage_exists(rootfs_path: str) -> bool:
    """Check if a path exists inside the staging rootfs.

    rootfs_path: path relative to rootfs root (e.g. 'usr/bin/game')
    """
    result = _run_staging(f"test -e {STAGING_ROOTFS}/{rootfs_path}")
    return result.returncode == 0


def stage_rename(src: str, dst: str):
    """Rename/move a file within the staging rootfs.

    src, dst: paths relative to rootfs root
    """
    result = _run_staging(
        f"mv {STAGING_ROOTFS}/{src} {STAGING_ROOTFS}/{dst}"
    )
    if result.returncode != 0:
        raise SquashFSError(f"stage_rename failed: {result.stderr}")


# ---------------------------------------------------------------------------
# SquashFS operations
# ---------------------------------------------------------------------------

def unsquash_rootfs(squashfs_path: str, output_dir: str = None) -> tuple[bool, str]:
    """Unsquash a SquashFS image into the Docker named volume.

    The rootfs is extracted to /staging/rootfs inside the volume.
    output_dir is accepted for backward compat but ignored.

    Returns (success, detail_message).
    """
    squashfs_path = os.path.abspath(squashfs_path)
    parent = os.path.dirname(squashfs_path)
    filename = os.path.basename(squashfs_path)

    ensure_volume()

    result = _run_staging(
        f"rm -rf {STAGING_ROOTFS} && unsquashfs -d {STAGING_ROOTFS} /src/{filename}",
        extra_volumes=[f"{parent}:/src:ro"],
    )

    if result.returncode != 0:
        # unsquashfs may return non-zero but still succeed (e.g., device node errors)
        if "created" in result.stdout.lower():
            return True, result.stdout
        return False, result.stderr

    return True, result.stdout


def repack_rootfs(output_squashfs: str, rootfs_dir: str = None) -> str:
    """Repack the staging rootfs from the Docker volume into a SquashFS image.

    The rootfs is read from /staging/rootfs inside the volume.
    rootfs_dir is accepted for backward compat but ignored.

    Returns the output path on success.
    Raises SquashFSError on failure.
    """
    output_squashfs = os.path.abspath(output_squashfs)
    parent = os.path.dirname(output_squashfs)
    filename = os.path.basename(output_squashfs)

    os.makedirs(parent, exist_ok=True)

    result = _run_staging(
        f"mksquashfs {STAGING_ROOTFS} /out/{filename}"
        f" -comp gzip -b {SQUASHFS_BLOCK_SIZE} -noappend -no-xattrs",
        extra_volumes=[f"{parent}:/out"],
        volume_readonly=True,
    )

    if result.returncode != 0:
        raise SquashFSError(f"mksquashfs failed:\n{result.stderr}")

    return output_squashfs
