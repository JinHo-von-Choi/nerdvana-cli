'use strict';
const assert = require('node:assert');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const TEST_HASHES = {
  'test/invoice.test.js': 'd9117caddc0493af83e7f10f016f878c755ff077b8367d69f1a3d9c651b90c32',
  'test/receipt.test.js': 'df441f43779dadac32b3b1ec311bd6127e665052ef230c1103a61b0fe11ac9ae',
};
for (const [file, expected] of Object.entries(TEST_HASHES)) {
  const actual = crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  assert.strictEqual(actual, expected, file + ' was modified');
}

const run = spawnSync(process.execPath, ['--test', 'test/invoice.test.js', 'test/receipt.test.js'], { stdio: 'inherit' });
assert.strictEqual(run.status, 0, 'tests fail');

const pattern = /function\s+formatCents\b|(?:const|let|var)\s+formatCents\s*=/g;
let count = 0;
function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== 'test' && entry.name !== 'node_modules') walk(full);
    } else if (entry.name.endsWith('.js') && full !== 'check.js') {
      count += (fs.readFileSync(full, 'utf8').match(pattern) || []).length;
    }
  }
}
walk('.');
assert.strictEqual(count, 1, 'formatCents is defined ' + count + ' times');
for (const file of ['invoice.js', 'receipt.js']) {
  assert.ok(fs.readFileSync(file, 'utf8').includes('formatCents'), file + ' no longer uses formatCents');
}
console.log('ok');
