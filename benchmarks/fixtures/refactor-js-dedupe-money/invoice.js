'use strict';

/** Format an integer amount of cents as a dollar string such as $1,234.05. */
function formatCents(cents) {
  const sign   = cents < 0 ? '-' : '';
  const abs    = Math.abs(cents);
  const dollars = String(Math.floor(abs / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${sign}$${dollars}.${String(abs % 100).padStart(2, '0')}`;
}

/** One printable line per invoice item plus a total line. */
function renderInvoice(items) {
  const lines = items.map((item) => `${item.name}: ${formatCents(item.cents * item.qty)}`);
  const total = items.reduce((sum, item) => sum + item.cents * item.qty, 0);
  return [...lines, `TOTAL ${formatCents(total)}`].join('\n');
}

module.exports = { renderInvoice };
