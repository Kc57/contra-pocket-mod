# =============================================================================
# Contra Pocket Player Cross-Compile Toolchain
# Target: Rockchip RK3128 (ARM Cortex-A7, glibc 2.26)
# Toolchain: Buildroot 2018.02.12, GCC 6.4.0, glibc 2.26
# Base: Ubuntu 18.04 (era-matched to avoid build incompatibilities)
# =============================================================================

FROM ubuntu:18.04

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# ---- Step 1: Fix Ubuntu 18.04 EOL package mirrors ----
# Ubuntu 18.04 reached EOL April 2023; mirrors moved to old-releases.
RUN sed -i 's/archive.ubuntu.com/old-releases.ubuntu.com/g' /etc/apt/sources.list && \
    sed -i 's/security.ubuntu.com/old-releases.ubuntu.com/g' /etc/apt/sources.list

# ---- Step 2: Install host dependencies ----
# These are the packages Buildroot needs to bootstrap the cross-toolchain.
RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    g++ \
    make \
    patch \
    wget \
    curl \
    cpio \
    unzip \
    rsync \
    bc \
    libncurses-dev \
    python \
    git \
    file \
    perl \
    squashfs-tools \
    && rm -rf /var/lib/apt/lists/*

# ---- Step 3: Clone Buildroot 2018.02.12 ----
# 2018.02.12 is the last bugfix release in the 2018.02 series.
# It natively ships glibc 2.26 and GCC 6.4.0 — no overrides needed.
RUN git clone --depth 1 --branch 2018.02.12 \
    https://github.com/buildroot/buildroot.git /opt/buildroot

WORKDIR /opt/buildroot

# ---- Step 4: Create contra defconfig ----
# Minimal config: ARM Cortex-A7, hard-float, glibc, C++ enabled.
# GCC defaults to 6.4.0. Add BR2_GCC_VERSION_7_X=y for GCC 7.3.0.
RUN printf 'BR2_arm=y\n\
BR2_cortex_a7=y\n\
BR2_ARM_EABIHF=y\n\
BR2_ARM_FPU_VFPV4D16=y\n\
BR2_ARM_INSTRUCTIONS_ARM=y\n\
BR2_TOOLCHAIN_BUILDROOT_GLIBC=y\n\
BR2_TOOLCHAIN_BUILDROOT_CXX=y\n' \
    > configs/contra_defconfig

# ---- Step 5: Build the cross-toolchain ----
# `make toolchain` builds only the cross-compiler (gcc, binutils, glibc) —
# not a full rootfs. Takes ~15-30 minutes depending on machine.
RUN make contra_defconfig && make toolchain -j$(nproc)

# ---- Step 6: Add toolchain to PATH ----
ENV PATH="/opt/buildroot/output/host/bin:${PATH}"
ENV CROSS_COMPILE="arm-buildroot-linux-gnueabihf-"
ENV SYSROOT="/opt/buildroot/output/host/arm-buildroot-linux-gnueabihf/sysroot"

# ---- Step 7: Set up working directory ----
WORKDIR /work

# Default command: show toolchain version
CMD ["arm-buildroot-linux-gnueabihf-gcc", "--version"]
