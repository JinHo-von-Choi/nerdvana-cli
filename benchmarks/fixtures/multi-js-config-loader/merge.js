'use strict';

const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

/** Recursively merge override into base and return the merged object; base and override are not modified. */
function deepMerge(base, override) {
  for (const [key, value] of Object.entries(override)) {
    if (isObject(value) && isObject(base[key])) {
      base[key] = deepMerge(base[key], value);
    } else {
      base[key] = value;
    }
  }
  return base;
}

module.exports = { deepMerge };
