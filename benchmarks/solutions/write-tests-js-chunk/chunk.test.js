'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { chunk } = require('./chunk');

test('splits evenly', () => {
  assert.deepStrictEqual(chunk([1, 2, 3, 4], 2), [[1, 2], [3, 4]]);
});

test('keeps a shorter last group', () => {
  assert.deepStrictEqual(chunk([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]]);
  assert.deepStrictEqual(chunk([1, 2, 3], 5), [[1, 2, 3]]);
});

test('empty input gives no groups', () => {
  assert.deepStrictEqual(chunk([], 3), []);
});

test('size one gives singletons', () => {
  assert.deepStrictEqual(chunk([1, 2], 1), [[1], [2]]);
});

test('does not modify the input', () => {
  const input = [1, 2, 3, 4, 5];
  chunk(input, 2);
  assert.deepStrictEqual(input, [1, 2, 3, 4, 5]);
});

test('rejects sizes that are not positive integers', () => {
  for (const size of [0, -1, 1.5, NaN, '2', undefined]) {
    assert.throws(() => chunk([1, 2, 3], size), RangeError);
  }
});
