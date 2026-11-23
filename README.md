# Compression

`compression/inflate` decodes raw DEFLATE and gzip into caller-owned buffers.

## Zstandard

`compression/zstd` is a pure Ziran decoder for one dictionary-free [Zstandard frame](https://www.rfc-editor.org/rfc/rfc8878.html). It supports raw, RLE and compressed blocks, frames with or without a declared content size, and XXH64 checksum validation. Provide a disjoint output buffer and an explicit maximum window in bytes. Output capacity is the expansion limit. The decoder uses already decoded output as match history and needs at most 128 KiB of heap scratch for literals. The result reports the number of bytes written on success. Discard the output on failure.

```zi
using Zstd :: #import "compression/zstd"

ReadAsset :: (frame: []u8, asset: []u8) -> bool {
    result := Zstd.ZstdDecode(frame, asset, 32 * 1024 * 1024)
    return result.problem == .None && result.count == asset.count
}
```

`compression/zstd_frame` exposes `ZstdReadHeader` for inspecting a frame's window, content size, checksum flag and dictionary ID before allocating output. Header inspection checks framing only through the end of the header. Apply application limits before using those fields to allocate memory.

`compression/zstd_encode` writes a dictionary-free frame in pure Ziran, with a declared content size and checksum. `ZstdEncodeBound` covers any frame the encoder may write for an input of up to 4 GiB minus one byte. Give the encoder disjoint input and output slices. A smaller output slice is allowed and returns `.OutputLimit` if the frame does not fit. Discard output on failure.

```zi
using Encode :: #import "compression/zstd_encode"

CompressAsset :: (asset: []u8, output: []u8) -> s64 {
    result := Encode.ZstdEncode(asset, output, .High)
    if result.problem != .None { return -1 }
    return result.count
}
```

`.Balanced` has a window of up to 2 MiB and about 10 MiB of heap scratch for large inputs. `.High` is the default and uses a window of up to 16 MiB, longer-prefix matching and adaptive sequence tables, with about 73 MiB of heap scratch for large inputs. Both use Huffman literals when beneficial and emit raw or RLE blocks when they are smaller. These are package presets with their own speed and compression ratios. The native API exposes libzstd's numbered levels.

`compression/zstd_native` is an opt-in C target binding. Import it and link with `-lzstd` on targets that supply the library. The pure Ziran modules build without libzstd. [libzstd API manual](https://meta.github.io/zstd/doc/api_manual_latest.html)

`ZstdEncodeBound` returns the capacity needed for `ZstdEncode`. Encoding defaults to level 22 with the frame checksum and content size enabled. The native decoder has the same output and window limits as the portable decoder. Use `ZstdDecodeWithDictionary` when the frame and formatted dictionary advertise the same nonzero ID. Both decoders take exactly one standard frame and reject trailing bytes, concatenated frames and skippable frames.

Use `ZstdDecodeFrames` for a body containing one or more complete frames. It accepts skippable frames and applies the same window limit to every data frame. The output capacity bounds the sum of all decoded data. The sequence API requires dictionary-free frames and rejects corrupt, incomplete or unrecognized input. Use the result only when `problem` is `.None`.

`ZstdOpenEncoder` creates a reusable streaming context with an explicit level and maximum window in bits (use `23` for 8 MiB). Each successful `ZstdWriteEncoder` step reports how much input it consumed and how much output to emit. Call it again with the unconsumed tail when `done` is false. Supply a nonempty output buffer disjoint from the input. Use `ZstdFlushEncoder` to emit pending bytes while keeping the frame open, or `ZstdFinishEncoder` to complete the checksummed frame. Repeat either call with fresh output space until `done` is true. `ZstdResetEncoder` discards the current frame and keeps the settings and context for reuse. Keep each context under one caller's ownership and release it with `ZstdFreeEncoder`.

The format tables carry the IETF attribution and Revised BSD notice in [zstd_tables.zi](src/zstd_tables.zi). The optional native binding uses the system library under its upstream terms. [libzstd BSD license](https://github.com/meta/zstd/blob/v1.5.7/LICENSE) · [libzstd GPLv2 terms](https://github.com/meta/zstd/blob/v1.5.7/COPYING)

## Local tests and benchmarks

`make test` exercises gzip. `make test-zstd` tests native and pure round trips and uses the `zstd` CLI for interoperability. `make test-zstd-wasm` runs pure encoding, decoding and bounds fixtures in Node.js. Build the benchmark driver with `make zstd-fixture`. Run `python3 bench/zstd.py LABEL=PATH_TO_WASM` to measure the reference codecs, then run `python3 bench/zstd.py --complete-pure` in the same output directory to compare both pure presets using those exact bytes. The driver verifies decoded output against the input hash.

For example, on the local Jungle and Breeze WASM builds used during development, the measured size and encode times were as follows. These are single-run wall times.

| Asset | Pure High size | Pure High time | libzstd Ultra-22 size | libzstd Ultra-22 time |
| --- | ---: | ---: | ---: | ---: |
| Jungle | 4,182,760 B | 0.745 s | 3,797,400 B | 2.412 s |
| Breeze | 62,683,590 B | 16.714 s | 61,615,106 B | 26.589 s |
