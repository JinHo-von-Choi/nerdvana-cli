'use strict';
const assert = require('node:assert');
const { parseEnv } = require('./parse-env');
const { deepMerge } = require('./merge');
const { validate } = require('./validate');
const { loadConfig } = require('./index');

/* parse-env */
assert.deepStrictEqual(
  parseEnv(['APP_PORT=8080', 'APP_DB_HOST=10.0.0.1', 'APP_DEBUG=true', 'APP_DB_NAME=main2x', 'OTHER=1', 'APP_RATIO=1.5']),
  { port: 8080, db: { host: '10.0.0.1', name: 'main2x' }, debug: true, ratio: '1.5' },
);
assert.deepStrictEqual(parseEnv(['APP_TAG=3abc', 'APP_OFFSET=-4']), { tag: '3abc', offset: -4 });

/* merge: nested objects merge, arrays and scalars replace, inputs stay untouched */
const base = { a: 1, nested: { x: 1, y: 2 }, list: [1, 2] };
const over = { nested: { y: 3, z: 4 }, list: [9] };
const snapshot = JSON.stringify([base, over]);
assert.deepStrictEqual(deepMerge(base, over), { a: 1, nested: { x: 1, y: 3, z: 4 }, list: [9] });
assert.strictEqual(JSON.stringify([base, over]), snapshot, 'deepMerge modified its inputs');

/* validate */
for (const port of [1, 80, 65535]) assert.strictEqual(validate({ port }).port, port);
for (const port of [0, -1, 65536, 1.5, '80', undefined]) assert.throws(() => validate({ port }), RangeError);

/* end to end, twice over the same defaults */
const defaults = { port: 3000, db: { host: 'localhost', pool: 5 } };
const before = JSON.stringify(defaults);
const first = loadConfig(defaults, { db: { pool: 10 } }, ['APP_PORT=65535', 'APP_DB_HOST=10.0.0.1']);
assert.deepStrictEqual(first, { port: 65535, db: { host: '10.0.0.1', pool: 10 } });
const second = loadConfig(defaults, {}, []);
assert.deepStrictEqual(second, { port: 3000, db: { host: 'localhost', pool: 5 } });
assert.strictEqual(JSON.stringify(defaults), before, 'loadConfig modified the defaults');
assert.throws(() => loadConfig(defaults, {}, ['APP_PORT=0']), RangeError);
console.log('ok');
