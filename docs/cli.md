# CLI

Every server your Python app starts is also visible and controllable from the terminal — same registry, no separate daemon. The CLI is how you operate your OpenCode fleet in production: inspect running servers, health-check tenants, drain a workspace, or kill everything before a deploy.

Think of it as `kubectl` for your OpenCode fleet.

## See everything that's running

```sh
opencode-runtime ps
```

```
  ID                  PID    PORT    STATUS      UPTIME    WORKSPACE   USER    PROJECT
  ───────────────────────────────────────────────────────────────────────────────────
  39dce5beb4debfaa   12051   58409   ● running   5m        org_a       u_1     ~/Developer/myproject
  81fa29acb3e9210f   12088   58411   ● running   3m        org_b       u_2     ~/Developer/myproject
  c3f2a1d9e8b74f05   13204   58413   ● running   1h        org_c       u_3     ~/Developer/myproject
```

Every server your Python app has started — across all users and workspaces — is visible here. PID, port, uptime, which tenant, which user, which project. No guessing, no digging through logs.

## Health check a specific server

```sh
opencode-runtime health 39dce5beb4debfaa
```

Hit the health endpoint of any server by ID. Use this in a monitoring script, a deployment check, or just to verify a server is responsive after a spike in traffic.

Pipe it into your alerting:

```sh
opencode-runtime health 39dce5beb4debfaa || pagerduty-alert "opencode server down"
```

## Inspect a server in detail

`health` tells you if a server is up; `inspect` tells you everything else — uptime, idle time, runtime version, log file location:

```sh
opencode-runtime inspect 39dce5beb4debfaa
```

```
  ID         39dce5beb4debfaa
  Status     ● running
  Project    ~/Developer/myproject
  Workspace  org_a
  User       u_1
  PID        12051
  Port       58409
  Uptime     5m 12s
  Last used  30s ago
```

## Start a server manually

Spin up a server outside of Python — useful for pre-warming tenants before they hit your API, or for running one-off tasks from the terminal:

```sh
opencode-runtime serve --workspace acme --user-id alice
```

The server registers in the same shared registry your Python app uses. When your app later calls `r.session(workspace="acme", user_id="alice")`, it reuses the already-running server instead of starting a new one.

## Drain a tenant

A tenant is causing runaway resource usage. Stop their server without touching any other tenant:

```sh
# find the server
opencode-runtime ps | grep acme

# stop just that one
opencode-runtime stop 39dce5beb4debfaa
```

Next request from that tenant will cold-start a fresh server — clean slate, no lingering state.

## Emergency stop

Deploy gone wrong. A bug is spawning servers faster than expected. Kill everything instantly:

```sh
opencode-runtime stop-all
```

All servers stopped, all ports freed. Your app will restart them on next use.

## Use as a sidecar in production

The CLI reads from the same registry as the Python library — no separate daemon, no IPC. Run it alongside your application in any environment:

```sh
# in one terminal — your app
python app.py

# in another — live monitoring
watch -n 5 opencode-runtime ps
```

Or wire it into your infra tooling. The `ps` output is structured enough to parse, `health` exits non-zero on failure, and `stop`/`stop-all` are safe to call from scripts or runbooks.

```sh
# runbook: drain all servers before a deploy
opencode-runtime stop-all
deploy.sh
```

## Command reference

| Command | Description |
|---|---|
| `opencode-runtime ps` | List all running servers with ID, PID, port, status, uptime, workspace, user, project |
| `opencode-runtime serve` | Start a background server. Accepts `--workspace`, `--user-id` |
| `opencode-runtime health <id>` | Health check a server by ID. Exits non-zero if unhealthy |
| `opencode-runtime inspect <id>` | Show detailed info for a server: uptime, idle time, runtime version, log file |
| `opencode-runtime stop <id>` | Stop a specific server by ID |
| `opencode-runtime stop-all` | Stop all running servers |
