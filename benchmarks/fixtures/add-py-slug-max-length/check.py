"""Verify command for add-py-slug-max-length: existing tests plus the new max_length option."""

import subprocess
import sys

done = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."])
assert done.returncode == 0, "existing tests fail"

from slug import slugify

cases = [
    (("Hello World Again", 11), "hello-world"),
    (("Hello World Again", 12), "hello-world"),
    (("Hello World Again", 17), "hello-world-again"),
    (("Hello World Again", 100), "hello-world-again"),
    (("Hello World Again", 5), "hello"),
    (("Hello World Again", 3), "hel"),
    (("ab cd", 3), "ab"),
    (("abcdef gh", 4), "abcd"),
    (("a b c", 3), "a-b"),
    (("Hello World", 6), "hello"),
]
for (text, limit), expected in cases:
    got = slugify(text, max_length=limit)
    assert got == expected, (text, limit, got, expected)
    assert len(got) <= limit and not got.endswith("-")
assert slugify("Hello World") == "hello-world"
print("ok")
