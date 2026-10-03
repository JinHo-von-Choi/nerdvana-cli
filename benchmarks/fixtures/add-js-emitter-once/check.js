'use strict';
const assert = require('node:assert');
const { spawnSync } = require('node:child_process');

const existing = spawnSync(process.execPath, ['--test', 'test/emitter.test.js'], { stdio: 'inherit' });
assert.strictEqual(existing.status, 0, 'existing tests fail');

const { Emitter } = require('./emitter');

/* once fires a single time and is then gone */
let e = new Emitter();
let hits = 0;
assert.strictEqual(e.once('x', () => hits++), e);
assert.strictEqual(e.emit('x'), 1);
assert.strictEqual(e.emit('x'), 0);
assert.strictEqual(hits, 1);

/* once passes its arguments and keeps registration order with on */
e = new Emitter();
const seen = [];
e.on('x', (v) => seen.push('a' + v)).once('x', (v) => seen.push('b' + v)).on('x', (v) => seen.push('c' + v));
e.emit('x', 1);
e.emit('x', 2);
assert.deepStrictEqual(seen, ['a1', 'b1', 'c1', 'a2', 'c2']);

/* off removes a plain listener, only that one */
e = new Emitter();
const calls = [];
const f = () => calls.push('f');
const g = () => calls.push('g');
e.on('x', f).on('x', g);
assert.strictEqual(e.off('x', f), e);
e.emit('x');
assert.deepStrictEqual(calls, ['g']);

/* off removes a once listener before it fires */
e = new Emitter();
let fired = 0;
const h = () => fired++;
e.once('x', h);
e.off('x', h);
assert.strictEqual(e.emit('x'), 0);
assert.strictEqual(fired, 0);

/* off for something never registered is a no-op */
e.off('x', () => {});
e.off('never', h);

/* two once listeners each fire exactly once */
e = new Emitter();
let n = 0;
e.once('x', () => n++);
e.once('x', () => n++);
e.emit('x');
e.emit('x');
assert.strictEqual(n, 2);
console.log('ok');
