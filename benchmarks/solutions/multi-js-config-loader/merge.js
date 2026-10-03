'use strict';

const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

/** Recursively merge override into base and return the merged object; base and override are not modified. */
function deepMerge(base, override) {
  const result = { ...base };
  for (const [key, value] of Object.entries(override)) {
    result[key] = isObject(value) && isObject(result[key]) ? deepMerge(result[key], value) : value;
  }
  return result;
}

module.exports = { deepMerge };
