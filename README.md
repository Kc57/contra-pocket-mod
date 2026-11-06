# contra-pocket-mod

Firmware modding toolkit for the **My Arcade Contra Pocket Player** handheld. Replaces the stock launcher with a custom game menu that lets you load your own arcade ROMs, then rebuilds the NAND image for flashing back to the device.

<div align="center">
  <a href="https://youtu.be/FkxIYgoh6bo"><img src="images/launcher.gif" width="480" alt="Custom launcher running on the My Arcade Contra Pocket Player"></a>
  <br><br>
  <a href="https://youtu.be/FkxIYgoh6bo"><img src="https://img.shields.io/badge/Watch_the_demo-YouTube-FF0000?style=for-the-badge&logo=youtube&logoColor=white" alt="Watch the demo on YouTube"></a>
</div>

The device runs a Rockchip RK3128 (quad-core ARM Cortex-A7, Mali400 MP2) with 128 MB SPI NAND flash and a 240x320 portrait screen perfect for shmups (shoot 'em ups).

This mod replaces the stock game launcher with a custom MiniGUI application that scans for ROM zips and presents a scrollable game menu. The entire build process is automated. The pipeline extracts your firmware, cross-compiles and injects the launcher, injects your added ROMs, and rebuilds a flashable NAND image.

## What You'll Need

*As an Amazon Associate I earn from qualifying purchases. This section contains an affiliate link. Using it is optional, costs you nothing extra, and helps fund future projects.*

- [My Arcade Contra Pocket Player](https://link.amazon/B0dQU0zL2) (affiliate link): the target device
- A micro-USB cable for dumping and flashing the NAND
- Docker, Python 3.10+, and `rkflashtool`

## Project Structure

```
contra-pocket-mod/
├── docker/              # Dockerfile + compose for cross-compile toolchain
├── apps/
│   ├── launcher/        # Custom game launcher (MiniGUI, C)
│   ├── btntest/         # Button/input test utility
│   └── include/minigui/ # MiniGUI 3.2 headers (see ORIGIN file for provenance)
├── tools/
│   ├── mod.py           # Main entry point - full pipeline
│   ├── extract.py       # Standalone partition extraction
│   ├── rebuild.py       # Standalone NAND rebuild
│   └── lib/             # Helpers (GPT parser, NAND I/O, squashfs)
├── firmware/
│   ├── dumps/           # NAND dumps (not checked in)
│   └── extracted/       # Extracted partitions (not checked in)
├── ROMS/fzip/           # ROM zips + names.cfg (see names.cfg.example)
├── build/               # Build output (not checked in)
└── images/              # README media
```

## Getting Started

1. Clone the repo:

   ```bash
   git clone https://github.com/Kc57/contra-pocket-mod.git
   cd contra-pocket-mod
   ```

2. Build the Docker toolchain (one-time, takes 15-30 minutes):

   ```bash
   cd docker
   docker build -t contra-toolchain .
   cd ..
   ```

3. Install Python dependencies:

   ```bash
   uv sync
   ```

   Or, if you're not using `uv`:

   ```bash
   pip install rich
   ```

4. Install `rkflashtool` for reading/writing the device's NAND flash over USB:

   **macOS (Homebrew):**

   ```bash
   brew install rkflashtool
   ```

   **Linux (build from source):**

   ```bash
   git clone https://github.com/linux-rockchip/rkflashtool.git
   cd rkflashtool
   make
   sudo make install
   ```

## Dump Your Firmware

Connect the device to your PC via the micro-USB cable, then enter RockUSB Mask ROM mode: hold the **Volume Up** button, power on while still holding it.

1. Verify the device is detected:

   ```bash
   rkflashtool v
   ```

2. Dump the full 128 MB NAND:

   ```bash
   sudo rkflashtool r 0 262144 > nand_dump.bin
   ```

3. Copy the dump into the project:

   ```bash
   cp nand_dump.bin firmware/dumps/nand_original.bin
   ```

> **Recommended:** Dump twice and compare hashes to catch read errors. If the hashes don't match, dump again until you get two that agree. Keep a copy of the verified dump somewhere safe outside this repo, it's your only way to restore the device if a flash goes wrong.
>
> ```bash
> sudo rkflashtool r 0 262144 > nand_dump_2.bin
> sha256sum nand_dump.bin nand_dump_2.bin
> ```

## Add ROMs

Place FBA-compatible ROM zips in `ROMS/fzip/`.

The device uses the **FBAlpha (FBA)** libretro core for emulation. Arcade ROM sets are version-specific, so you need ROMs from a FinalBurn Alpha compatible set. If a game fails to load, it is most likely a ROM set version mismatch.

**Space is limited.** The rootfs partition is capped at 65 MiB and the stock firmware already uses about 50 MiB of that. This leaves roughly 15 MiB for the custom launcher, ROMs, and any other modifications. The pipeline will error out if the repacked rootfs exceeds the partition limit, so stick to smaller ROM zips.

You can optionally set display names by copying `ROMS/fzip/names.cfg.example` to `ROMS/fzip/names.cfg` and editing it:

```
# rom_stem=Display Name
1941=1941: Counter Attack
digdug=Dig Dug
truxton2=Truxton II
```

Games without an entry in `names.cfg` display their zip filename.

## Build the Modded Image

Run the pipeline in interactive mode (prompts at each step):

```bash
python3 tools/mod.py
```

Or in headless mode (no prompts):

```bash
python3 tools/mod.py --config mod_config.yaml -y
```

See `mod_config.example.yaml` for the config format. Copy it to `mod_config.yaml` and adjust paths as needed.

The pipeline runs five stages:

1. **Validate** -- checks the NAND dump size and GPT partition structure
2. **Extract** -- extracts partitions and unpacks the rootfs into a Docker volume
3. **Compile** -- cross-compiles the custom launcher using the Docker toolchain
4. **Mod** -- installs the launcher binary and copies ROMs into the rootfs
5. **Rebuild** -- repacks the rootfs and assembles the final NAND image

The output lands at `build/firmware/nand.bin`.

### Why Docker?

Docker serves two purposes in this pipeline. First, it provides the Buildroot 2018.02.12 cross-compilation toolchain with the exact glibc and shared library versions the device expects. Second, it handles all squashfs packing and unpacking -- the device's rootfs contains case-sensitive symlinks that macOS's case-insensitive filesystem corrupts during extraction.

The unpacked rootfs lives in a Docker named volume (`contra-rootfs`), not on your host filesystem. All modifications -- installing the launcher, copying ROMs -- happen inside containers that mount this volume. You won't find the unpacked rootfs anywhere on your host disk. When the pipeline rebuilds, it repacks the rootfs from the volume into a squashfs image and writes the final NAND binary back to `build/firmware/` on the host.

## Flash the Device

1. Enter RockUSB Mask ROM mode: connect via micro-USB, hold **Volume Up**, then power on.
2. Write the modded image:

   ```bash
   sudo rkflashtool w 0 262144 < build/firmware/nand.bin
   ```

3. Reboot the device:

   ```bash
   sudo rkflashtool b
   ```

## Restoring Stock Firmware

If something goes wrong, flash your original backup:

```bash
sudo rkflashtool w 0 262144 < /path/to/your/backup/nand_dump.bin
```

## Notes

- **Standalone tools** -- `tools/extract.py` and `tools/rebuild.py` can be used independently outside the full pipeline.
- **Cleanup** -- remove the Docker volume when you're done: `docker volume rm contra-rootfs`
- **Dry run** -- use `python3 tools/mod.py --dry-run` to preview all actions without executing them.
- **Multiplayer** -- two-player mode via the CO/VS link cable is partially supported but needs more testing. Some games may desync during play, and the multiplayer option currently appears for all games regardless of whether they actually support it.

## License

This project uses two licenses:

- **`apps/`** (the custom launcher, the button test utility, and the vendored MiniGUI headers): [GPL-3.0-or-later](apps/LICENSE). The launcher is built on [MiniGUI](https://github.com/VincentWei/MiniGUI), which is GPL-licensed, so these apps are too.
- **Everything else** (the Python pipeline, the Docker toolchain, and the configuration): [MIT](LICENSE).

## Disclaimer

- This project is not affiliated with, endorsed by, or sponsored by My Arcade or Konami. Contra is a trademark of its respective owner.
- No ROMs or firmware images are included. You must supply your own legally obtained copies.
- Flashing modified firmware can brick your device. Keep a verified backup of your original NAND dump, and use this project at your own risk.
