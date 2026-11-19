ZIRAN ?= ziran
LOCKED := $(if $(wildcard ziran.local.toml),,--locked)
.PHONY: test test-zstd test-zstd-wasm zstd-fixture
test:
	$(ZIRAN) build --project $(LOCKED) --target=c --entry inflate_test:Main --exe -o build/tests tests/inflate_test.zi
	env -u DISPLAY -u WAYLAND_DISPLAY build/tests/inflate_test

test-zstd: zstd-fixture
	LDLIBS="$$LDLIBS -lzstd" $(ZIRAN) build --project $(LOCKED) --target=c --entry zstd_native_test:Main --exe -o build/zstd-tests tests/zstd_native_test.zi
	env -u DISPLAY -u WAYLAND_DISPLAY build/zstd-tests/zstd_native_test
	$(ZIRAN) build --project $(LOCKED) --target=c --entry zstd_encode_test:Main --exe -o build/zstd-tests tests/zstd_encode_test.zi
	env -u DISPLAY -u WAYLAND_DISPLAY build/zstd-tests/zstd_encode_test
	python3 tests/zstd_interop.py

zstd-fixture:
	LDLIBS="$$LDLIBS -lzstd" $(ZIRAN) build --project $(LOCKED) --target=c --entry zstd_fixture:Main --exe -o build/zstd-fixture tests/zstd_fixture.zi

test-zstd-wasm:
	mkdir -p build/zstd-wasm
	$(ZIRAN) build --project $(LOCKED) --target=wasm --entry zstd_portable_test:Answer -o build/zstd-wasm/portable.wasm tests/zstd_portable_test.zi
	node tests/zstd_wasm.cjs build/zstd-wasm/portable.wasm
	$(ZIRAN) build --project $(LOCKED) --target=wasm --entry zstd_encode_test:Answer -o build/zstd-wasm/encode.wasm tests/zstd_encode_test.zi
	node tests/zstd_wasm.cjs build/zstd-wasm/encode.wasm
