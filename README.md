# CopyDock

Package a Docker Compose app into one bundle, copy it to a Linux server, and install it with a
single script. The server needs only Docker. It does not need internet, Python or CopyDock.

> **Status: alpha (0.1.0).** The full flow is tested on the included sample app (frontend, API,
> PostgreSQL, Caddy), including an install in a Docker daemon with no network. It has not yet been
> tested on a wide range of real servers or on a real ARM server. Try it on a test machine first.

## How it works

```
your laptop                       transfer                    the server
-----------                       --------                    ----------
copydock package ./app   ───►   USB / scp / share   ───►   tar xzf bundle.tar.gz
  builds + collects images                                  ./install.sh
  rewrites compose for offline                                verifies files
  seals a checksummed bundle                                  checks Docker, CPU, disk, ports
                                                              asks for passwords (.env, mode 600)
                                                              loads images, starts the app
```

## Install

```bash
pipx install copydock
```

You also need Docker with the Compose plugin on the machine where you build the bundle.

## Use

```bash
# 1. See what a project needs, and whether CopyDock can package it
copydock inspect ./myapp

# 2. Build a bundle for the server's CPU type (linux/amd64 or linux/arm64)
copydock package ./myapp --app-version 1.0 --platform linux/amd64 -o dist

# 3. Copy dist/myapp-1.0-linux-amd64.tar.gz to the server, then on the server:
tar xzf myapp-1.0-linux-amd64.tar.gz
cd myapp-1.0-linux-amd64
./install.sh
```

`./install.sh --check` only verifies the bundle and checks the server; it changes nothing.

### Commands

| Command | What it does |
| --- | --- |
| `copydock inspect PROJECT` | Lists images, ports, required settings, and anything that blocks packaging. `--json` for scripts. |
| `copydock package PROJECT` | Builds the bundle `.tar.gz` and a `.sha256` file. |

Options for `package`: `--platform`, `--app-version`, `-f/--compose`, `-o/--output`,
`--include-secret-files`.

### On the server

`./install.sh` verifies checksums, checks Docker, Compose, CPU type, disk space and ports, collects
settings into `.env` (mode 600, never printed), loads the images from the bundle, and starts the
app with pulling disabled. It never deletes volumes, so your data survives re-installs.
Options: `--check`, `--non-interactive`, `--env-file PATH`.

## What it deliberately does not do (yet)

- It does not install Docker or Compose on the server; it checks for them.
- It does not set up DNS or trusted HTTPS. Bundles serve plain HTTP on the server's address.
  Caddy runs as an ordinary service in your Compose file.
- It does not back up or migrate databases, and it does not sign bundles. Checksums catch damage,
  not tampering: compare the `.sha256` file over a channel you trust.
- It rejects Compose features it cannot package safely, with a clear message: external volumes or
  networks, `secrets:`, `configs:`, `env_file`, bind mounts outside the project, variable build
  args. Files that look like secrets (`.env`, `.pem`, `.key`) block packaging unless you pass
  `--include-secret-files`.
- One CPU type per bundle.

External services still need internet. Packaging an app does not make an SMS provider, for
example, work offline.

## Development

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
```

`examples/sample-app/` is the test application. `scripts/offline-test.sh` installs a bundle in a
Docker daemon with no network and checks the result. Scope and decisions are in `V1.md`.

## License

MIT. See `LICENSE`.
