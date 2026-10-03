"""Verify command for multi-py-report-pipeline."""

import app
import ledger
import reader
import render

assert reader.read_records("category,amount\nFood ,12.50\n Rent,800\n\nfood,7.05\n") == [("food", 1250), ("rent", 80000), ("food", 705)]
assert reader.read_records("category,amount\n") == []

assert ledger.summarize([("a", 100), ("b", 300), ("a", 250)]) == [("a", 350), ("b", 300)]
assert ledger.summarize([("b", 100), ("a", 100), ("c", 500)]) == [("c", 500), ("a", 100), ("b", 100)]

assert render.format_cents(705) == "7.05"
assert render.format_cents(5) == "0.05"
assert render.format_cents(80050) == "800.50"
assert render.render_report([("rent", 80050), ("misc", 305)]) == "rent: 800.50\nmisc: 3.05"

text = "category,amount\nFood ,12.50\n rent,800\nfood,7.05\nRENT,0.5\nmisc,3.05\nzzz,3.05"
assert app.run(text) == "rent: 800.50\nfood: 19.55\nmisc: 3.05\nzzz: 3.05", app.run(text)
print("ok")
