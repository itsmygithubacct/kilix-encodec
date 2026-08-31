PROJECT := kilix-encodec
BUILD ?= build
PREFIX ?= /usr/local
DESTDIR ?=

CC ?= cc
AR ?= ar
INSTALL ?= install
PYTHON ?= python3
PKG_CONFIG ?= pkg-config

CPPFLAGS += -Iinclude
CFLAGS ?= -O2 -g
CFLAGS += -std=c11 -fPIC -Wall -Wextra -Wpedantic -Wconversion -Wshadow \
	-Wstrict-prototypes -Wmissing-prototypes -Wformat=2 -Werror
LDFLAGS ?=

LIB_SOURCES := \
	src/context.c \
	src/encoder.c \
	src/decoder.c \
	src/rvq.c \
	src/packet.c \
	src/onnx.c
LIB_OBJECTS := $(LIB_SOURCES:src/%.c=$(BUILD)/%.o)
LIB_DEPS := $(LIB_OBJECTS:.o=.d)
TEST_NAMES := packet rvq stream model
TEST_BINS := $(TEST_NAMES:%=$(BUILD)/test-%)

STATIC_LIB := $(BUILD)/lib$(PROJECT).a
SHARED_LIB := $(BUILD)/lib$(PROJECT).so.0
SHARED_LINK := $(BUILD)/lib$(PROJECT).so
COMMAND := $(BUILD)/kenc
PKG_CONFIG_FILE := $(BUILD)/$(PROJECT).pc

.DEFAULT_GOAL := all

.PHONY: all clean export-env export-test install install-test sanitize test

all: $(STATIC_LIB) $(SHARED_LIB) $(SHARED_LINK) $(COMMAND) $(PKG_CONFIG_FILE)

$(BUILD):
	mkdir -p $@

$(BUILD)/%.o: src/%.c include/kilix_encodec.h | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) -MMD -MP -c $< -o $@

$(STATIC_LIB): $(LIB_OBJECTS)
	$(AR) rcs $@ $^

$(SHARED_LIB): $(LIB_OBJECTS)
	$(CC) -shared -Wl,-soname,lib$(PROJECT).so.0 $(LDFLAGS) -o $@ $^

$(SHARED_LINK): $(SHARED_LIB)
	ln -sfn $(notdir $(SHARED_LIB)) $@

$(COMMAND): tools/kenc.c $(STATIC_LIB) | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ $< $(STATIC_LIB)

$(PKG_CONFIG_FILE): kilix-encodec.pc.in VERSION | $(BUILD)
	sed 's|@PREFIX@|$(PREFIX)|g' $< > $@

$(BUILD)/test-%: tests/test_%.c tests/test.h $(STATIC_LIB) | $(BUILD)
	$(CC) $(CPPFLAGS) -Itests $(CFLAGS) $(LDFLAGS) -o $@ $< $(STATIC_LIB)

test: all $(TEST_BINS)
	@set -eu; passed=0; total=$(words $(TEST_BINS)); \
	for binary in $(TEST_BINS); do \
		"$$binary"; \
		passed=$$((passed + 1)); \
	done; \
	$(COMMAND) --selftest; \
	$(PYTHON) tools/verify_export.py --skeleton \
		models/encodec-24khz-v1/manifest.json; \
	$(PYTHON) tools/verify_export.py --self-test; \
	$(PYTHON) tools/export_24khz.py --version; \
	printf 'kilix-encodec test binaries: %s/%s PASS\n' "$$passed" "$$total"

export-env:
	uv sync --frozen --group export

export-test:
	@test -n "$(CHECKPOINT)" || \
		{ printf '%s\n' 'CHECKPOINT is required'; exit 2; }
	@test -n "$(OUTPUT_DIR)" || \
		{ printf '%s\n' 'OUTPUT_DIR is required'; exit 2; }
	uv run --frozen --group export python tools/export_24khz.py \
		--checkpoint "$(CHECKPOINT)" --output-dir "$(OUTPUT_DIR)"
	uv run --frozen --group export python tools/verify_export.py \
		--bundle "$(OUTPUT_DIR)" --checkpoint "$(CHECKPOINT)"

sanitize:
	$(MAKE) clean BUILD=build-sanitize
	$(MAKE) test BUILD=build-sanitize \
		CFLAGS='-O1 -g3 -std=c11 -fPIC -Wall -Wextra -Wpedantic -Wconversion -Wshadow -Wstrict-prototypes -Wmissing-prototypes -Wformat=2 -Werror -fsanitize=address,undefined -fno-omit-frame-pointer' \
		LDFLAGS='-fsanitize=address,undefined'

install: all
	$(INSTALL) -d $(DESTDIR)$(PREFIX)/include $(DESTDIR)$(PREFIX)/lib \
		$(DESTDIR)$(PREFIX)/lib/pkgconfig $(DESTDIR)$(PREFIX)/bin \
		$(DESTDIR)$(PREFIX)/share/doc/$(PROJECT)
	$(INSTALL) -m 0644 include/kilix_encodec.h $(DESTDIR)$(PREFIX)/include/
	$(INSTALL) -m 0644 $(STATIC_LIB) $(DESTDIR)$(PREFIX)/lib/
	$(INSTALL) -m 0755 $(SHARED_LIB) $(DESTDIR)$(PREFIX)/lib/
	ln -sfn lib$(PROJECT).so.0 \
		$(DESTDIR)$(PREFIX)/lib/lib$(PROJECT).so
	$(INSTALL) -m 0644 $(PKG_CONFIG_FILE) \
		$(DESTDIR)$(PREFIX)/lib/pkgconfig/
	$(INSTALL) -m 0755 $(COMMAND) $(DESTDIR)$(PREFIX)/bin/
	$(INSTALL) -m 0644 LICENSE THIRD-PARTY-NOTICES.md README.md \
		$(DESTDIR)$(PREFIX)/share/doc/$(PROJECT)/

install-test: all
	rm -rf $(BUILD)/install-root
	$(MAKE) install DESTDIR=$(abspath $(BUILD)/install-root)
	@set -eu; \
	pc_path=$(abspath $(BUILD)/install-root$(PREFIX)/lib/pkgconfig); \
	flags=$$(PKG_CONFIG_PATH="$$pc_path" \
		$(PKG_CONFIG) --define-prefix --cflags --libs $(PROJECT)); \
	$(CC) $(CFLAGS) -o $(BUILD)/test-install tests/test_install.c $$flags
	LD_LIBRARY_PATH=$(abspath $(BUILD)/install-root$(PREFIX)/lib) \
		$(BUILD)/test-install

clean:
	rm -rf $(BUILD)

-include $(LIB_DEPS)
