const assert = require('node:assert/strict');
const fs = require('node:fs');

const moduleBytes = fs.readFileSync(process.argv[2]);
const moduleObject = new WebAssembly.Module(moduleBytes);
const budget = new WebAssembly.Global({value: 'i64', mutable: true}, 100000000n);
const {exports: guest} = new WebAssembly.Instance(moduleObject, {
    zx: {budget, overrun() { throw Error('Zstandard fixture exceeded the test budget'); }},
});
guest.zx_initialize();
assert.equal(guest.zx_entry(), 42n);
assert.equal(guest.zx_entry(), 42n);
console.log(`Portable Zstandard WASM fixture passed (${process.argv[2]})`);
