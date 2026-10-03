'use strict';
const { deepMerge }  = require('./merge');
const { parseEnv }   = require('./parse-env');
const { validate }   = require('./validate');

/** Defaults, then the file config, then APP_* environment lines; the result is validated. */
function loadConfig(defaults, fileConfig, envLines) {
  return validate(deepMerge(deepMerge(defaults, fileConfig), parseEnv(envLines)));
}

module.exports = { loadConfig };
