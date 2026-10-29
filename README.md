# Proxmox (Hisho add-on)

## What it does

Shows your Proxmox VE servers on the Hisho dashboard: each server's state, the VMs and containers on
it (with the Hisho machine each one is), and Start / Shutdown buttons for them. A VM that runs the
Hisho Core machine never gets a Shutdown button, and neither does the server it is on. If a server
has the thermal guard set up (`thermal_ssh`), its CPU temperatures show on its row, a hot server
becomes one alert that escalates, and there is a clean server shutdown button. Maintenance mode uses
it to stop a machine's VM and start it again afterwards. With Hisho Core's own Proxmox updates
(`hisho proxmox-update`), each server's row also shows its Proxmox VE version, the updates waiting, when
Hisho last updated it, a reboot waiting on Approvals, and where an update stopped. Its **update now** button
starts that same guarded update for one server (the weekly run's and Semaphore's per-server button's code): it asks
first, and its question says whether the server reboots by itself or an Approvals card asks (Fleet.yaml `proxmox_updates.hosts.<server>: {reboot: approve, reboot_note: "why"}`). A
sleeping server keeps its row and is woken by the update.

## What it needs

- You need:
  - A Proxmox VE server you can reach from the Core machine
  - Its admin login (e.g. root@pam) once, so Hisho can make its own limited user and token (and once more if you remove it); or a Proxmox API token made by hand
- Runs on: the Core machine, inside Hisho Core
- One entry per Proxmox server, each with:
  - `url` (str, required)
  - `token_id` (str, optional)
  - `token_secret` (password, kept as a local secret, optional)
  - `token` (secret name, optional)
  - `fingerprint` (str, optional)
  - `note` (str, optional)
  - `thermal_ssh` (str, optional)
  - `thermal_key` (str, optional)
  - `made` (map, optional)

## How to install

On the dashboard: **Add-ons > Store > Proxmox > Install**. Or from a terminal:

```bash
hisho addon install proxmox
```

Hisho takes the newest version from this repository and turns it on inside Hisho Core. Updates show
as **Update available** on its Store card; **Roll back** returns to the version before.

## How to use

Its card shows each server and how many VMs run. Its page (**Add-ons > Proxmox**) lists the VMs and containers with **Start** and **Shutdown** (each asks first), CPU temperatures if the thermal guard is set, and, with Hisho's Proxmox updates, an **update now** button per server.

## Settings

Add a server on its page (**Add-ons > Proxmox**). **Found on your network** lists the Proxmox servers
Hisho sees (their sign-in page on port 8006); press **Use this** (or type the address), give it a short
name, type an admin login such as `root@pam` and its password, and press **Sign in once**. Hisho signs in
once and makes its own access, then tests it and saves it:

- user `hisho-<machine>@pve` (no password, token only), where `<machine>` is the name of the machine Hisho runs
  on (e.g. `hisho-kihon@pve`),
- role `Hisho-<machine>` on `/`: `VM.Audit VM.PowerMgmt Sys.Audit Sys.PowerMgmt` (see the servers, VMs and
  containers, start and shut them down, shut a server down cleanly),
- role `HishoCreate-<machine>` on `/`: what Core's "add a machine" needs (create a VM, its disks and cloud-init, put
  Ubuntu's image on a storage; `Sys.Modify` is what Proxmox asks for to download it),
- an API token `hisho` for that user with the user's rights.

Hisho only makes new things: it never changes or reuses a user, role, token or permission that is already there
(another Hisho's, or yours). If a name is taken it uses the next free one with the same suffix on all three
(`hisho-kihon-2@pve`, `Hisho-kihon-2`, `HishoCreate-kihon-2`). The entry remembers what it made (`made`: names
only); if something fails halfway, Hisho deletes what it had just made.

Neither role can change users or permissions or open a shell on the server. Remove the `HishoCreate-<machine>`
permission (Datacenter > Permissions) if you never make machines from Hisho. The admin password is used
once and not kept; the token's secret is kept only on the Core machine (`~/.hisho-secrets.env`).

Or open **+ add by filling in every field yourself** and paste a token you made by hand
(**Datacenter > Permissions > API Tokens > Add**, untick Privilege Separation): Hisho tests it first and
remembers the server's certificate fingerprint. You can give a secret's name from Infisical or Bitwarden
instead of the secret. Entries are kept in `~/Hisho/System/Config/Addons/proxmox.yaml`.

To rotate a token: press **Remove from Proxmox** on the server's entry, then Sign in once again (or add one by
hand and paste it on the server's entry).

**Remove from Proxmox** (on an entry made with Sign in once) asks for the admin login once more and deletes
exactly what that entry's `made` lists: its token, the permissions of its user, the user, then its roles (a role
that someone else is still given stays, and the message says so). Anything already gone is said, not an error.
It checks the server's certificate is the one Hisho remembers before sending the login. Then Hisho forgets the
entry.

## How to remove

**Add-ons > Store > Proxmox > Remove**, or `hisho addon remove proxmox`. Its card and page go away;
your servers stay listed in `~/Hisho/System/Config/Addons/proxmox.yaml`, so installing it again
picks up where you left off. Nothing on the Proxmox servers is changed: Hisho's `hisho-<machine>@pve` user, its
roles and token stay. To delete those too, press **Remove from Proxmox** on each server's entry first (or remove
them in Proxmox, Datacenter > Permissions).

## License

Source-available under the [PolyForm Shield License 1.0.0](LICENSE.md): free to use, change and share,
for any purpose except offering a product that competes with Hisho (reselling it, for example).
