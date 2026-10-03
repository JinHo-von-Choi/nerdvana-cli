'use strict';

/** Convert an environment value: integers become numbers, true/false become booleans, anything else stays a string. */
function coerce(raw) {
  if (raw === 'true') return true;
  if (raw === 'false') return false;
  return /^-?\d+$/.test(raw) ? parseInt(raw, 10) : raw;
}

/**
 * Turn 'APP_PORT=8080' style lines into a nested object: the APP_ prefix is
 * dropped and the remaining name is lowercased and split on '_' into a path,
 * so APP_DB_HOST=10.0.0.1 gives { db: { host: '10.0.0.1' } }. Other lines are ignored.
 */
function parseEnv(lines) {
  const result = {};
  for (const line of lines) {
    const match = /^APP_([A-Z0-9_]+)=(.*)$/.exec(line);
    if (!match) continue;
    const path = match[1].toLowerCase().split('_');
    let node = result;
    for (const key of path.slice(0, -1)) node = node[key] = node[key] || {};
    node[path[path.length - 1]] = coerce(match[2]);
  }
  return result;
}

module.exports = { parseEnv };
