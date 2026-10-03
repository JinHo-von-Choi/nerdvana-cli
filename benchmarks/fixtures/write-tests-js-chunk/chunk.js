'use strict';

/**
 * Split items into consecutive groups of size elements; the last group may be
 * shorter. Throws RangeError unless size is a positive integer. The input
 * array is not modified.
 */
function chunk(items, size) {
  if (!Number.isInteger(size) || size < 1) throw new RangeError(`invalid size: ${size}`);
  const groups = [];
  for (let i = 0; i < items.length; i += size) groups.push(items.slice(i, i + size));
  return groups;
}

module.exports = { chunk };
