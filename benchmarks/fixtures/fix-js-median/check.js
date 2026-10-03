'use strict';
const assert = require('node:assert');
const { median } = require('./stats');

assert.strictEqual(median([3, 1, 2]), 2);
assert.strictEqual(median([1, 2, 3, 4]), 2.5);
assert.strictEqual(median([10, 9, 1]), 9);
assert.strictEqual(median([100, 20, 3, 4]), 12);
assert.strictEqual(median([-5, 10, 2]), 2);
assert.throws(() => median([]), RangeError);
const input = [3, 1, 2];
median(input);
assert.deepStrictEqual(input, [3, 1, 2]);
console.log('ok');
