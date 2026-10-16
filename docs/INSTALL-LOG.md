# Installation work log

This log records how the portable installation was built and tested, so
anyone can repeat the test or install TinyTable the same way. The
installation guide itself is [`SETUP.md`](SETUP.md); this page explains
where it came from and how it was checked.

## 2026-10-16: portable full-stack setup

### Goal

Install the full TinyTable stack (TinyTable, the Replika and Kindroid companions, a Matrix homeserver
and the Matrix bridge) on a fresh machine, using steps that a person can
follow without AI assistance, on Linux, macOS or Windows.

### Starting point

- The full two-companion stack existed only as a Kubernetes deployment
  (`k8s/federation-test/host-a.yaml`). That pod runs four containers: the
  TinyTable app, the Replika receiver, the Kindroid sidecar and the Matrix
  bridge. The homeserver was a separate deployment
  (`k8s/federation-test/matrix.yaml`).
- The public Compose files ran only the app and the bridge, so a Compose
  install had no companions and needed an existing homeserver.

### Design decisions

| Decision | Reason |
|---|---|
| Docker Compose is the only supported install route | It is the one host requirement that works the same on Linux, macOS and Windows |
| Setup steps run inside a container (`docker compose run --rm setup ...`) | No bash, Python or other tools needed on the host, so Windows works without WSL |
| A homeserver (continuwuity v26.9.1) is part of the stack | New users do not need to find or run a Matrix server |
| Configuration lives in a gitignored `local/` folder, files mode 600 | One place for everything private; never committed |
| Provider files are copied into a Docker volume owned by the service user | Avoids host file-permission differences between operating systems; services run as a non-root user and read them only |
| Docker-managed volumes, not host folders, for databases | SQLite locking and file ownership behave the same on every OS |
| Services run read-only, as non-root, with all capabilities dropped | Same hardening as the Kubernetes deployment |
| Containers talk over two internal networks (`table`, `matrix`) | The optional Tailscale container can reach only the homeserver |
| Web UI (8000) and homeserver (8008) published on 127.0.0.1 only | Neither has a login boundary suitable for a network |
| Tailscale and the Replika receiver are opt-in Compose profiles | Most installs do not need them |
| `.gitattributes` forces LF line endings | Windows checkouts would otherwise break files run inside Linux containers |
| Old Compose files renamed to `compose.basic.yml` / `compose.basic.matrix.yml` | With `compose.yaml` present, Compose warned "multiple config files" on every command |

### Files added or changed

- `compose.yaml`: the full stack.
- `scripts/tinytable_setup.py`: the `init`, `matrix`, `kindroid-har`,
  `secrets` and `check` commands.
- `scripts/matrix_bootstrap.py`: new `--digest-env` and `--client-url`
  options, so setup writes the bridge token digests itself.
- `deploy/tailscale-serve.json`: HTTPS proxy settings for the Tailscale profile.
- `Dockerfile`: copies the setup helpers into the image.
- `docs/SETUP.md`: the step-by-step guide.
- `.gitattributes`, `.gitignore`, `.dockerignore`: line endings and the
  `local/` folder.
- `README.md`, `docs/MATRIX-SETUP.md`: point to the new guide and the
  renamed Compose files.

### Test 1: development machine, fake credentials

The whole flow was run in a clean copy of the working tree:
`setup init`, `up -d matrix`, `setup matrix`, a fake Kindroid session file
and a fake one-frame Replika capture, `setup secrets`, `setup check`, and
`up -d`.

Results:

- All services started; generated files were owned by the user, mode 600.
- A `!status` message sent through Matrix got the bridge's reply.
- Data survived `docker compose down` / `up -d`.
- The Kindroid sidecar reported `api_key` mode once a key was set.
- The existing 111 unit tests passed.

Finding: containers on that machine had no outbound network (DNS and plain
HTTP both failed), so the image build failed. This was a host firewall/DNS
issue, not a TinyTable one. The workaround, `docker build --network host -t
tinytable-table:local .`, is now in the guide's troubleshooting section.

### Test 2: fresh Debian 13 VM, following SETUP.md as written

Because the test host's own containers could not reach the internet, the
guide was tested in a VirtualBox VM, whose NAT networking does not depend on
the host's container firewall. The host had console access only, so the VM
was built headless from Debian's cloud image (see
[Making a test VM](#making-a-headless-test-vm) below).

| Guide step | Result | Change made |
|---|---|---|
| 1. Install Docker | Worked. Docker reported Compose **v5.6.0** | Guide said "2.24 or newer"; now says any v5 is also fine |
| 2. Get the code (deploy key) | Worked with a new read-only deploy key | Added: the first clone asks to trust GitHub's fingerprint; type `yes` |
| 3. Decide on Matrix access | Chose no Tailscale, server name `tinytable.home` | None |
| 4. `setup init` | Built the image (a few minutes on first run), wrote `local/` | None |
| 5. Homeserver and accounts | `up -d matrix`, log grep showed the token, `setup matrix` created the accounts and room | The homeserver's log line also says "open your Matrix client and register"; the guide now says to ignore that |
| 6-7. Replika and Kindroid credentials | Not run: needs the operator's real Replika and Kindroid sessions | None yet |
| 8. `setup secrets` / `setup check` | Correctly reported both companions as missing | `check` used to print "Ready" anyway; it now says Matrix is ready but the companions are not configured |
| 9. `docker compose up -d` | All services started; web UI answered on 127.0.0.1:8000 | None |
| 10. Matrix | Logged in as the operator, sent `!status`, got "No sessions yet. <Replika companion> disconnected, <Kindroid companion> disconnected." | None |

### Still to test

- Steps 6 and 7 with real Replika and Kindroid credentials, and a real round
  in which both companions answer.
- The Tailscale profile (HTTPS from a phone).
- macOS and Windows with Docker Desktop.
- ARM machines.

Only one installation may use a given Replika session, Kindroid AI and
Matrix room at a time. Stop any other installation before testing with real
credentials.

## 2026-10-16: seats named by provider, not persona

### Goal

No companion, operator or model names in the code. Seats are identified by
the service or model behind them, and every name shown to people is
configuration.

### What changed

| Before | After |
|---|---|
| Seat IDs were persona names from one installation | `operator`, `replika-companion`, `kindroid-companion`, `kindroid-companion-2`, `local-model` |
| Channel and room IDs named after those personas | `kindroid:companion`, `channel:companions` |
| A persona-named channel API path | `/api/channels/companions/...` (old path still served) |
| Names and pronouns written into prompts, disclosures, errors and pages | `TINYTABLE_<SEAT>_NAME` / `_PRONOUNS`, served to clients as `seats` in `/api/runtime` and channel status |
| Persona-named mode settings | `TINYTABLE_COMPANIONS_ONLY`, `TINYTABLE_KINDROID_ENABLED` (old names still read) |
| Kindroid chat URLs built into the code and the fallback extension | `TINYTABLE_KINDROID_COMPANION_URL` etc.; the extension reads `config.js` |
| A persona-named Compose profile | `replika-receiver` |

The central list of seats is `backend/app/seats.py`. The Docker setup gained
`docker compose run --rm setup names`, which writes `local/seats.env`.

Existing databases are migrated on first start: `db.migrate_legacy_ids`
copies the database to `tinytable.db.pre-seat-ids-<time>.bak`, then rewrites
seat, room and channel IDs in every table, including JSON fields such as
mentions, session `heard`/`awaiting` and message metadata IDs, inside one
transaction with a foreign-key check. Message bodies, signed federation
events and audit actor strings are left exactly as recorded.

### Tests

- Unit tests: 117 pass, including new ones for neutral defaults, configured
  names and pronouns, old IDs and settings still accepted, the migration
  (with backup and a second start that changes nothing) and the setup
  `names` command.
- A copy of a real database from an older build (1,000+ messages) migrated
  with identical per-seat message counts and no broken references.
- The Debian VM from the first entry was upgraded in place with `git pull`
  and `docker compose up -d --build`: its database, created by the older
  build, was migrated with a backup in the data volume. `setup names`
  picked up the names already in `bridge.env`, and `/api/runtime` showed
  them.
- Found and fixed on the VM: the `matrix` setup command was registered twice,
  which broke every setup command.

## 2026-10-16: cleanup for public release

- The Hermes CLI agents are no longer seeded by the code. They come from
  `TINYTABLE_CLI_AGENTS` (a JSON list); none are configured by default, and
  agents already in a database are kept.
- Scripts tied to one operator's cluster (image rollout, port-forward
  watchdog, local IRC gateway) moved out of the repository into a gitignored
  `ops/` folder.
- Tests, `conversation_lab/` fixtures and the design notes use seat IDs and
  fictional sample names (Sam, Juniper, Wren, Ember). The design notes were
  renamed to `docs/COMPANION-CHANNEL-v0.1.md` and `docs/KINDROID-SIDECAR.md`.
- The Kubernetes example overlay keeps its structure; the storage class is
  marked as the one value to change.
- The public repository starts from a single fresh commit, so no earlier
  development history (which contained provider chat identifiers and local
  paths) is published.

## Making a headless test VM

Use this to test the guide on a clean Debian machine. It needs VirtualBox
and `qemu-img` on the host, and works over SSH with no screen. Put the VM
on a disk with at least 25 GB free.

1. Download and verify Debian's cloud image (it includes cloud-init, which
   sets up a user and SSH key on first boot):

   ```bash
   mkdir -p ~/VMs/tinytable-vm && cd ~/VMs/tinytable-vm
   base=https://cloud.debian.org/images/cloud/trixie/latest
   curl -fsSLO "$base/SHA512SUMS"
   curl -fsSLO "$base/debian-13-generic-amd64.qcow2"
   sha512sum -c --ignore-missing SHA512SUMS
   ```

2. Make an SSH key for the VM and a cloud-init seed disk:

   ```bash
   ssh-keygen -t ed25519 -N "" -C tinytable-vm -f ~/.ssh/tinytable_vm
   cat > user-data <<EOF
   #cloud-config
   hostname: tinytable-vm
   users:
     - name: $USER
       groups: [sudo]
       shell: /bin/bash
       sudo: "ALL=(ALL) NOPASSWD:ALL"
       lock_passwd: true
       ssh_authorized_keys:
         - $(cat ~/.ssh/tinytable_vm.pub)
   ssh_pwauth: false
   EOF
   printf 'instance-id: tinytable-vm-1\nlocal-hostname: tinytable-vm\n' > meta-data
   cloud-localds seed.iso user-data meta-data
   ```

   `cloud-localds` is in Debian's `cloud-image-utils` package. Without it,
   `genisoimage -output seed.iso -volid cidata -joliet -rock user-data meta-data`
   does the same. On a host with neither, the Python package `pycdlib` can
   write an ISO with volume label `cidata` containing the two files.

3. Convert the disk and create the VM. It gets 2 CPUs, 4 GB of RAM, a 20 GB
   disk, and SSH forwarded to port 2225 on the host's loopback only:

   ```bash
   qemu-img convert -O vdi debian-13-generic-amd64.qcow2 tinytable-vm.vdi
   VBoxManage modifymedium disk tinytable-vm.vdi --resize 20480
   VBoxManage createvm --name tinytable-vm --ostype Debian_64 --basefolder ~/VMs --register
   VBoxManage modifyvm tinytable-vm --memory 4096 --cpus 2 --nic1 nat \
     --natdnshostresolver1 on --natpf1 "ssh,tcp,127.0.0.1,2225,,22" \
     --audio-enabled off --graphicscontroller vmsvga \
     --uart1 0x3F8 4 --uartmode1 file "$PWD/console.log" \
     --boot1 disk --boot2 dvd --boot3 none --boot4 none
   VBoxManage storagectl tinytable-vm --name SATA --add sata --controller IntelAhci --portcount 2
   VBoxManage storageattach tinytable-vm --storagectl SATA --port 0 --device 0 --type hdd --medium "$PWD/tinytable-vm.vdi"
   VBoxManage storageattach tinytable-vm --storagectl SATA --port 1 --device 0 --type dvddrive --medium "$PWD/seed.iso"
   VBoxManage startvm tinytable-vm --type headless
   ```

   `--natdnshostresolver1 on` makes the VM use the host's DNS resolver,
   which matters when the host uses Tailscale's MagicDNS. The serial console
   is written to `console.log` for troubleshooting.

4. After a minute, log in and check the VM has internet:

   ```bash
   ssh -i ~/.ssh/tinytable_vm -p 2225 $USER@127.0.0.1
   curl -fsS -o /dev/null -w '%{http_code}\n' https://deb.debian.org/
   ```

5. Follow [`SETUP.md`](SETUP.md) from step 1 inside the VM. To reach the web
   UI and homeserver from the host, add
   `-L 8000:127.0.0.1:8000 -L 8008:127.0.0.1:8008` to the ssh command.

To stop the VM: `VBoxManage controlvm tinytable-vm acpipowerbutton`. To
delete it completely: `VBoxManage unregistervm tinytable-vm --delete`.
