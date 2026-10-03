"""Checks the snake_case migration of the warehouse package. Exit status 0 means done."""

import ast
import os
import pathlib
import re
import subprocess
import sys

EXPECTED = {'inventory': ['compute_inventory_total', 'resolve_inventory_index', 'merge_inventory_window', 'fetch_inventory_offset'], 'orders': ['fetch_orders_quota', 'apply_orders_digest', 'collect_orders_margin', 'build_orders_total'], 'shipping': ['build_shipping_window', 'normalize_shipping_offset', 'compute_shipping_balance', 'resolve_shipping_quota'], 'returns': ['resolve_returns_margin', 'merge_returns_total', 'fetch_returns_index', 'apply_returns_window'], 'pricing': ['apply_pricing_balance', 'collect_pricing_quota', 'build_pricing_digest', 'normalize_pricing_margin'], 'billing': ['normalize_billing_index', 'compute_billing_window', 'resolve_billing_offset', 'merge_billing_balance'], 'taxes': ['merge_taxes_digest', 'fetch_taxes_margin', 'apply_taxes_total', 'collect_taxes_index'], 'discounts': ['collect_discounts_offset', 'build_discounts_balance', 'normalize_discounts_quota', 'compute_discounts_digest'], 'catalog': ['compute_catalog_total', 'resolve_catalog_index', 'merge_catalog_window', 'fetch_catalog_offset'], 'suppliers': ['fetch_suppliers_quota', 'apply_suppliers_digest', 'collect_suppliers_margin', 'build_suppliers_total'], 'carriers': ['build_carriers_window', 'normalize_carriers_offset', 'compute_carriers_balance', 'resolve_carriers_quota'], 'pallets': ['resolve_pallets_margin', 'merge_pallets_total', 'fetch_pallets_index', 'apply_pallets_window'], 'zones': ['apply_zones_balance', 'collect_zones_quota', 'build_zones_digest', 'normalize_zones_margin'], 'labels': ['normalize_labels_index', 'compute_labels_window', 'resolve_labels_offset', 'merge_labels_balance'], 'barcodes': ['merge_barcodes_digest', 'fetch_barcodes_margin', 'apply_barcodes_total', 'collect_barcodes_index'], 'audits': ['collect_audits_offset', 'build_audits_balance', 'normalize_audits_quota', 'compute_audits_digest'], 'forecasts': ['compute_forecasts_total', 'resolve_forecasts_index', 'merge_forecasts_window', 'fetch_forecasts_offset'], 'reorder': ['fetch_reorder_quota', 'apply_reorder_digest', 'collect_reorder_margin', 'build_reorder_total'], 'stocktake': ['build_stocktake_window', 'normalize_stocktake_offset', 'compute_stocktake_balance', 'resolve_stocktake_quota'], 'receiving': ['resolve_receiving_margin', 'merge_receiving_total', 'fetch_receiving_index', 'apply_receiving_window'], 'putaway': ['apply_putaway_balance', 'collect_putaway_quota', 'build_putaway_digest', 'normalize_putaway_margin'], 'picking': ['normalize_picking_index', 'compute_picking_window', 'resolve_picking_offset', 'merge_picking_balance'], 'packing': ['merge_packing_digest', 'fetch_packing_margin', 'apply_packing_total', 'collect_packing_index'], 'dispatch': ['collect_dispatch_offset', 'build_dispatch_balance', 'normalize_dispatch_quota', 'compute_dispatch_digest'], 'routing': ['compute_routing_total', 'resolve_routing_index', 'merge_routing_window', 'fetch_routing_offset'], 'tracking': ['fetch_tracking_quota', 'apply_tracking_digest', 'collect_tracking_margin', 'build_tracking_total'], 'invoices': ['build_invoices_window', 'normalize_invoices_offset', 'compute_invoices_balance', 'resolve_invoices_quota'], 'refunds': ['resolve_refunds_margin', 'merge_refunds_total', 'fetch_refunds_index', 'apply_refunds_window'], 'payments': ['apply_payments_balance', 'collect_payments_quota', 'build_payments_digest', 'normalize_payments_margin'], 'ledgers': ['normalize_ledgers_index', 'compute_ledgers_window', 'resolve_ledgers_offset', 'merge_ledgers_balance'], 'rebates': ['merge_rebates_digest', 'fetch_rebates_margin', 'apply_rebates_total', 'collect_rebates_index'], 'tariffs': ['collect_tariffs_offset', 'build_tariffs_balance', 'normalize_tariffs_quota', 'compute_tariffs_digest'], 'customs': ['compute_customs_total', 'resolve_customs_index', 'merge_customs_window', 'fetch_customs_offset'], 'vendors': ['fetch_vendors_quota', 'apply_vendors_digest', 'collect_vendors_margin', 'build_vendors_total'], 'contracts': ['build_contracts_window', 'normalize_contracts_offset', 'compute_contracts_balance', 'resolve_contracts_quota'], 'slotting': ['resolve_slotting_margin', 'merge_slotting_total', 'fetch_slotting_index', 'apply_slotting_window'], 'cartons': ['apply_cartons_balance', 'collect_cartons_quota', 'build_cartons_digest', 'normalize_cartons_margin'], 'bins': ['normalize_bins_index', 'compute_bins_window', 'resolve_bins_offset', 'merge_bins_balance'], 'shifts': ['merge_shifts_digest', 'fetch_shifts_margin', 'apply_shifts_total', 'collect_shifts_index'], 'docks': ['collect_docks_offset', 'build_docks_balance', 'normalize_docks_quota', 'compute_docks_digest']}
OUTPUT_DIGEST = "0915ba361c557d8e"
CAMEL = re.compile(r"\b[a-z]+[A-Z][A-Za-z]*\b")

problems = []
for module, names in EXPECTED.items():
    path = pathlib.Path("warehouse") / f"{module}.py"
    text = path.read_text(encoding="utf-8")
    defined = {n.name for n in ast.parse(text).body if isinstance(n, ast.FunctionDef)}
    if defined != set(names):
        problems.append(f"{path}: functions {sorted(defined ^ set(names))} differ from the expected snake_case names")
for path in sorted(pathlib.Path("warehouse").glob("*.py")):
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        found = CAMEL.findall(line)
        if found:
            problems.append(f"{path}:{number}: camelCase left: {found[0]}")
run = subprocess.run(
    [sys.executable, "-c", "import hashlib, warehouse.app as a; print(hashlib.sha256(','.join(map(str, a.run())).encode()).hexdigest()[:16])"],
    capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
)
if run.returncode != 0:
    problems.append("running the package failed: " + (run.stderr.strip().splitlines() or ["no output"])[-1])
elif run.stdout.strip() != OUTPUT_DIGEST:
    problems.append("the package computes different values than before the rename")
for problem in problems[:40]:
    print(problem)
if problems:
    print(f"{len(problems)} problem(s)")
    sys.exit(1)
print("ok")
