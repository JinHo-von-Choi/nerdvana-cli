'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { renderReceipt } = require('../receipt');

test('shows paid amount and change', () => {
  assert.strictEqual(renderReceipt(2000, 1295), 'PAID $20.00 CHANGE $7.05');
});

test('negative change keeps its sign', () => {
  assert.strictEqual(renderReceipt(500, 1250), 'PAID $5.00 CHANGE -$7.50');
});
