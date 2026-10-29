# Changelog

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
