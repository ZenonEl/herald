# Doctor and safe Capture updates

## Set up Send from a checkout

```sh
uv run herald init --project example --chat-id=-1001234567890 --topic-id 42
uv run herald doctor
```

The first command prompts for the token without echoing it. Use `--token-file PATH`
to reuse a file or `--token-stdin` to pass one token line from automation. Pass the
token directly on stdin, keeping it out of command arguments and shell history.
`--dry-run --json` describes the setup without reading stdin or creating files.
Use `--config PATH` on both commands for a different location.

Update the destination of an existing project with `herald update --project example
--topic-id 43 --dry-run --json`. Remove `--dry-run` to apply the plan. Use
`--label` to change the project label, `--route` to choose an existing route,
`--chat-id` to change the destination, or `--clear-topic` to remove its topic.
A change to a shared destination requires `--allow-shared-route`. The command
validates the resulting config before replacing the file and preserves comments
on retained settings. `herald doctor --project example --json` shows the route,
inbox and Watch configuration and available listener status.

Run `uv run herald-doctor` from a checkout, or `herald-doctor` from an installed environment. Use `--config PATH` to select a configuration, `--offline` to skip network checks, `--no-systemd` on hosts without user systemd, and `--json` for machine-readable output. Exit status is 1 when a check fails, otherwise 0; warnings identify unverified or optional conditions.

Doctor validates configuration references, reads the configured token without displaying it, checks storage permissions and allowed file roots, and calls Telegram `getMe`, `getWebhookInfo` and `getChat`. It reads the existing Capture heartbeat and Watch duty count, probes the existing per-token lock without changing its marker, and inspects the user service. It never sends messages, consumes updates, creates databases, runs migrations or changes configuration.

A held local lock identifies an existing listener: do not launch a second poller. Doctor cannot prove that another machine is not polling the same bot. It deliberately does not call `getUpdates` to test this. Chat access checks do not prove access to every topic or permission to send every attachment. A registered Watch duty does not prove the AI session is listening.

## Install Capture on Linux

Enable Capture or Watch explicitly in the config. Use a persistent checkout and
environment for the service. Send works independently of the listener. Capture
requires Unix locking, and this unit generator targets Linux user systemd.
Keep the checkout, executable, config, token file and storage on persistent
paths. The unit has `PrivateTmp=true`, so host files in `/tmp` and `/var/tmp`
are hidden from the service.

```sh
uv sync --locked
mkdir -p ~/.config/systemd/user
uv run --no-sync herald capture-unit --prepare > herald-capture.service.new
```

Review the generated unit, then install it as
`~/.config/systemd/user/herald-capture.service` and run
`systemctl --user daemon-reload` and `systemctl --user enable --now herald-capture`.
Generate to a new file before replacing an installed unit. `--prepare` creates
the configured storage directories. `--config PATH` pins the config path in the
unit. Use a bot token file or make its configured token variable available to the
user systemd manager. Run the generator again if the installation or storage paths move.

The generated unit runs the installed `herald-capture` executable directly and
keeps the home directory read-only apart from the Capture storage paths. Prepare
the environment outside the service before starting it and after each update:

```sh
systemctl --user stop herald-capture
uv sync --locked
systemctl --user start herald-capture
uv run --no-sync herald doctor
```

Run the commands in the service's checkout. If synchronisation fails, correct that failure before starting the listener. When replacing a unit, also run `systemctl --user daemon-reload`. Keep downtime short: Telegram retains pending updates for at most 24 hours. A successful restart cannot recover already expired updates.

For a service failure inspect `journalctl --user -u herald-capture -n 50`. A restart loop plus an old Capture heartbeat is a failed listener even if systemd is still attempting to start it. Doctor marks an existing heartbeat older than three minutes as an error.
