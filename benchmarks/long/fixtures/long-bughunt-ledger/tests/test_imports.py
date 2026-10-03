"""Every module of the package imports."""

import importlib
import pkgutil
import unittest

import ledgerlib


class ImportTests(unittest.TestCase):
    def test_every_module_imports(self):
        names = [m.name for m in pkgutil.walk_packages(ledgerlib.__path__, "ledgerlib.")]
        self.assertGreater(len(names), 30)
        for name in names:
            importlib.import_module(name)


if __name__ == "__main__":
    unittest.main()
