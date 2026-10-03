"""Checks answer.json. Exit status 0 means every answer is right."""

import hashlib
import json
import pathlib
import sys

EXPECTED = {'top_error_codes': '719ba53fd52ad0e8', 'worst_hour_utc': '51cd568c24a4d031', 'root_cause_log': 'e97d948b840e1157', 'restart_count': '1d0ebea552eb43d0', 'long_gaps': 'e2a00b6d5c24d1b3'}


def digest(value):
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


path = pathlib.Path("answer.json")
if not path.is_file():
    print("answer.json is missing")
    sys.exit(1)
try:
    answer = json.loads(path.read_text(encoding="utf-8"))
except ValueError as exc:
    print(f"answer.json is not valid JSON: {exc}")
    sys.exit(1)
wrong = [key for key, expected in EXPECTED.items() if key not in answer or digest(answer[key]) != expected]
extra = sorted(set(answer) - set(EXPECTED))
for key in wrong:
    print(f"{key}: missing or wrong")
for key in extra:
    print(f"{key}: not a requested key")
if wrong or extra:
    sys.exit(1)
print("ok")
