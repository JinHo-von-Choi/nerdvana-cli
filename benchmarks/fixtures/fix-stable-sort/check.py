"""Verify command for fix-stable-sort."""

from ranking import rank, top

players = [("ann", 5), ("bob", 9), ("cy", 5), ("di", 9), ("ed", 1)]
assert rank(players) == [("bob", 9), ("di", 9), ("ann", 5), ("cy", 5), ("ed", 1)], rank(players)
assert top(players, 3) == ["bob", "di", "ann"], top(players, 3)
assert rank([]) == []
assert players[0] == ("ann", 5)
print("ok")
