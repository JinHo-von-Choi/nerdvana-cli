"""Leaderboard helpers."""


def rank(players: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Players ordered by score, highest first. Players with equal scores keep their input order."""
    return sorted(players, key=lambda player: player[1], reverse=True)


def top(players: list[tuple[str, int]], n: int) -> list[str]:
    """Names of the n best players."""
    return [name for name, _ in rank(players)[:n]]
