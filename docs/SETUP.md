# Setting up TinyTable

This guide sets up the full TinyTable stack on one machine:

```text
Matrix client (Element, phone or desktop)
        |
        v
Matrix homeserver (continuwuity) <-- matrix-bridge --> TinyTable API + web UI
   (private, built in)                                    |
                                                          +-- Replika companion  (sender)
                                                          +-- Kindroid companion (sidecar)
```

Everything runs in Docker containers. The commands are the same on Linux,
macOS and Windows; Debian is the reference platform. You type each command
from inside the `tinytable` folder.

Expect about an hour the first time, most of it collecting the Replika and
Kindroid credentials.

## What you need

- **A machine that stays on.** The companions only answer while the stack is
  running. 2 GB of RAM and 5 GB of free disk is plenty.
- **Docker** with Compose v2 (step 1).
- **Accounts:** a Replika account and a Kindroid account, each with the
  companion you want at the table.
- **A desktop browser** (Chrome or Chromium) where you are logged in to
  Replika and Kindroid, to copy their session details (steps 6 and 7). This
  can be a different computer from the server.
- **Optional: a Tailscale account,** so phones and other computers can reach
  your Matrix server over HTTPS. Without it, Matrix is only reachable on the
  server itself.

## 1. Install Docker

**Debian 12 or 13** (from Docker's own instructions):

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
```

Log out and back in (so the `docker` group applies), then check:

```bash
docker compose version
```

It must print version 2.24 or newer (any v5 version is also fine).

**macOS or Windows:** install Docker Desktop and git, start Docker Desktop, and
use Terminal (macOS) or PowerShell (Windows) for every command below.

## 2. Get the code

```bash
git clone https://github.com/cyph0rwitch/tinytable_public.git tinytable
cd tinytable
```

The repository is public, so no GitHub account or key is needed. To update
later, run `git pull` inside the `tinytable` folder (see
[Everyday tasks](#everyday-tasks)).

## 3. Decide how you will reach Matrix

You choose a **Matrix server name** in the next step. It becomes part of every
user ID (`@you:<server name>`) and **cannot be changed later** without
deleting the homeserver and starting over.

- **With Tailscale (recommended for phones):**
  1. In the Tailscale admin console, open **DNS**. Note your tailnet name
     (it looks like `tail1234.ts.net`) and make sure **MagicDNS** and
     **HTTPS Certificates** are enabled.
  2. Open **Settings > Keys** and generate an auth key with **Reusable**
     turned on.
  3. Your server name is `tinytable-matrix.<tailnet name>`, for example
     `tinytable-matrix.tail1234.ts.net`. If a device called
     `tinytable-matrix` already exists in your tailnet, pick another first
     part (for example `tinytable-home`); you will use the same first part
     in step 4.
- **Without Tailscale:** pick any name, for example `tinytable.home`. Matrix
  will be reachable at `http://127.0.0.1:8008` on the server only. (Advanced:
  you can put your own HTTPS reverse proxy in front of that port.)

## 4. Create the configuration

```bash
docker compose run --rm setup init
```

The first run builds the TinyTable image, which takes a few minutes. It asks
for the server name from step 3 and creates a private `local` folder with
these files:

| File | You edit it? | What it holds |
|---|---|---|
| `local/matrix.env` | no | server name and a generated registration token |
| `local/tinytable.env` | no | feature switches for the two companions |
| `local/replika.env` | yes, step 6 | the Replika companion's session identifiers |
| `local/kindroid.env` | yes, step 7 | the Kindroid companion's API key |
| `local/tailscale.env` | yes, if using Tailscale | Tailscale auth key |
| `local/seats.env` | through `setup names` | display names and pronouns |

Everything in `local` is private: it is never committed to git, and you should
not share it.

**If you use Tailscale,** open `local/tailscale.env` in a text editor now and
fill in:

```dotenv
TS_AUTHKEY=tskey-auth-...        # the key from step 3
TS_HOSTNAME=tinytable-matrix     # the first part of your server name
```

### Names and pronouns

TinyTable identifies each seat by the service behind it (`operator`,
`replika-companion`, `kindroid-companion`), never by a persona name. Choose
how you and each companion are shown:

```bash
docker compose run --rm setup names
```

It asks for a display name and pronouns for each seat and writes
`local/seats.env`. The pronouns are used in the disclosure lines TinyTable
adds to forwarded messages, so pick the ones each companion uses. Run it again
at any time to change them, then `docker compose up -d`. (Matrix display
names are set once, in step 5; change those in your Matrix client.)

If you skip this, the seats are shown as "Operator", "Replika companion" and
"Kindroid companion", with they/them.

## 5. Start Matrix and create the accounts

Start the homeserver:

```bash
docker compose up -d matrix
```

A new homeserver prints a one-time token for its very first account. Show it
with:

```bash
docker compose logs matrix | grep "using the registration token"
```

The token is the word after "using the registration token". The same log
line suggests registering in a Matrix client: ignore that, the next command
does it for you. Run:

```bash
docker compose run --rm setup matrix
```

It asks for that token, your Matrix username, and a password for your Matrix
account (at least 12 characters; nothing appears on screen while you type
it). It uses the names from `setup names`, asking for them first if you have
not set them. It then creates:

- your account and three bot accounts (`tinytable`, `companion-a` for the
  Replika companion, `companion-b` for the Kindroid companion) with random
  passwords nobody needs to know;
- a private room called **TinyTable** containing all four;
- `local/bridge.env` and `local/operator.env`, the bridge's access tokens.

Run this step only once. If it stops with an error, see
[Starting over](#starting-over).

## 6. Replika companion credentials

TinyTable talks to Replika the same way your browser does, using a capture of
the browser's own connection start-up. This capture contains your Replika
login: treat it like a password.

1. On your desktop computer, open Chrome, go to `chrome://extensions`, turn
   on **Developer mode**, choose **Load unpacked**, and select the
   `replika_context_grabber` folder from this repository.
2. Log in to Replika in Chrome and open your chat with the companion. Reload
   the page.
3. Open the extension's popup. Wait until it shows the bootstrap frames and
   identity fields as captured, then choose **Download replikastuff.md**.
4. Copy that file to the server as `local/replika/bootstrap.md`. For example,
   from the desktop computer:

   ```bash
   scp replikastuff.md <user>@<server>:tinytable/local/replika/bootstrap.md
   ```

5. Open `local/replika.env` on the server and fill in `REPLIKA_CHAT_ID`,
   `REPLIKA_CUSTOMER_ID` and `REPLIKA_BOT_ID` from the extension popup (the
   chat ID is also at the top of the downloaded file). Leave
   `REPLIKA_WS_HEADERS_JSON='{}'` as it is unless you know you need it.

If you log out of Replika in that browser, the capture stops working; repeat
this step and step 8.

## 7. Kindroid companion credentials

The Kindroid companion needs two things: a session file that identifies which
Kindroid AI it is, and (strongly recommended) Kindroid's API key.

**API key:** in Kindroid, open **General Settings**, copy the API key (it
starts with `kn_`), and put it in `local/kindroid.env`:

```dotenv
KINDROID_API_KEY=kn_...
```

Without the key, TinyTable uses the browser login inside the session file, which
expires after an hour.

**Session file:** this is made from a browser recording (a HAR file).

1. On your desktop computer, log in to kindroid.ai in Chrome and open your
   chat with the companion.
2. Press F12 to open DevTools and choose the **Network** tab. Reload the page
   and leave it open for about 10 seconds.
3. Right-click any request in the list and choose **Save all as HAR with
   content**.
4. Copy the HAR file to the server as `local/kindroid/kindroid.har`, then run:

   ```bash
   docker compose run --rm setup kindroid-har local/kindroid/kindroid.har
   ```

   This writes `local/kindroid/session.json`.
5. **Delete the HAR file** from the server and the desktop. It contains your
   Kindroid login and is no longer needed:

   ```bash
   rm local/kindroid/kindroid.har
   ```

## 8. Load the credentials and check

```bash
docker compose run --rm setup secrets
docker compose run --rm setup check
```

`secrets` validates what you added and copies the companion files into a
private Docker volume that only the TinyTable services can read. `check`
prints one line per item: `[ok]` is done, `[--]` is missing or optional, and
`[!!]` needs fixing. Each line says what to do next.

Run `secrets` again whenever you replace a credential file.

## 9. Start everything

Without Tailscale:

```bash
docker compose up -d
```

With Tailscale:

```bash
docker compose --profile tailscale up -d
```

Then run `docker compose run --rm setup check` again. The "Running services"
section should show everything answering. After a minute, the Kindroid sidecar
should say `connected`.

The stack restarts by itself after a reboot as long as Docker starts at boot
(the default on Debian).

## 10. Join the table from Matrix

Install a Matrix client, for example Element (phone, desktop or
app.element.io). When signing in:

- choose **Edit** or **Change** next to the homeserver and enter:
  - with Tailscale: `https://<your server name>` (the device needs Tailscale
    running);
  - without Tailscale: `http://127.0.0.1:8008`. That address works only on the
    server itself; from another computer, first run
    `ssh -L 8008:127.0.0.1:8008 <user>@<server>` and keep it open;
- username and password: the ones you chose in step 5.

Open the **TinyTable** room. Every ordinary message you write there is shared
with **both** companions, and their replies appear as separate users.
Commands:

| Command | What it does |
|---|---|
| `!help` | list the commands |
| `!status` | show the session and whether both companions are connected |
| `!preview [text]` | show exactly what each companion would receive, without sending |
| `!share` | share a round without words of your own |
| `!stop` | end the current session |

Start with `!status`. If a reply ever looks uncertain, check `!status` before
sending again; the bridge never retries on its own.

**Web UI fallback:** on the server, `http://127.0.0.1:8000`. From another
computer, tunnel it with `ssh -L 8000:127.0.0.1:8000 <user>@<server>` and open
the same address. It has no login page, which is why it only listens on the
server itself.

## Everyday tasks

| Task | Command |
|---|---|
| See what is running | `docker compose ps` |
| Check everything | `docker compose run --rm setup check` |
| Follow logs | `docker compose logs -f tinytable matrix-bridge kindroid-sidecar` |
| Stop (keeps all data) | `docker compose down` |
| Update to a new version | `git pull` then `docker compose up -d --build` |
| Replace credentials | edit files in `local`, run `setup secrets`, then `docker compose up -d --force-recreate` |

Add `--profile tailscale` to `up` commands if you use Tailscale.

**Backups.** Conversation history and the Matrix server live in Docker
volumes. To back them up, stop the stack first so the databases are not being
written, then:

```bash
docker compose down
docker run --rm -v tinytable-table_tinytable-data:/data -v tinytable-table_matrix-data:/matrix -v "$PWD":/backup alpine \
  tar czf /backup/tinytable-backup.tgz /data /matrix
docker compose up -d
```

Also keep a private copy of the `local` folder. The backup contains your
conversations: store it as carefully as `local`.

## Optional: hear the Replika companion outside table rounds

By default the Replika companion's replies are collected only when TinyTable
sends it a message. To also record messages it sends on its own, set
`TINYTABLE_REPLIKA_RECEIVER_ENABLED=1` in `local/tinytable.env` and start
with the extra profile:

```bash
docker compose --profile replika-receiver up -d
```

## Moving from another TinyTable installation

Only one TinyTable may use a given Replika session, Kindroid AI and Matrix
room at a time. Two bridges in the same room both relay every message. Stop
the old installation completely before starting this one. A new installation
from this guide has its own homeserver, accounts and room, so it never shares
Matrix accounts with an older one.

## Troubleshooting

**The build fails with "Temporary failure in name resolution" (or npm
"Exit handler never called").** Containers on this machine cannot reach the
internet during the build. This is common when the host uses Tailscale's DNS
or a strict firewall. Build using the host's network instead, then carry on:

```bash
docker build --network host -t tinytable-table:local .
```

If the running containers also cannot reach the internet (the companions never
connect), tell Docker which DNS servers to use: create `/etc/docker/daemon.json`
containing `{"dns": ["1.1.1.1", "9.9.9.9"]}`, then run
`sudo systemctl restart docker`. Also check that your firewall allows
forwarding for Docker's networks.

**"port is already allocated" for 8000 or 8008.** Another program uses that
port. Stop it, or change the left-hand number of that port in `compose.yaml`
(for example `"127.0.0.1:8100:8000"`).

**`!status` gets no answer.** Run `docker compose logs matrix-bridge`. The
bridge ignores messages sent before it started, so send a new one. Check
`setup check` shows the Matrix room line as `[ok]`.

**The Replika companion is "disconnected" or sends fail.** The Replika capture has expired or
was rejected. Repeat step 6, then step 8, then
`docker compose up -d --force-recreate`.

**The Kindroid companion shows `auth_expired`.** The API key is wrong or missing, or the
browser session expired. Check `local/kindroid.env` (step 7), then step 8.

**Phone cannot reach the homeserver.** Tailscale must be running on the
phone, `docker compose --profile tailscale up -d` must have been used, and
`docker compose logs tailscale` should show it logged in. HTTPS certificates
must be enabled in the Tailscale admin console.

## Starting over

- **Change credentials only:** edit `local`, then step 8. Nothing is lost.
- **`setup matrix` failed partway:** delete everything Matrix-related and
  redo steps 5 onwards. This deletes the homeserver and TinyTable history:

  ```bash
  docker compose down -v
  rm local/bridge.env local/operator.env
  ```

- **Choose a different server name:** as above, and also delete
  `local/matrix.env`, then rerun step 4.

`docker compose down -v` permanently deletes all conversation history and the
Matrix server. Never run it on an installation you want to keep without a
backup.

## Notes for macOS and Windows

- Use Docker Desktop. Every command is the same; on Windows run them in
  PowerShell, and copy files into `local` with Explorer instead of `scp`.
- On Windows, keep git's default line-ending settings. The repository's
  `.gitattributes` keeps the files the containers need in Linux format.
- Apple Silicon and other ARM machines need ARM builds of every image; the
  Python, Node and Tailscale images have them. This setup has so far been
  tested on x86-64 Debian only.
- Laptops that sleep disconnect the companions; prefer an always-on machine.
