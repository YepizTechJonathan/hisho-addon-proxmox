# Changelog

## 0.7.0

- Safe next to another Hisho: Sign in once names everything after the machine Hisho runs on (user
  `hisho-<machine>@pve`, roles `Hisho-<machine>` and `HishoCreate-<machine>`, token `hisho`) and only makes new
  things: a name already taken gets the next free suffix (-2, -3...) on all three, and no existing user, role, token
  or permission is changed or reused any more (0.6.0 reused `hisho@pve` and rewrote the roles `Hisho` and
  `HishoCreate`). The entry records what was made (`made`, names only); a sign-in that fails halfway deletes what it
  had just made.
- Remove from Proxmox: on an entry made by Sign in once, the admin login once more deletes exactly what it made (the
  token, that user's permissions, the user, and its roles unless someone else is given them), checking the
  server's remembered certificate first; already-gone items are said, not an error. Then Hisho forgets the entry.

## 0.6.0

- Found on your network: the page lists the Proxmox servers it sees (their sign-in page on port 8006), with Use this.
- Sign in once: type an admin login (e.g. root@pam) once and Hisho makes its own user hisho@pve, roles Hisho (see and
  power VMs, clean server shutdown) and HishoCreate (Core's "add a machine") and an API token; the password is not kept.
  An existing user is reused and a token name in use gets a dated name. Two-factor admins are told to make the token by hand.
- Setup help says to use Sign in once instead of making a token by hand.

## 0.5.2

- Every field on its setup form says in plain words what it is, and links to where to get it (addon.yaml `help:`, shown by Hisho's dashboard).

## 0.5.1

- README says what you need (addon.yaml you_need) and how to use it; the README check requires both.
- Why a reboot waits on Approvals comes from the server's reboot_note setting (Fleet.yaml proxmox_updates.hosts), not from names in the code; neutral sample names in tests and docs.

## 0.5.0

- "update now" on each server Hisho updates: starts the same guarded update as the weekly run and Semaphore's
  per-server button (Core's `hisho-proxmox-update@<server>`); its question says how the server reboots (by itself, or
  an Approvals card first for servers set to `reboot: approve`). Hidden while an update runs; one at a time.
- A server whose API doesn't answer (deep sleep) still shows its update line and "update now" (Hisho wakes it first).

## 0.4.0

- Each server's row shows Hisho's Proxmox updates (Core's `hisho proxmox-update`): Proxmox VE version, updates
  waiting, last update in your time zone, a reboot waiting on Approvals, and where an update stopped.

## 0.3.0

- Moved out of Hisho into its own repository (hisho-addon-proxmox); installed from the add-on store.
- CPU temperatures from the thermal guard, one escalating alert for a hot server, clean server shutdown.

## 0.2.0

- Token ID and Secret typed on the page (the secret is kept on the Core machine), certificate learned on Add.

## 0.1.0

- Servers, their VMs and containers, Start and Shutdown; never shuts down the Core machine.
