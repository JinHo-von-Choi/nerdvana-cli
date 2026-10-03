'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { Emitter } = require('../emitter');

test('on and emit call listeners in order', () => {
  const seen = [];
  const e = new Emitter();
  e.on('x', (v) => seen.push(['a', v])).on('x', (v) => seen.push(['b', v]));
  assert.strictEqual(e.emit('x', 1), 2);
  assert.deepStrictEqual(seen, [['a', 1], ['b', 1]]);
});

test('emit on an unknown event calls nothing', () => {
  assert.strictEqual(new Emitter().emit('nope'), 0);
});
