CROSS_COMPILE ?= arm-buildroot-linux-gnueabihf-
CC = $(CROSS_COMPILE)gcc
STRIP = $(CROSS_COMPILE)strip
OBJCOPY = $(CROSS_COMPILE)objcopy

SYSROOT ?= /opt/buildroot/output/host/arm-buildroot-linux-gnueabihf/sysroot
DEVICE_LIBS ?= /staging/rootfs/usr/lib
BUILD_DIR ?= ../../build/apps

CFLAGS = -march=armv7-a -mfpu=neon-vfpv4 -mfloat-abi=hard -mtune=cortex-a7 -O2
CFLAGS += -Wall -Wextra -Wno-unused-parameter
CFLAGS += --sysroot=$(SYSROOT)
CFLAGS += -I../include/minigui -Isrc

LDFLAGS = --sysroot=$(SYSROOT)
LDFLAGS += -L$(DEVICE_LIBS)
LDFLAGS += -Wl,-rpath-link,$(DEVICE_LIBS)
LDFLAGS += -lminigui_ths -lfreetype -lpng12 -ljpeg -ldrm -lpixman-1 -lz -lpthread -lm -ldl

SRCS = src/main.c
OBJS = $(SRCS:.c=.o)
TARGET = $(BUILD_DIR)/btntest

all: $(TARGET)

$(TARGET): $(OBJS)
	@mkdir -p $(BUILD_DIR)
	$(CC) $(CFLAGS) -o $@ $^ $(LDFLAGS)
	$(OBJCOPY) --remove-section=.note.ABI-tag $@
	$(STRIP) $@

src/%.o: src/%.c
	$(CC) $(CFLAGS) -c -o $@ $<

clean:
	rm -f $(OBJS) $(TARGET)

.PHONY: all clean
