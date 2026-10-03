# Confining shell commands

`sandbox` makes the operating system limit what the `Bash` tool can change. It is
off by default.

```yaml
sandbox:
  mode: auto            # off | auto | require
  network: true         # false also refuses TCP connections and binds
  write_paths: []       # writable in addition to the project and the temporary directories
```

| Mode | Behaviour |
|-|-|
| `off` | Commands run in the plain shell. |
| `auto` | Commands are confined where the system supports it. Where it does not, they run unconfined and a warning is logged once. |
| `require` | A command that cannot be confined is refused. |

`nerdvana doctor` reports whether confinement is available and what the
configuration asks for.

`nerdvana run --sandbox off|auto|require` overrides the mode for a single run.

## What it does

On Linux the command runs under [Landlock](https://docs.kernel.org/userspace-api/landlock.html),
a kernel feature (5.13 and later) that an unprivileged process uses to restrict itself
and everything it starts. It needs no root, container or helper program, and it works
on hosts where unprivileged user namespaces are switched off.

A confined command can write only below the project directory, `/tmp`, `/var/tmp`,
`/dev`, the system temporary directory and the paths in `write_paths`. Elsewhere it
cannot create, write, truncate, rename or delete files, so an `rm -rf` or a redirect
that strays out of the project fails with `Permission denied`. Subprocesses inherit the
restriction and cannot lift it.

With `network: false` and a kernel that has Landlock ABI 4 (Linux 6.7), TCP connect
and bind fail as well. On an older kernel, `auto` runs the command without
confinement and `require` refuses it.

Tools that keep state outside the project, such as package managers and compilers
with a cache in the home directory, need those directories in `write_paths`, for
example `~/.cache`, `~/.npm` or `~/.cargo`.

## What it does not do

- It does not stop reading. A confined command can read any file the user can, including
  credentials, and print them. The `Bash` tool already withholds environment variables
  whose names look like secrets; that is a mitigation, not a boundary.
- It does not restrict running programs, UDP, or local sockets. With `network: false`,
  DNS over UDP still works and a command can still talk to a local service on a socket
  file.
- It applies to the `Bash` tool only. Other tools, language servers and MCP servers
  started by the application run as the user.
- macOS and Windows are not supported. There `auto` runs commands unconfined and
  `require` refuses them.

## Why Landlock

Bubblewrap needs unprivileged user namespaces. Ubuntu 24.04 restricts them with
AppArmor, so `bwrap` fails there with `setting up uid map: Permission denied` even when
it is installed. Landlock needs only `no_new_privs`, which any process can set, and a
kernel built with it (`landlock` appears in `/sys/kernel/security/lsm`).
