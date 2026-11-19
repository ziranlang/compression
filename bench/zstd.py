#!/usr/bin/env python3
"""Compare bounded Zstandard decoding and the size of exact WASM assets."""

import argparse
import gzip
import hashlib
import json
import os
import platform
import random
import shutil
import statistics
import subprocess
import tempfile
import time
from pathlib import Path


def timed(command):
    start = time.perf_counter_ns()
    with tempfile.TemporaryFile() as stdout:
        process = subprocess.Popen(command, stdout=stdout)
        _, status, usage = os.wait4(process.pid, 0)
        process.returncode = os.waitstatus_to_exitcode(status)
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, command)
        elapsed = time.perf_counter_ns() - start
        stdout.seek(0)
        return {"wall_ns": elapsed, "peak_kib": usage.ru_maxrss, "output": stdout.read(2048).decode().strip()}


def digest(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def frame_digest(path):
    return {"bytes": path.stat().st_size, "sha256": digest(path)}


def summarize(samples, field):
    values = [sample[field] for sample in samples]
    q1, _, q3 = statistics.quantiles(values, n=4, method='inclusive')
    iqr = q3 - q1
    excluded = [i for i, value in enumerate(values) if value < q1 - 1.5 * iqr or value > q3 + 1.5 * iqr]
    return {"median": statistics.median(v for i, v in enumerate(values) if i not in excluded),
            "excluded_indices": excluded}


def decode_samples(fixture, frame, expected, output, count):
    modes = ['native', 'portable']
    samples = {mode: [] for mode in modes}
    for mode in modes:
        timed([fixture, mode, frame, output, str(expected['bytes']), '1'])
        if frame_digest(output) != expected:
            raise ValueError(f'{mode} decoder output differs from the input asset')
    rng = random.Random(8878)
    for _ in range(count):
        rng.shuffle(modes)
        for mode in modes:
            sample = timed([fixture, mode, frame, output, str(expected['bytes']), '1'])
            fields = dict(field.split('=') for field in sample.pop('output').split())
            sample['codec_ns'] = int(fields['ns'])
            assert int(fields['bytes']) == expected['bytes']
            samples[mode].append(sample)
    if frame_digest(output) != expected:
        raise ValueError('decoder output differs from the input asset')
    return {mode: {"samples": values, **{field: summarize(values, field)
                                       for field in ['wall_ns', 'codec_ns', 'peak_kib']}}
            for mode, values in samples.items()}


def node_codec_samples(codec, frame, expected, decoded, count):
    # Node exercises the platform gzip and Brotli implementations. Codec time
    # excludes reading the compressed file and writing the comparison output.
    decode = """
const fs = require('node:fs');
const zlib = require('node:zlib');
const [codec, input, output, capacity] = process.argv.slice(1);
const frame = fs.readFileSync(input);
const method = codec === 'gzip9' ? zlib.gunzipSync : zlib.brotliDecompressSync;
const start = process.hrtime.bigint();
const bytes = method(frame, {maxOutputLength: Number(capacity)});
console.log(String(process.hrtime.bigint() - start));
fs.writeFileSync(output, bytes);
"""
    samples = []
    command = ['node', '-e', decode, codec, frame, decoded, str(expected['bytes'])]
    timed(command)
    for _ in range(count):
        sample = timed(command)
        sample['codec_ns'] = int(sample.pop('output'))
        samples.append(sample)
    if frame_digest(decoded) != expected:
        raise ValueError(f'{codec} decoder output differs from the input asset')
    return {"samples": samples, **{field: summarize(samples, field)
                                   for field in ['wall_ns', 'codec_ns', 'peak_kib']}}


def pure_asset(fixture, reference, directory, label, asset, expected, count):
    decoded = directory / 'pure-decoded.wasm'
    formats = {}
    for mode in ['balanced', 'high']:
        frame = directory / f'{label}-pure-{mode}.zst'
        encode = timed([fixture, mode, asset, frame, '0', '1'])
        timed([*reference, '-d', frame, '-o', decoded])
        if frame_digest(decoded) != expected:
            raise ValueError('reference decoder output differs from the input asset')
        decode = decode_samples(fixture, frame, expected, decoded, count)
        entry = {**frame_digest(frame), 'encode': encode, 'decode': decode}
        formats[f'pure_{mode}'] = entry
        print(label, f'pure_{mode}', entry['bytes'], 'bytes',
              round(encode['wall_ns'] / 1e9, 3), 's', encode['peak_kib'], 'KiB', flush=True)
    return formats


def gzip_file(asset, output):
    with asset.open('rb') as source, gzip.GzipFile(filename="", fileobj=output, mode='wb', mtime=0) as encoded:
        shutil.copyfileobj(source, encoded, length=262144)


def brotli_file(asset, output):
    import brotli
    with asset.open('rb') as source:
        encoder = brotli.Compressor(quality=11)
        for chunk in iter(lambda: source.read(262144), b''):
            output.write(encoder.process(chunk))
        output.write(encoder.finish())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size-worker', nargs=3, metavar=('CODEC', 'ASSET', 'OUTPUT'), help=argparse.SUPPRESS)
    parser.add_argument('--out', type=Path, default=Path('build/zstd-bench'))
    parser.add_argument('--samples', type=int, default=9)
    parser.add_argument('--mbps', type=float, default=20)
    parser.add_argument('--fast-only', action='store_true', help='Only measure decoding of Zstd level 3')
    parser.add_argument('--complete-pure', action='store_true', help='Measure both pure encoders against the saved reference run')
    parser.add_argument('--complete-codecs', action='store_true', help='Measure gzip and Brotli decoding from a saved full run')
    parser.add_argument('assets', nargs='*', metavar='LABEL=WASM')
    args = parser.parse_args()
    if args.size_worker:
        codec, asset, output = args.size_worker
        with Path(output).open('wb') as target:
            (gzip_file if codec == 'gzip9' else brotli_file)(Path(asset), target)
        return
    if args.samples < 2 or args.mbps <= 0 or not (args.assets or args.complete_codecs or args.complete_pure):
        parser.error('supply assets, two or more samples and a positive transfer speed')
    directory = args.out
    directory.mkdir(parents=True, exist_ok=True)
    fixture = Path('build/zstd-fixture/zstd_fixture')
    if args.complete_pure:
        stored = directory / 'receipt.json'
        receipt = json.loads(stored.read_text())
        receipt['pure_fixture'] = frame_digest(fixture)
        reference = ['zstd', '-q', '-f', '--single-thread']
        for label, result in receipt['assets'].items():
            expected = {key: result['input'][key] for key in ['bytes', 'sha256']}
            asset = Path(result['input']['path'])
            if frame_digest(asset) != expected:
                raise ValueError('the app build replaced the input from the reference run')
            for name, entry in pure_asset(fixture, reference, directory, label, asset, expected, args.samples).items():
                entry['transfer_ms'] = entry['bytes'] * 8 / (receipt['mbps'] * 1000)
                result['formats'][name] = entry
            if frame_digest(asset) != expected:
                raise ValueError('the app build replaced an input during measurement')
            stored.write_text(json.dumps(receipt, indent=2) + '\n')
        return
    if args.complete_codecs:
        stored = directory / 'receipt.json'
        receipt = json.loads(stored.read_text())
        for label, asset in receipt['assets'].items():
            expected = {key: asset['input'][key] for key in ['bytes', 'sha256']}
            for codec in ['gzip9', 'brotli11']:
                frame = directory / f'{label}-{codec}.bin'
                stats = node_codec_samples(codec, frame, expected, directory / 'decoded.wasm', args.samples)
                asset['formats'][codec]['decode'] = {'node': stats}
                print(label, codec, 'decode_ms', stats['codec_ns']['median'] / 1e6,
                      'peak_kib', stats['peak_kib']['median'], flush=True)
        stored.write_text(json.dumps(receipt, indent=2) + '\n')
        return
    receipt = {"host": platform.node(), "platform": platform.platform(), "mbps": args.mbps,
               "reference": subprocess.check_output(['zstd', '--version'], text=True).strip(),
               "fixture": frame_digest(fixture), "assets": {}}
    for argument in args.assets:
        label, filename = argument.split('=', 1)
        if not label or Path(label).name != label or label in {'.', '..'}:
            parser.error('asset labels must be simple filenames')
        asset = Path(filename)
        asset_meta = frame_digest(asset)
        result = {"input": {"path": str(asset), **asset_meta}, "formats": {}}
        decoded = directory / 'decoded.wasm'
        fast = directory / f'{label}-3.zst'
        reference = ['zstd', '-q', '-f', '--single-thread']
        fast_encode = timed([*reference, '-3', asset, '-o', fast])
        formats = result['formats']
        formats['zstd3'] = {**frame_digest(fast), 'encode': fast_encode,
                            'decode': decode_samples(fixture, fast, asset_meta, decoded, args.samples)}
        if not args.fast_only:
            ultra = directory / f'{label}-22.zst'
            library_frame = directory / f'{label}-library.zst'
            for name, frame, cmd in [
                ('reference_ultra22', ultra, [*reference, '--ultra', '-22', asset, '-o', ultra]),
                ('library_ultra22', library_frame, [fixture, 'encode', asset, library_frame, '22', '1']),
            ]:
                encoded = timed(cmd)
                formats[name] = {**frame_digest(frame), 'encode': encoded}
                timed([*reference, '-d', frame, '-o', decoded])
                if frame_digest(decoded) != asset_meta:
                    raise ValueError('reference decoder output differs from the input asset')
            formats['library_ultra22']['decode'] = decode_samples(fixture, library_frame, asset_meta, decoded, args.samples)
            for name in ['gzip9', 'brotli11']:
                frame = directory / f'{label}-{name}.bin'
                encoded = timed(['python3', __file__, '--size-worker', name, asset, frame])
                formats[name] = {**frame_digest(frame), 'encode': encoded,
                                 'decode': {'node': node_codec_samples(name, frame, asset_meta, decoded, args.samples)}}
        compile_probe = "const fs=require('node:fs');const wasm=fs.readFileSync(process.argv[1]);const t=performance.now();new WebAssembly.Module(wasm);console.log(performance.now()-t)"
        result['node_compile'] = timed(['node', '-e', compile_probe, decoded])
        for row in formats.values():
            row['transfer_ms'] = row['bytes'] * 8 / (args.mbps * 1000)
        if frame_digest(asset) != asset_meta:
            raise ValueError('the app build replaced an input during measurement')
        receipt['assets'][label] = result
        digest_summary = {name: {"bytes": row['bytes'], "encode_ms": row['encode']['wall_ns'] / 1e6,
                                 "transfer_ms": row['transfer_ms'],
                                 **{mode: round(stats['codec_ns']['median'] / 1e6, 3)
                                    for mode, stats in row.get('decode', {}).items()}}
                          for name, row in formats.items()}
        print(label, json.dumps(digest_summary), flush=True)
        (directory / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')


if __name__ == '__main__':
    main()
