"use strict";

// eslint-disable-next-line @typescript-eslint/no-require-imports
const modern = require("brace-expansion-safe");

const MAX_PATTERN_LENGTH = 16_384;
const MAX_BRANCH_TOKENS = 1_024;
const MAX_NESTING_DEPTH = 64;
const MAX_SEQUENCE_TOKENS = 128;
const MAX_BRACE_GROUPS = 256;
const NUMERIC_SEQUENCE = /-?\d+\.\.-?\d+(?:\.\.-?\d+)?/g;

function inspectPattern(pattern) {
  if (typeof pattern !== "string") {
    return { complexity: 1, safe: false };
  }
  if (pattern.length > MAX_PATTERN_LENGTH) {
    return { complexity: 1, safe: false };
  }

  let depth = 0;
  let maxDepth = 0;
  let commas = 0;
  let groups = 0;
  let escaped = false;

  for (const character of pattern) {
    if (escaped) {
      escaped = false;
    } else if (character === "\\") {
      escaped = true;
    } else if (character === "{") {
      groups += 1;
      if (groups > MAX_BRACE_GROUPS) {
        return { complexity: 1, safe: false };
      }
      depth += 1;
      maxDepth = Math.max(maxDepth, depth);
    } else if (character === "}") {
      depth = Math.max(0, depth - 1);
    } else if (character === ",") {
      commas += 1;
      if (commas > MAX_BRANCH_TOKENS) {
        return { complexity: 1, safe: false };
      }
    }
  }

  return {
    complexity: commas + 1,
    safe: maxDepth <= MAX_NESTING_DEPTH,
  };
}

function safeLimit(value, fallback, complexity) {
  const requested =
    typeof value === "number" && Number.isFinite(value)
      ? Math.min(Math.max(value, 0), fallback)
      : fallback;
  return Math.floor(requested / complexity);
}

function inspectNumericSequences(pattern) {
  let count = 0;
  let width = 1;
  for (const match of pattern.matchAll(NUMERIC_SEQUENCE)) {
    count += 1;
    if (count > MAX_SEQUENCE_TOKENS) {
      return { safe: false, width };
    }
    for (const part of match[0].split("..")) {
      width = Math.max(width, part.length);
    }
  }
  return { safe: true, width };
}

function expand(pattern, options) {
  const { complexity, safe } = inspectPattern(pattern);
  if (!safe) return [];
  const sequences = inspectNumericSequences(pattern);
  if (!sequences.safe) return [];

  const maxLength = safeLimit(
    options?.maxLength,
    modern.EXPANSION_MAX_LENGTH,
    complexity,
  );
  const sequenceMax = Math.floor(maxLength / sequences.width);
  return modern.expand(pattern, {
    ...options,
    max: Math.min(
      safeLimit(options?.max, modern.EXPANSION_MAX, complexity),
      sequenceMax,
    ),
    maxLength,
  });
}

module.exports = expand;
module.exports.expand = expand;
module.exports.EXPANSION_MAX = modern.EXPANSION_MAX;
module.exports.EXPANSION_MAX_LENGTH = modern.EXPANSION_MAX_LENGTH;
