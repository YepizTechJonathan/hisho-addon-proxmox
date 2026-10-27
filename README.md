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
  - A Proxmox API token (Datacenter > Permissions > API Tokens)
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

Add a server on its page (**Add-ons > Proxmox > + add**). In Proxmox: **Datacenter > Permissions >
API Tokens > Add** (user `root@pam`, Token ID e.g. `hisho`, untick Privilege Separation). Proxmox
then shows the **Token ID** (`root@pam!hisho`) and the **Secret** once: paste both and press Add.
Hisho tests the connection first and remembers the server's certificate fingerprint. A typed secret
is kept only on the Core machine (`~/.hisho-secrets.env`); you can give a secret's name from
Infisical or Bitwarden instead. Entries are kept in `~/Hisho/System/Config/Addons/proxmox.yaml`.

To rotate a token: Proxmox > Datacenter > Permissions > API Tokens, remove it and add it again,
then paste the new one on the server's entry.

## How to remove

**Add-ons > Store > Proxmox > Remove**, or `hisho addon remove proxmox`. Its card and page go away;
your servers stay listed in `~/Hisho/System/Config/Addons/proxmox.yaml`, so installing it again
picks up where you left off. Nothing on the Proxmox servers is changed.

## License

Source-available under the [PolyForm Shield License 1.0.0](LICENSE.md): free to use, change and share,
for any purpose except offering a product that competes with Hisho (reselling it, for example).
