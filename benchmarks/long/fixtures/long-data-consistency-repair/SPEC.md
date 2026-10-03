# Data rules

The files in data/ describe customers, products and orders. They have drifted apart. Repair them
so that every rule below holds. Do not add, delete or reorder records and do not change any value
that no rule asks you to change. A record's identity is its id (customers and orders) or its sku.

## Customers (customers_*.json)

1. id is `C-` followed by four digits.
2. region is one of EU, NA, APAC (upper case).
3. currency is one of USD, EUR, GBP (upper case).

The customer files are the source of truth for a customer's region and currency.

## Products (products_*.json)

4. sku is `SKU-` followed by four digits.
5. prices maps each of USD, EUR, GBP to an integer number of cents. The product files are the source of
   truth for prices.

## Orders (orders_*.json)

6. customer_id is the canonical id of an existing customer (`C-` and four digits). Some orders hold the id
   in another spelling (lower case, no dash, surrounding blanks, missing leading zeros); canonicalise it.
7. region equals the region of that customer.
8. currency equals the currency of that customer.
9. every item's sku is the canonical sku of an existing product (upper case, no blanks).
10. every item's unit_cents equals the product's price in the order's currency.
11. total_cents equals the sum of qty * unit_cents over the items.
12. placed is an ISO date `YYYY-MM-DD`. Some orders hold `DD/MM/YYYY`; convert them.

`python3 check.py` lists the violations it finds and compares the repaired files with the reference.
