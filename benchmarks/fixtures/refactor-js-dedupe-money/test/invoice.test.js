'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { renderInvoice } = require('../invoice');

test('renders items and a grouped total', () => {
  const out = renderInvoice([{ name: 'desk', cents: 123456, qty: 1 }, { name: 'pen', cents: 105, qty: 3 }]);
  assert.strictEqual(out, 'desk: $1,234.56\npen: $3.15\nTOTAL $1,237.71');
});

test('empty invoice totals zero', () => {
  assert.strictEqual(renderInvoice([]), 'TOTAL $0.00');
});
