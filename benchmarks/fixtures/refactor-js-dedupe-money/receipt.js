'use strict';

/** Format an integer amount of cents as a dollar string such as $1,234.05. */
function formatCents(cents) {
  const sign   = cents < 0 ? '-' : '';
  const abs    = Math.abs(cents);
  const dollars = String(Math.floor(abs / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${sign}$${dollars}.${String(abs % 100).padStart(2, '0')}`;
}

/** A short receipt: paid amount and change due. */
function renderReceipt(paidCents, dueCents) {
  return `PAID ${formatCents(paidCents)} CHANGE ${formatCents(paidCents - dueCents)}`;
}

module.exports = { renderReceipt };
