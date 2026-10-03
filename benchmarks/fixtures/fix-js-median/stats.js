'use strict';

/** Median of a non-empty array of numbers; throws RangeError on empty input. */
function median(values) {
  if (values.length === 0) throw new RangeError('empty input');
  const sorted = [...values].sort();
  const mid    = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

module.exports = { median };
