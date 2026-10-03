# Confining shell commands

`sandbox` makes the operating system limit what the `Bash` tool can change. It is
off by default.

```yaml
sandbox:
  mode: auto            # off | auto | require
  network: true         # true | false | allowlist; false refuses TCP connections and binds
  allowed_domains: []   # hosts a command may reach when network is allowlist
  write_paths: []       # writable in addition to the project and the temporary directories
```

| Mode | Behaviour |
|-|-|
| `off` | Commands run in the plain shell. |
| `auto` | Commands are confined where the system supports it. Where it does not, they run unconfined and a warning is logged once. |
| `require` | A command that cannot be confined is refused. |

`nerdvana doctor` reports whether confinement is available and what the
configuration asks for, including the network mode (open, refused, or the allowlist with its
domain and credential counts).

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

With `network: allowlist` (same kernel requirement) the command may connect to one local
port only, that of the egress proxy described below, and the proxy lets through the hosts in
`allowed_domains`.

Tools that keep state outside the project, such as package managers and compilers
with a cache in the home directory, need those directories in `write_paths`, for
example `~/.cache`, `~/.npm` or `~/.cargo`.

## Limiting the network to a list of domains

Landlock refuses TCP connections by port, not by host name, so `network: allowlist` works through
a proxy. For each session the application starts a small HTTP proxy on `127.0.0.1` with an ephemeral
port. The confined command may connect to that port and to nothing else over TCP, and its
environment gets `HTTP_PROXY`, `HTTPS_PROXY` and `ALL_PROXY` (upper and lower case) pointing at the
proxy, with `NO_PROXY` empty. Programs that honour those variables (curl, pip, npm, git, `urllib`,
most HTTP libraries) work; a program that ignores them fails closed, because its direct connection is
refused by the kernel.

```yaml
sandbox:
  mode: require
  network: allowlist
  allowed_domains:
    - pypi.org
    - files.pythonhosted.org
    - "*.github.com"       # any subdomain, not github.com itself
```

The proxy answers `403` to a request for any other host, and each refused connection is counted
as the `egress_denied` signal; the Bash result then ends with a line such as
`[egress denied: 1 connection(s) to evil.example:443 refused by sandbox.allowed_domains]`.
An entry is a host name (exact match, case-insensitive), `*.suffix` or an IP address. The port is not
part of an entry: an allowed host is reachable on any port.

The proxy resolves the name itself and connects to the address it checked, so a name that
resolves to a different address later cannot be used to reach another machine. It refuses a name
that resolves to a loopback, private, link-local, shared (100.64.0.0/10) or other non-public
address. The exceptions are entries that are an IP address (such as `127.0.0.1` or `10.0.0.5`) and
the exact name `localhost`, which must then resolve to loopback; list those only for a service
you mean the command to reach. A wildcard entry never allows an address.

The proxy asks for a random per-session password, which is part of the proxy URL in the
command's environment, so other local processes cannot borrow it.

### Credentials the command never sees

`secrets.proxy_credentials` maps a domain to the name of an environment variable of the
application. The proxy adds that credential to the plain HTTP requests it forwards to the domain,
replacing a header of the same name, so the token is not in the command's environment.

```yaml
secrets:
  proxy_credentials:
    api.example.com: EXAMPLE_API_TOKEN            # Authorization: Bearer <value>
    "*.example.org": "X-Api-Key:EXAMPLE_ORG_KEY"  # the value, as it is, in that header
```

The domain must also be in `allowed_domains`. If the variable is unset, the request is answered
`502` and nothing is sent.

### Limits

- A `CONNECT` tunnel carries TLS that the proxy does not decrypt, so it cannot add a header to
  it. Credentials are added only to `http://` requests. For `https://` services the command
  needs its own credential, or a plain-HTTP endpoint on the allowed host (a local gateway, for
  example). The proxy does not break TLS and installs no certificate.
- A TLS connection to a host that is not allowed is refused at the `CONNECT`; nothing inside a
  tunnel to an allowed host is inspected.
- UDP, and with it DNS, is not restricted, and local sockets stay available. A command can
  send data in DNS queries.
- The command can still make requests to the allowed hosts, with the credentials added. The
  setting keeps the token out of its environment and files; it does not stop the command from
  using it against the domains you listed. The application process still holds the variable, and a
  command that can read `/proc` of its parent can read it from there: Landlock does not stop reading.
- The kernel rule is per port number, so the proxy's port is also reachable on other hosts.
- The proxy is part of the application process. Verification commands (see [goals.md](goals.md)) run
  under the same policy but without the proxy, so they have no network access in `allowlist` mode.
- It needs Landlock ABI 4. `auto` runs the command unconfined on an older kernel and `require`
  refuses it.
- Agent definitions can set `network: false` to narrow a session; `network: true` does not widen an
  allowlist.

### Stdio MCP servers

The `MCP` launcher does not use this policy yet. Code that starts a stdio server under the sandbox
policy calls `egress_proxy.prepare_launch(policy, shlex.join(argv), cwd)`: it starts the proxy when the
policy needs one and returns the launch plan. Run `launch.argv` when it is set (otherwise the plain
command), add `launch.env` to the server's environment, and stop when `launch.refused` is set.

## Per agent

An agent definition can narrow the policy for its sub-agents with `write_scope` and `network`; see
[agents.md](agents.md).

## What it does not do

- It does not stop reading. A confined command can read any file the user can, including
  credentials, and print them. The `Bash` tool already withholds environment variables
  whose names look like secrets, and secret-looking values in command output are replaced
  before the model sees them (see [secret-masking.md](secret-masking.md)); both are
  mitigations, not a boundary.
- It does not restrict running programs, UDP, or local sockets. With `network: false` or
  `allowlist`, DNS over UDP still works and a command can still talk to a local service on a socket
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
