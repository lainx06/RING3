# RING3 build. clang + ld.lld, target i686-unknown-none-elf (D-03).
#   make                     production kernel (build/kernel.elf): no test programs, quotas fixed in code
#   make TEST=1              test image (build/kernel-test.elf): test programs, `run`/`tele`, quota boot options
#   make KTEST=1             in-kernel unit tests image (implies TEST=1)
#   make POLICY=permissive   permissive policy variant (ARCHITECTURE §7), labelled, different policy hash
#   make host-policy         build/host/policy_test{,_permissive}: the policy source compiled for the host
VERSION    := 0.1.0
BUILD_HASH := $(shell git rev-parse --short HEAD 2>/dev/null || echo nogit)
POLICY     ?= default

CC      := clang
LD      := ld.lld
HOSTCC  ?= cc
OBJCOPY := $(shell command -v llvm-objcopy || command -v llvm-objcopy-18 || echo objcopy)
QEMU    := qemu-system-i386
TARGET  := --target=i686-unknown-none-elf

ifeq ($(KTEST),1)
  TEST := 1
endif
ifeq ($(POLICY),permissive)
  SUF := -permissive
else ifeq ($(POLICY),default)
  SUF :=
else
  $(error POLICY must be default or permissive)
endif

ifeq ($(KTEST),1)
  BUILD := build/ktest$(SUF)
  KERNEL := build/kernel-ktest$(SUF).elf
else ifeq ($(TEST),1)
  BUILD := build/test$(SUF)
  KERNEL := build/kernel-test$(SUF).elf
else
  BUILD := build/obj$(SUF)
  KERNEL := build/kernel$(SUF).elf
endif

# The policy hash covers exactly the policy sources compiled into this image (variant header included).
POLICY_SRC  := kernel/policy/policy.h kernel/policy/policy.c kernel/policy/variant_$(POLICY).h
POLICY_HASH := $(shell cat $(POLICY_SRC) | sha256sum | cut -d' ' -f1)
POLICY_DEFS := -Ikernel/policy -DPOLICY_VARIANT_HEADER='"variant_$(POLICY).h"'

CFLAGS  := $(TARGET) -std=c11 -ffreestanding -nostdlib -fno-pic -fno-stack-protector -mno-sse -mno-mmx -mno-80387 \
           -fno-asynchronous-unwind-tables -O2 -g -Wall -Wextra -Werror -Ikernel/include -I$(BUILD)/gen \
           $(POLICY_DEFS) -DRING3_VERSION=\"$(VERSION)\" -DBUILD_HASH=\"$(BUILD_HASH)\"
ASFLAGS := $(TARGET) -ffreestanding -nostdlib -fno-pic -g -Wall -Wextra -Werror -Ikernel/include
LDFLAGS := -m elf_i386 -nostdlib --gc-sections
ifeq ($(KTEST),1)
  CFLAGS  += -DKTEST=1
  ASFLAGS += -DKTEST=1
endif
ifeq ($(TEST),1)
  CFLAGS  += -DRING3_TEST=1
  ASFLAGS += -DRING3_TEST=1
  UPROGS  := hello kwrite ticks priv spin badptr null hlt ud div0 stack dflag fuzz taint confirmw nocap objs \
             fan multi lcd audspin
else
  UPROGS  :=
endif

KSRC_C := $(wildcard kernel/arch/i386/*.c kernel/core/*.c kernel/lib/*.c kernel/drivers/*.c) kernel/policy/policy.c
KSRC_S := $(wildcard kernel/arch/i386/*.S)
KOBJ   := $(patsubst %.c,$(BUILD)/%.o,$(KSRC_C)) $(patsubst %.S,$(BUILD)/%.o,$(KSRC_S))

# User programs: flat binaries in the D-41 layout, embedded into test images.
UCFLAGS := $(TARGET) -std=c11 -ffreestanding -nostdlib -fno-pic -fno-stack-protector -mno-sse -mno-mmx -mno-80387 \
           -fno-asynchronous-unwind-tables -O2 -g -Wall -Wextra -Werror -Iuser/libu
UBIN    := $(patsubst %,build/user/%.bin,$(UPROGS))

QEMU_BASE := $(QEMU) -m 64 -display none -serial stdio -monitor none \
             -device isa-debug-exit,iobase=0xf4,iosize=0x04 -no-reboot

.PHONY: all clean run test-kernel user host-policy
.SECONDARY: $(patsubst %,build/user/%.elf,$(UPROGS))
all: $(KERNEL)

user: $(UBIN)

$(KERNEL): $(KOBJ) kernel/linker.ld $(BUILD)/user/progs.o
	@mkdir -p $(dir $@)
	$(LD) $(LDFLAGS) -T kernel/linker.ld -o $@ $(KOBJ) $(BUILD)/user/progs.o

$(BUILD)/gen/policy_hash.h: $(POLICY_SRC)
	@mkdir -p $(dir $@)
	@echo '#define POLICY_HASH "$(POLICY_HASH)"' > $@

$(BUILD)/kernel/core/audit.o: $(BUILD)/gen/policy_hash.h

$(BUILD)/%.o: %.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -MMD -MP -c $< -o $@

$(BUILD)/%.o: %.S
	@mkdir -p $(dir $@)
	$(CC) $(ASFLAGS) -MMD -MP -c $< -o $@

$(BUILD)/user/progs.o: user/progs.S $(UBIN)
	@mkdir -p $(dir $@)
	$(CC) $(ASFLAGS) -c $< -o $@

build/user/%.elf: user/test/%.c user/crt0.S user/libu/libu.c user/libu/libu.h user/user.ld
	@mkdir -p $(dir $@)
	$(CC) $(UCFLAGS) -c user/crt0.S -o build/user/crt0.o
	$(CC) $(UCFLAGS) -c user/libu/libu.c -o build/user/libu.o
	$(CC) $(UCFLAGS) -c $< -o build/user/$*.o
	$(LD) -m elf_i386 -nostdlib -T user/user.ld -o $@ build/user/crt0.o build/user/$*.o build/user/libu.o

build/user/%.bin: build/user/%.elf
	$(OBJCOPY) -O binary $< $@

run: $(KERNEL)
	$(QEMU_BASE) -kernel $(KERNEL) $(QEMU_EXTRA)

test-kernel:
	$(MAKE) KTEST=1
	$(QEMU_BASE) -kernel build/kernel-ktest.elf -append "ktest testexit"; rc=$$?; \
	  echo "qemu exit code $$rc"; [ $$rc -eq 33 ]

# Host build of the same policy source (D-28): unit tests + replay, no QEMU.
HOST_CFLAGS := -std=c11 -O1 -g -Wall -Wextra -Werror -Ikernel/policy
host-policy: build/host/policy_test build/host/policy_test_permissive

build/host/policy_test: kernel/policy/policy.c kernel/policy/policy.h kernel/policy/variant_default.h tests/host/policy_test.c
	@mkdir -p $(dir $@)
	$(HOSTCC) $(HOST_CFLAGS) -DPOLICY_VARIANT_HEADER='"variant_default.h"' tests/host/policy_test.c kernel/policy/policy.c -o $@

build/host/policy_test_permissive: kernel/policy/policy.c kernel/policy/policy.h kernel/policy/variant_permissive.h tests/host/policy_test.c
	@mkdir -p $(dir $@)
	$(HOSTCC) $(HOST_CFLAGS) -DPOLICY_VARIANT_HEADER='"variant_permissive.h"' tests/host/policy_test.c kernel/policy/policy.c -o $@

clean:
	rm -rf build

-include $(KOBJ:.o=.d)
