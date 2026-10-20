"""The sandboxed side of a PTC run: the program that runs the model's code.

This module is not imported by the parent. The parent sends its source over
the pipe and the sandbox executes it, so the child needs no file of the
project once the Landlock policy is in place.

The child is the inside of the sandbox. It receives the code to run and the
names of the host bindings, confines itself with Landlock (read-only, no
network, nothing executable), and then runs the code with the bindings bound
into its namespace. Every call of a binding travels back to the host over the
channel and comes back as the host's answer, so the code never touches a file
the host did not hand it.

Only the standard library is imported, and only before the policy is applied.

Author: 최진호
Date:   2026-10-21
"""

from __future__ import annotations

import ast
import json
import os
import socket
import sys
from typing import Any

# The modules the sandboxed code may import. Anything that reaches a process,
# a socket or the loader is left out: the code has the bindings for that.
IMPORTABLE: frozenset[str] = frozenset({
    "base64",
    "bisect",
    "collections",
    "copy",
    "csv",
    "datetime",
    "decimal",
    "enum",
    "fractions",
    "functools",
    "heapq",
    "itertools",
    "json",
    "math",
    "operator",
    "re",
    "statistics",
    "string",
    "textwrap",
    "time",
    "unicodedata",
})

# The builtins the sandboxed code may use. ``open`` is not among them: a file
# is read through a binding, which the host audits.
BUILTINS: tuple[str, ...] = (
    "abs", "all", "any", "bool", "bytes", "callable", "chr", "dict", "divmod",
    "enumerate", "filter", "float", "format", "frozenset", "getattr", "hasattr",
    "hash", "hex", "int", "isinstance", "issubclass", "iter", "len", "list",
    "map", "max", "min", "next", "oct", "ord", "print", "range", "repr",
    "reversed", "round", "set", "slice", "sorted", "str", "sum", "tuple", "type",
    "zip",
)


class Channel:
    """The socket to the host: a request goes out, an answer comes back."""

    def __init__(self, fd: int) -> None:
        self._socket = socket.socket(fileno=fd)
        self._reader = self._socket.makefile("r", encoding="utf-8")
        self._writer = self._socket.makefile("w", encoding="utf-8")
        self._next_id = 0

    def receive(self) -> dict[str, Any]:
        """The next message from the host."""
        line = self._reader.readline()
        if not line:
            raise RuntimeError("the host closed the channel")
        message: dict[str, Any] = json.loads(line)
        return message

    def send(self, message: dict[str, Any]) -> None:
        """One message to the host."""
        self._writer.write(json.dumps(message, default=str) + "\n")
        self._writer.flush()

    def call(self, name: str, args: dict[str, Any]) -> Any:
        """Call a host binding and wait for its answer."""
        self._next_id += 1
        call_id = self._next_id
        self.send({"type": "call", "id": call_id, "name": name, "args": args})
        while True:
            message = self.receive()
            if message.get("type") != "result" or message.get("id") != call_id:
                continue
            if message.get("ok"):
                return message.get("value")
            raise RuntimeError(str(message.get("error", "the host refused the call")))


class Binding:
    """One host binding, as the sandboxed code sees it: a function to call."""

    def __init__(self, name: str, channel: Channel) -> None:
        self._name = name
        self._channel = channel

    def __call__(self, **kwargs: Any) -> Any:
        return self._channel.call(self._name, kwargs)


def _guarded_import(name: str, namespace: dict[str, Any] | None = None, scope: dict[str, Any] | None = None,
                    fromlist: tuple[str, ...] = (), level: int = 0) -> Any:
    """An import that only lets the modules of :data:`IMPORTABLE` through."""
    root = name.split(".")[0]
    if root not in IMPORTABLE:
        raise ImportError(f"the sandbox does not allow importing '{root}'")
    return __import__(name, namespace, scope, fromlist, level)


def _builtins() -> dict[str, Any]:
    """The builtins the sandboxed code may use."""
    import builtins as _module

    allowed = {name: getattr(_module, name) for name in BUILTINS if hasattr(_module, name)}
    allowed["__import__"] = _guarded_import
    return allowed


def _namespace(names: list[str], channel: Channel) -> dict[str, Any]:
    """The namespace the code runs in: the bindings and the safe builtins."""
    namespace: dict[str, Any] = {"__builtins__": _builtins()}
    for name in names:
        namespace[name] = Binding(name, channel)
    return namespace


def _execute(code: str, namespace: dict[str, Any]) -> Any:
    """Run *code* and return the value of its last expression."""
    tree = ast.parse(code, mode="exec")
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = tree.body.pop()
        exec(compile(tree, "<ptc>", "exec"), namespace)  # noqa: S102 - the code is the point of the run
        expression = ast.Expression(body=last.value)  # type: ignore[attr-defined]
        return eval(compile(expression, "<ptc>", "eval"), namespace)  # noqa: S307 - the same code, as a value
    exec(compile(tree, "<ptc>", "exec"), namespace)  # noqa: S102 - the code is the point of the run
    return None


def _confine(read_paths: list[str], sandbox_path: str) -> None:
    """Apply the read-only Landlock policy to this process."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("nerdvana_sandbox", sandbox_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the sandbox policy from {sandbox_path}")
    module = importlib.util.module_from_spec(spec)
    # The module has to be in sys.modules before it runs: a dataclass looks its
    # own module up there while it is being defined.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.confine_read_only(read_paths)


def main() -> None:
    """Run the code the host asked for and answer with what it produced."""
    channel = Channel(int(os.environ["NERDVANA_PTC_FD"]))
    request = channel.receive()
    try:
        _confine(list(request.get("read_paths", [])), str(request.get("sandbox_path", "")))
        namespace = _namespace(list(request.get("bindings", [])), channel)
        value = _execute(str(request.get("code", "")), namespace)
    except BaseException as exc:  # noqa: BLE001 - the host has to learn what happened
        channel.send({"type": "done", "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return
    channel.send({"type": "done", "ok": True, "value": value})


if __name__ == "__main__":
    main()
