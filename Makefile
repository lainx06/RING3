# RING3 build. clang + ld.lld, target i686-unknown-none-elf (D-03).
VERSION    := 0.1.0
BUILD_HASH := $(shell git rev-parse --short HEAD 2>/dev/null || echo nogit)

CC      := clang
LD      := ld.lld
OBJCOPY := $(shell command -v llvm-objcopy || command -v llvm-objcopy-18 || echo objcopy)
QEMU    := qemu-system-i386
TARGET  := --target=i686-unknown-none-elf

CFLAGS  := $(TARGET) -std=c11 -ffreestanding -nostdlib -fno-pic -fno-stack-protector -mno-sse -mno-mmx -mno-80387 \
           -fno-asynchronous-unwind-tables -O2 -g -Wall -Wextra -Werror -Ikernel/include \
           -DRING3_VERSION=\"$(VERSION)\" -DBUILD_HASH=\"$(BUILD_HASH)\"
ASFLAGS := $(TARGET) -ffreestanding -nostdlib -fno-pic -g -Wall -Wextra -Werror -Ikernel/include
LDFLAGS := -m elf_i386 -nostdlib --gc-sections

ifeq ($(KTEST),1)
  CFLAGS  += -DKTEST=1
  ASFLAGS += -DKTEST=1
  BUILD := build/ktest
  KERNEL := build/kernel-ktest.elf
else
  BUILD := build/obj
  KERNEL := build/kernel.elf
endif

KSRC_C := $(wildcard kernel/arch/i386/*.c kernel/core/*.c kernel/lib/*.c kernel/drivers/*.c)
KSRC_S := $(wildcard kernel/arch/i386/*.S)
KOBJ   := $(patsubst %.c,$(BUILD)/%.o,$(KSRC_C)) $(patsubst %.S,$(BUILD)/%.o,$(KSRC_S))

# User programs: flat binaries in the D-41 layout, embedded into the kernel image.
UPROGS  := hello kwrite ticks priv spin badptr
UCFLAGS := $(TARGET) -std=c11 -ffreestanding -nostdlib -fno-pic -fno-stack-protector -mno-sse -mno-mmx -mno-80387 \
           -fno-asynchronous-unwind-tables -O2 -g -Wall -Wextra -Werror -Iuser/libu
UBIN    := $(patsubst %,build/user/%.bin,$(UPROGS))

QEMU_BASE := $(QEMU) -m 64 -display none -serial stdio -monitor none \
             -device isa-debug-exit,iobase=0xf4,iosize=0x04 -no-reboot

.PHONY: all clean run test-kernel user
.SECONDARY: $(patsubst %,build/user/%.elf,$(UPROGS))
all: $(KERNEL)

user: $(UBIN)

$(KERNEL): $(KOBJ) kernel/linker.ld $(BUILD)/user/progs.o
	@mkdir -p $(dir $@)
	$(LD) $(LDFLAGS) -T kernel/linker.ld -o $@ $(KOBJ) $(BUILD)/user/progs.o

$(BUILD)/%.o: %.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -MMD -MP -c $< -o $@

$(BUILD)/%.o: %.S
	@mkdir -p $(dir $@)
	$(CC) $(ASFLAGS) -MMD -MP -c $< -o $@

$(BUILD)/user/progs.o: user/progs.S $(UBIN)
	@mkdir -p $(dir $@)
	$(CC) $(ASFLAGS) -c $< -o $@

build/user/%.elf: user/test/%.c user/crt0.S user/libu/libu.c user/user.ld
	@mkdir -p $(dir $@)
	$(CC) $(UCFLAGS) -c user/crt0.S -o build/user/crt0.o
	$(CC) $(UCFLAGS) -c user/libu/libu.c -o build/user/libu.o
	$(CC) $(UCFLAGS) -c $< -o build/user/$*.o
	$(LD) -m elf_i386 -nostdlib -T user/user.ld -o $@ build/user/crt0.o build/user/$*.o build/user/libu.o

build/user/%.bin: build/user/%.elf
	$(OBJCOPY) -O binary $< $@

run: $(KERNEL)
	$(QEMU) -kernel $(KERNEL) -m 64 -display none -serial stdio -monitor none \
	  -device isa-debug-exit,iobase=0xf4,iosize=0x04 -no-reboot $(QEMU_EXTRA)

test-kernel:
	$(MAKE) KTEST=1
	$(QEMU_BASE) -kernel build/kernel-ktest.elf -append "ktest testexit"; rc=$$?; \
	  echo "qemu exit code $$rc"; [ $$rc -eq 33 ]

clean:
	rm -rf build

-include $(KOBJ:.o=.d)
