"""Verify command for fix-mutable-default."""

from tagger import add_tag

assert add_tag("a") == ["a"]
assert add_tag("b") == ["b"], add_tag("b")
existing = ["x"]
assert add_tag("y", existing) == ["x", "y"]
assert existing == ["x"], existing
assert add_tag("z", []) == ["z"]
print("ok")
