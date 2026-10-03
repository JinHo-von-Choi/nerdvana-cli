"""Verify command for fix-parse-kv."""

from kvconfig import parse

text = "# comment\nname = demo\n\nurl = http://example.com/?a=b&c=d\nempty =\ntoken=abc==\n"
expected = {"name": "demo", "url": "http://example.com/?a=b&c=d", "empty": "", "token": "abc=="}
assert parse(text) == expected, parse(text)
assert parse("") == {}
print("ok")
