# firmware/Makefile: host tests and Uno compile for the RING3 work cell (T-06). Independent of the top-level
# Makefile.
#   make -C firmware test      host-compile frame.cpp + tests and cell_rules.h tests (clang++, ASan/UBSan),
#                              run them, check the
#                              committed link vectors, AVR warning check of frame.cpp, pins.h consistency
#   make -C firmware vectors   regenerate test/link_vectors.txt
#   make -C firmware compile   arduino-cli compile both sketches for arduino:avr:uno (never uploads)

HOSTCXX ?= clang++
HOSTFLAGS := -std=c++17 -Wall -Wextra -Werror -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer
BUILD := ../build/firmware
BIN := $(BUILD)/test_frame
RULES := $(BUILD)/test_rules
SRC := ring3cell/frame.cpp test/test_frame.cpp
HDR := ring3cell/frame.h ring3cell/protocol.h

FQBN := arduino:avr:uno
ARDUINO_CLI ?= arduino-cli
# avr-g++ from the arduino:avr core (the same compiler arduino-cli uses); optional for `test`.
AVRCXX ?= $(firstword $(shell ls -d $(HOME)/.arduino15/packages/arduino/tools/avr-gcc/*/bin/avr-g++ 2>/dev/null) \
                      $(shell command -v avr-g++ 2>/dev/null))
AVRFLAGS := -mmcu=atmega328p -DF_CPU=16000000L -std=gnu++11 -Os -Wall -Wextra -Werror -c -o /dev/null

.PHONY: test vectors compile avrcheck pins clean

test: $(BIN) $(RULES) avrcheck pins
	$(BIN)
	$(RULES)
	$(BIN) --vectors | diff -u test/link_vectors.txt - && echo "link_vectors.txt matches"

$(BIN): $(SRC) $(HDR)
	@mkdir -p $(BUILD)
	$(HOSTCXX) $(HOSTFLAGS) -o $@ $(SRC)

$(RULES): test/test_rules.cpp ring3cell/cell_rules.h
	@mkdir -p $(BUILD)
	$(HOSTCXX) $(HOSTFLAGS) -o $@ test/test_rules.cpp

vectors: $(BIN)
	$(BIN) --vectors > test/link_vectors.txt

avrcheck:
	@if [ -n "$(AVRCXX)" ]; then \
	  echo "$(AVRCXX) $(AVRFLAGS) ring3cell/frame.cpp"; \
	  $(AVRCXX) $(AVRFLAGS) ring3cell/frame.cpp && echo "frame.cpp: no AVR warnings"; \
	else echo "avrcheck: avr-g++ not found, skipped"; fi

pins:
	@if [ -f ring3cell/pins.h ] && [ -f bringup/pins.h ]; then \
	  cmp ring3cell/pins.h bringup/pins.h && echo "pins.h identical in bringup/ and ring3cell/"; \
	else echo "pins: ring3cell/pins.h or bringup/pins.h missing, skipped"; fi

compile:
	$(ARDUINO_CLI) compile --fqbn $(FQBN) --warnings all bringup
	@if [ -f ring3cell/ring3cell.ino ]; then \
	  echo "$(ARDUINO_CLI) compile --fqbn $(FQBN) --warnings all ring3cell"; \
	  $(ARDUINO_CLI) compile --fqbn $(FQBN) --warnings all ring3cell; \
	else echo "compile: ring3cell/ring3cell.ino not written yet, skipped"; fi

clean:
	rm -rf $(BUILD)
