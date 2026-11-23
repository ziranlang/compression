#!/usr/bin/env python3
"""Cross-check compression and decompression with the reference Zstandard CLI."""
import random
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory


def call(*command, **kwargs):
    return subprocess.run(command, capture_output=True, check=True, **kwargs)


def main():
    rng = random.Random(8878)
    mixed = b"".join(rng.choices([b"function", b"lexicon", b"index", b"\\x00", b"x"],
                                 weights=[10, 3, 2, 14, 1], k=30000))
    payloads = [b"", b"x", b"z" * 268002, rng.randbytes(133737), mixed,
                (rng.randbytes(13777) + b"Zstandard" * 513) * 24]
    entropy = bytes(rng.choices(range(256), weights=[(symbol + 1) ** 3 for symbol in range(256)], k=132000))
    long_codes = bytes(rng.choices(range(256), weights=[max(1, 1 << (20 - symbol // 8)) if symbol < 168 else 1
                                                        for symbol in range(256)], k=131073))
    encoder_payloads = [entropy, long_codes, *[mixed[:n] for n in [31, 32, 255, 256, 65791, 65792, 131071, 131072]]]
    with TemporaryDirectory(prefix="interop-", dir="build/zstd-fixture") as temporary:
        directory = Path(temporary)
        payload = directory / "data"
        frame = directory / "frame.zst"
        result = directory / "decoded"
        fixture = Path("build/zstd-fixture/zstd_fixture")

        def decode(kind, expected, dictionary=None):
            args = [] if dictionary is None else [dictionary]
            call(fixture, kind, frame, result, str(len(expected)), "1", *args)
            assert result.read_bytes() == expected

        def refuse(kind, expected_size, expected_problem, dictionary=None):
            args = [] if dictionary is None else [dictionary]
            refused = subprocess.run([fixture, kind, frame, result, str(expected_size), "1", *args],
                                     capture_output=True)
            assert refused.returncode == 5 and refused.stdout == f"problem={expected_problem}\n".encode(), refused

        for data in payloads:
            payload.write_bytes(data)
            for options in [["-1"], ["-9", "--no-check"], ["--ultra", "-22"]]:
                call("zstd", "-q", "-f", "--single-thread", *options, payload, "-o", frame)
                decode("native", data)
                decode("portable", data)
        for data in [*payloads, *encoder_payloads]:
            payload.write_bytes(data)
            for mode in ["encode", "balanced", "high"]:
                assert b"ns=" in call(fixture, mode, payload, frame, "22", "1").stdout
                assert call("zstd", "-q", "-d", "-c", frame).stdout == data
                decode("portable", data)
                if mode != "encode" and data == entropy:
                    encoded = frame.read_bytes()
                    # This single-segment frame has a four-byte content size.
                    assert (encoded[9] >> 1) & 3 == 2
                    assert encoded[12] & 3 == 2
                    assert encoded[17] < 128  # FSE-compressed Huffman weights.
                    jump_table = 18 + encoded[17]
                    first_stream_size = int.from_bytes(encoded[jump_table:jump_table + 2], 'little')
                    assert first_stream_size > 0
                    broken = bytearray(encoded)
                    broken[jump_table + 6 + first_stream_size - 1] = 0
                    frame.write_bytes(broken)
                    refuse("portable", len(data), 2)
                if mode != "encode" and data == mixed:
                    assert frame.stat().st_size < len(data) // 2
                    compressed = frame.read_bytes()
                    frame.write_bytes(compressed[:-1])
                    refuse("portable", len(data), 1)
                    frame.write_bytes(compressed[:-1] + bytes([compressed[-1] ^ 1]))
                    refuse("portable", len(data), 7)

        # stdin streaming omits the content size. Also cover separately produced
        # RLE and raw blocks, independent of the reference encoder's choices.
        frame.write_bytes(call("zstd", "-q", "-c", input=mixed).stdout)
        decode("native", mixed)
        decode("portable", mixed)
        refuse("native", len(mixed) - 1, 5)
        refuse("portable", len(mixed) - 1, 5)
        for data, block_type, block in [(b"raw", 0, b"raw"), (b"R" * 13, 1, b"R")]:
            frame.write_bytes(bytes.fromhex("28 b5 2f fd 20") + bytes([len(data)]) +
                              ((len(data) << 3) | (block_type << 1) | 1).to_bytes(3, "little") + block)
            assert call("zstd", "-q", "-d", "-c", frame).stdout == data
            decode("portable", data)
        standard = frame.read_bytes()
        frame.write_bytes(standard + standard)
        refuse("native", 26, 3)
        refuse("portable", 26, 3)
        frame.write_bytes(bytes.fromhex("50 2a 4d 18 01 00 00 00 ff"))
        refuse("native", 0, 2)
        refuse("portable", 0, 2)

        skipped = frame.read_bytes()
        first = call("zstd", "-q", "-c", input=mixed).stdout
        second = call("zstd", "-q", "-c", input=entropy).stdout
        sequence = first + skipped + second
        expected = mixed + entropy
        frame.write_bytes(sequence)
        assert call("zstd", "-q", "-d", "-c", frame).stdout == expected
        decode("frames", expected)
        refuse("frames", len(expected) - 1, 5)
        frame.write_bytes(sequence[:-1] + bytes([sequence[-1] ^ 1]))
        refuse("frames", len(expected), 7)

        samples = []
        for index in range(28):
            sample = directory / f"sample-{index:02}"
            sample.write_bytes(rng.choice(payloads[2:])[index:index + 4096] + mixed[index * 70:index * 70 + 1000])
            samples.append(sample)
        dictionary = directory / "dictionary"
        wrong = directory / "wrong"
        for path, identifier in [(dictionary, 1101), (wrong, 1102)]:
            call("zstd", "-q", "--train", f"--dictID={identifier}", "--maxdict=4096", *samples, "-o", path)
        payload.write_bytes(mixed[:9991])
        call("zstd", "-q", "-f", "-D", dictionary, payload, "-o", frame)
        decode("native", mixed[:9991], dictionary)
        refuse("native", 9991, 6)
        refuse("native", 9991, 6, wrong)
        refuse("portable", 9991, 6)
    print("Zstandard CLI interoperability passed (pure encode, Ultra-22, Huffman, raw/RLE, frame sequences and dictionaries)")


if __name__ == "__main__":
    main()
