ZIRAN ?= ziran
LOCKED := $(if $(wildcard ziran.local.toml),,--locked)
.PHONY: test
test:
	$(ZIRAN) build --project $(LOCKED) --target=c --entry inflate_test:Main --exe -o build/tests tests/inflate_test.zi
	env -u DISPLAY -u WAYLAND_DISPLAY build/tests/inflate_test
