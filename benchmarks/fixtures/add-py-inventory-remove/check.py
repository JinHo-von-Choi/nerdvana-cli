"""Verify command for add-py-inventory-remove: existing tests plus the new remove feature."""

import subprocess
import sys

done = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."])
assert done.returncode == 0, "existing tests fail"

from inventory import InsufficientStock, Inventory

inv = Inventory()
inv.add("bolt", 5)
assert inv.remove("bolt", 2) == 3
assert inv.count("bolt") == 3
assert inv.remove("bolt", 3) == 0
assert inv.count("bolt") == 0

inv.add("nut", 4)
for name, qty in (("nut", 5), ("washer", 1)):
    try:
        inv.remove(name, qty)
    except InsufficientStock:
        pass
    else:
        raise AssertionError(f"remove({name!r}, {qty}) should raise InsufficientStock")
assert inv.count("nut") == 4
assert inv.count("washer") == 0

for qty in (0, -1):
    try:
        inv.remove("nut", qty)
    except ValueError:
        pass
    else:
        raise AssertionError("non-positive qty should raise ValueError")
assert inv.count("nut") == 4
print("ok")
