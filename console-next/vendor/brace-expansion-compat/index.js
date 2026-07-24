"use strict";

// CommonJS is required for minimatch 3 compatibility.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const modern = require("brace-expansion-safe");

function expand(pattern, options) {
  return modern.expand(pattern, options);
}

Object.assign(expand, modern, { expand });

module.exports = expand;
