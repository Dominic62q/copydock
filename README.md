# CopyDock

Package a Docker Compose app into one bundle and install it on a Linux server, online or offline.

Status: early scaffold. See `V1.md` for the agreed scope and `CopyDock_Project_Brief.md` for background.

## Development

```bash
uv sync --extra dev
uv run copydock --help
uv run pytest
```

## Sample app

`examples/sample-app/` is a frontend + API + Postgres + Caddy app used to test packaging and offline install.
