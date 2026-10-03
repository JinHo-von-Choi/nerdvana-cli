'use strict';
const { formatCents } = require('./money');

/** One printable line per invoice item plus a total line. */
function renderInvoice(items) {
  const lines = items.map((item) => `${item.name}: ${formatCents(item.cents * item.qty)}`);
  const total = items.reduce((sum, item) => sum + item.cents * item.qty, 0);
  return [...lines, `TOTAL ${formatCents(total)}`].join('\n');
}

module.exports = { renderInvoice };
