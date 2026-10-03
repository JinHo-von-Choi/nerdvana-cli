'use strict';

/** Throw a RangeError unless config.port is an integer from 1 to 65535. */
function validate(config) {
  const port = config.port;
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new RangeError(`invalid port: ${port}`);
  }
  return config;
}

module.exports = { validate };
