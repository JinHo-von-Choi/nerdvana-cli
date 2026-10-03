'use strict';
const { formatCents } = require('./money');

/** A short receipt: paid amount and change due. */
function renderReceipt(paidCents, dueCents) {
  return `PAID ${formatCents(paidCents)} CHANGE ${formatCents(paidCents - dueCents)}`;
}

module.exports = { renderReceipt };
