"""Verify command for recover-shared-rate: the new tax figures and the existing tests."""

import subprocess
import sys

from tax import tax

assert tax(100) == 8.0, tax(100)
assert tax(12.5) == 1.0, tax(12.5)
assert tax(0) == 0.0

done = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."])
assert done.returncode == 0, "existing tests fail"
print("ok")
