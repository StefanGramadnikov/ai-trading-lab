# 30 — Deployment runbook: the V0 paper run on one small server

**Status:** slice 5 of docs/29. Paper only. Written for a first-time server owner; do the steps in order and stop at any
step that does not give the expected result. The deployment files are `compose.prod.yaml` and `deploy/`.

## 1. What you get, what it costs, what is not included

- One small server (VPS) running PostgreSQL and the `atl-live` loop (rules breakout, passive benchmark and, once you switch it
  on, the AI strategy) in Docker, restarted automatically, with a health check, nightly backups and log rotation.
- Nothing is reachable from the internet except SSH. You look at it by logging in (`docker compose ... exec`, `atl-live status`).
  The dashboard (slice 6) and Telegram alerts (slice 7) come later; until then **you check by hand once a day**.
- Cost: the server (about 4–6 EUR a month on Hetzner for a 2 vCPU / 4 GB shared type; **check the price on the order page**,
  billing is hourly so deleting it ends the cost) plus the OpenAI spend, hard-capped at USD 15 in total by the application and
  by the monthly limit you set on the key.
- Not included: real money, real orders, exchange credentials. The execution mode is fixed to `PAPER_ONLY` in
  `compose.prod.yaml`; no setting in the env file can change it.

## 2. Before you start (you do these; I cannot create accounts or enter payment details)

1. Create a Hetzner Cloud account and a project. Add a payment method.
2. On your Mac make an SSH key if you have none: `ssh-keygen -t ed25519 -C "atl-v0"` (a passphrase is fine). Keep the private
   key on the Mac. You will paste the **public** key (`~/.ssh/id_ed25519.pub`) into Hetzner.
3. Create the server: location Falkenstein, Nuremberg or Helsinki; image **Ubuntu 24.04**; type: a shared-vCPU x86 with 2 vCPU and
   4 GB RAM (the cheapest sizes with 2 GB also work, but leave room for the database); add your SSH key; skip paid extras; name it
   `atl-v0`. Note its IPv4 address.
4. OpenAI (only when we reach section 7): create a **separate project key** for this server with a **hard monthly spend limit**
   in the OpenAI dashboard. Do not reuse the key in your local `.env`.

## 3. Prepare the server

On your Mac (replace `SERVER` with the IPv4):

```bash
ssh root@SERVER 'mkdir -p /root/atl && echo ok'
scp -r deploy root@SERVER:/root/atl/
ssh root@SERVER 'bash /root/atl/deploy/bootstrap.sh'
```

Read `deploy/bootstrap.sh` first: it updates packages, installs Docker and git from Ubuntu's repositories, opens only SSH in the
firewall, turns on automatic security updates, creates a non-root `atl` account (it is in the `docker` group, which is effectively root
on this machine, so guard its key) and installs and starts the nightly backup timer. If it prints that a reboot is required,
run `ssh root@SERVER reboot` and wait a minute. Then confirm you can log in as that account **before** doing anything else:
`ssh atl@SERVER`. The `atl` account has no `sudo`; anything that needs root is done through `ssh root@SERVER`.

Optional hardening, only after `ssh atl@SERVER` works: as root, in `/etc/ssh/sshd_config.d/` set `PasswordAuthentication no`
(Hetzner servers created with an SSH key have no root password anyway) and reload with `systemctl reload ssh`. Keep your current
session open until a second login works. Keep root login while you follow this runbook; it is how you do root steps.

## 4. Ship the code and start the loop

The repository is private, so the server never gets GitHub access: you send it a tarball of one exact commit.

```bash
git fetch origin && git checkout main && git pull --ff-only
git archive --format=tar.gz --prefix=ai-trading-lab/ -o /tmp/atl.tgz HEAD
git rev-parse HEAD            # write this commit id down: it is the version you deployed
scp /tmp/atl.tgz atl@SERVER:/opt/atl/
scp deploy/env.prod.example atl@SERVER:/opt/atl/.env.prod
ssh atl@SERVER
```

On the server:

```bash
cd /opt/atl && tar xzf atl.tgz && echo "<the commit id>" > /opt/atl/DEPLOYED_COMMIT
chmod 600 /opt/atl/.env.prod
nano /opt/atl/.env.prod        # set POSTGRES_PASSWORD to the output of: openssl rand -hex 24  (hex only: other characters break the database URL)
                               # and ATL_IMAGE_TAG to the short commit id
cd /opt/atl/ai-trading-lab
docker compose -f compose.prod.yaml --env-file /opt/atl/.env.prod build
docker compose -f compose.prod.yaml --env-file /opt/atl/.env.prod up -d
docker compose -f compose.prod.yaml --env-file /opt/atl/.env.prod ps -a
```

`ps -a` must show `db` healthy, `migrate` **Exited (0)** and `live` running. If `migrate` exited with another code, `live` never
starts: read `docker compose ... logs migrate` and stop there. The loop creates its portfolios itself on first start.

Always pass `--env-file /opt/atl/.env.prod` (Compose otherwise reads a `.env` file from the current directory). To save typing:
`alias dc='docker compose -f /opt/atl/ai-trading-lab/compose.prod.yaml --env-file /opt/atl/.env.prod'`.

## 5. Check that it works

```bash
dc ps                                   # db healthy, migrate exited 0, live running
dc logs --tail 40 live                  # JSON lines; look for a cycle with "observed": [XBTEUR, ETHEUR, SOLEUR]
dc exec live /app/.venv/bin/atl-live status
dc exec live /app/.venv/bin/atl-live health   # {"healthy": true, ...} once the first cycle has run
```

The first cycle starts within 15 minutes. `health` reports healthy while a recent cycle exists, so after a crash or a kill switch it
stays green for up to 40 minutes, and it turns unhealthy during a Kraken outage longer than 40 minutes; it is a label for you, Docker
does not restart on it. The health check turns healthy after the first cycle and `docker compose ps` then
shows `healthy` for `live`. Rules and benchmark run from the start; the benchmark buys its three positions on the first cycle.
The rules strategy needs 48 hours of candle history before it can signal (it collects as it runs), and the AI strategy is off
until section 7.

The loop holds a database lock, so a second copy cannot trade by accident, and restarts after a crash by itself
(`restart: unless-stopped`; Docker backs off between repeated failures).

## 6. Backups

`bootstrap.sh` already installed the nightly timer (03:15 UTC, runs as `atl`). Check it and take the first backup by hand:

```bash
systemctl list-timers atl-backup.timer            # shows the next run
/opt/atl/ai-trading-lab/deploy/backup.sh          # prints the file it wrote
/opt/atl/ai-trading-lab/deploy/restore-test.sh /var/backups/atl/<that file>   # must end with: restore check ok
```

`backup.sh` refuses a dump that is not a readable archive containing our tables, never deletes older backups by age (it keeps the
newest 7) and stops if less than 512 MB is free. The nightly timer only *makes* backups; **run `restore-test.sh` yourself about once a
week** and after any change to the deployment. The first test, taken before the first trading cycle, may show empty tables; that is fine.

The server keeps the newest 7 backups. **Pull a copy to your Mac** (agreed backup target) at least once a day while the run lasts:

```bash
deploy/pull-backup.sh atl@SERVER      # copies the newest dump to ~/atl-backups and verifies its checksum
```

A backup you have never restored is not a backup: `restore-test.sh` loads it into a throwaway database and compares row counts.

## 7. Activating the AI (a separate, deliberate step)

Do this only after sections 5 and 6 pass and the loop has run a few hours. The AI strategy costs real (small) money.

1. Verify the model and its current price on OpenAI's pricing page yourself, today. Tell me the model name and the input and
   output price per million tokens; I will build the exact `ATL_AI_REVIEWED_PRICES` line. The default model name in the code
   (`gpt-5.6-terra`) comes from a 2026-10-19 note and may be out of date.
2. Put the separate server key in `/opt/atl/.env.prod` as `ATL_OPENAI_API_KEY`, set `ATL_AI_ENABLED=true`,
   `ATL_AI_TOTAL_LIMIT_USD=15` and the price line. Never paste the key into chat, git or a ticket.
3. `dc up -d live` (recreates the container; the loop registers the AI strategy itself on start), then
   `dc logs live | grep '"ai"' | tail -1`: it must say `{"ai": "ai_enabled"}`. Any other value names what is missing.
4. After the first 4-hour review window, `atl-live status` shows the AI decisions with their cost. Check the cost against the
   OpenAI dashboard once; the application's figures are estimates, the invoice is authoritative.

To switch the AI off without touching the key: `dc exec live /app/.venv/bin/atl-live disable ai --reason "..."` (takes effect
within one cycle and spends nothing from then on). To remove the key entirely, delete it from the env file and recreate `live`.

## 8. Daily routine during the run (5 minutes)

- `dc ps` (live is `healthy`), `atl-live health`, `atl-live status` (equity per strategy, holdings, recent decisions).
- `atl-live errors --status new` (anything the loop recorded as broken; empty is good), then `atl-live errors --show <prefix>` for the
  stack trace of one problem and `atl-live ack <prefix|all> --reason "..."` once it is understood. `atl-live status` shows the totals.
- `df -h /` (disk) and `docker stats --no-stream` (memory).
- `deploy/pull-backup.sh` from your Mac.
- If `live` is unhealthy or restarting: `dc logs --tail 200 live`; common causes are Kraken being unreachable (the loop recovers
  by itself) or the database being down. Do not edit the database by hand.

## 9. Stop, update, roll back, tear down

- **Stop trading now:** `deploy/kill-switch.sh`. Paper positions stay in the database; start again with `dc up -d live`. Note that
  any later `dc up -d` (for example when updating) also starts it again.
- **`live` keeps restarting:** Docker restarts it after every failure, including the two exits that mean "another loop holds the lock"
  and "the paper ledger failed an integrity check". Neither trades on a bad state (each cycle fails again at once), but if
  `dc ps` shows it restarting, run `deploy/kill-switch.sh`, keep the logs and a backup, and ask for help.
- **Pause one strategy:** `atl-live disable <rules|ai|benchmark> --reason "..."`. Exits still run (docs/29 section 12).
- **Update:** send a new tarball (section 4), `dc build`, then `dc up -d migrate live`. Record the new commit id. Never change
  numbers (risk, fees, intervals) mid-experiment without a new decision; changing the strategy changes the experiment.
- **Roll back:** extract the previous tarball, `dc build`, `dc up -d live`. A migration that already ran is not undone
  automatically; restore the last backup if the old code cannot read the new schema.
- **Tear down (end of the experiment):** pull a final backup and the evaluation data, then delete the server in the Hetzner
  console (billing stops) and revoke the OpenAI key.

## 9b. The dashboard (slice 7)

Set the sign-in once, on the server (the password is typed at a hidden prompt; never paste it anywhere else):

```bash
cd /opt/atl/ai-trading-lab
dc="docker compose -f compose.prod.yaml --env-file /opt/atl/.env.prod"
$dc run --rm --no-deps dashboard /app/.venv/bin/atl-dashboard hash-password   # prints a line: scrypt:...
$dc run --rm --no-deps dashboard /app/.venv/bin/atl-dashboard make-secret     # prints a random line
nano /opt/atl/.env.prod    # paste them as ATL_DASHBOARD_PASSWORD_HASH=... and ATL_DASHBOARD_SESSION_SECRET=...
$dc up -d dashboard
```

Open it from your Mac: `ssh -L 8000:localhost:8000 atl@SERVER`, leave that window open, browse to `http://localhost:8000`.
Switches and error acknowledgements work from there; both are recorded (`atl-live audit`, `atl-live errors`).

## 9c. Telegram alerts (slice 8)

You create the bot, so the token never leaves your hands:

1. In Telegram open `@BotFather`, send `/newbot`, choose a name, and copy the token it prints (looks like `123456:ABC...`).
2. Open your new bot in Telegram and send it any message (for example "hi").
3. On the server, put the token in the env file (`nano /opt/atl/.env.prod`), line `ATL_TELEGRAM_BOT_TOKEN=...`, then:

```bash
cd /opt/atl/ai-trading-lab
dc="docker compose -f compose.prod.yaml --env-file /opt/atl/.env.prod"
$dc run --rm --no-deps -T alerts /app/.venv/bin/atl-alerts find-chat     # prints your chat id (never the token)
nano /opt/atl/.env.prod    # set ATL_TELEGRAM_CHAT_ID=<that id> and ATL_TELEGRAM_ENABLED=true
$dc up -d alerts
$dc exec -T alerts /app/.venv/bin/atl-alerts test                       # you should get a message within seconds
```

`alerts` appears in `dc ps` and reports `healthy` once it has checked. If the test message does not arrive, `dc logs --tail 50 alerts`
shows the reason without ever printing the token. To stop alerts: set `ATL_TELEGRAM_ENABLED=false` and `up -d alerts`.

## 10. Security notes

- No inbound port except SSH; the dashboard is published on the server's loopback only (SSH tunnel); the database has no published port; the containers run read-only with all capabilities dropped.
- The env file is `chmod 600` and outside the repository. A tarball made with `git archive` contains only tracked files, so a
  local `.env` can never ride along.
- Secrets never go into logs or chat. If one does, revoke and replace it.
- Treat the server as disposable: everything needed to rebuild it is the tarball, the env file and a backup.

## 11. Known gaps (fixed in later slices)

The dashboard shows a stall but nothing pushes it to you until the Telegram alerts (slice 8) exist, so look at it daily. Backups are
logical dumps once a night: up to a day of paper trading could be lost on a total server failure (acceptable for paper). The
image is built on the server from the tarball, not pulled from a registry; the commit id file is the only version record.
