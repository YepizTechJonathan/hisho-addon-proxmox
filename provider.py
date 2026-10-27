"""Proxmox VE integration for Hisho (see addon.yaml). Runs inside Core."""
from __future__ import annotations

import os
import re
import subprocess
from typing import Any

GiB = 1024 ** 3
#: The key Core uses for ``thermal_ssh``. The server only lets it run the thermal guard's
#: ``print`` (forced command, see deploy/install-thermal-guard.sh), so it can read, not act.
THERMAL_KEY = "~/.ssh/hisho_thermal_ed25519"
WARN_C = 90  # used until the guard says its own number


def server_url(value: str) -> str:
    """What people type -> the API base. An IP or name means Proxmox's defaults (https, port 8006);
    anything with a scheme or a port is used as given."""
    text = str(value or "").strip().rstrip("/")
    if not text:
        raise ValueError("no address: give the server's IP or name")
    if "://" not in text:
        host, _, port = text.partition(":")
        text = f"https://{host}:{port or 8006}"
    return text


def _token(entry: dict[str, Any], ctx: Any) -> str:
    if entry.get("token_id") and entry.get("token_secret"):
        token_id = str(entry["token_id"]).strip()
        if "@" not in token_id and re.fullmatch(r"[A-Z][A-Z0-9_]{2,80}", token_id):
            token_id = ctx.secret(token_id).strip()  # the Token ID kept as a secret (e.g. in Bitwarden)
        return f"{token_id}={ctx.secret(entry['token_secret']).strip()}"
    if entry.get("token"):
        return ctx.secret(entry["token"])
    raise ValueError("no API token: fill in Token ID and Secret (or a token secret's name)")


def _api(entry: dict[str, Any], ctx: Any, fingerprint: str | None = None) -> Any:
    from hisho.proxmox_create import ProxmoxAPI
    return ProxmoxAPI(server_url(entry["url"]), _token(entry, ctx), fingerprint=fingerprint or entry.get("fingerprint"),
                      timeout=8)


def _guests(api: Any) -> list[dict[str, Any]]:
    return sorted((r for r in api.get("/cluster/resources", type="vm") or [] if not r.get("template")),
                  key=lambda r: int(r.get("vmid") or 0))


def _norm(name: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def machine_of(guest: dict[str, Any], ctx: Any) -> str | None:
    """The fleet machine a VM is: Fleet.yaml ``vms:`` (node and VMID), else a VM named like the
    machine (a VM named Hashira-1 is the machine hashira-1). Maintenance uses this to shut a machine's VM down."""
    from hisho import fleet_power
    vms = fleet_power._fleet_data().get("vms") or {}
    for name, v in vms.items():
        if str((v or {}).get("vmid")) == str(guest.get("vmid")) and (v or {}).get("node") in (None, guest.get("node")):
            return str(name)
    by_name = {_norm(m): m for m in ctx.fleet.machines}
    return by_name.get(_norm(guest.get("name"))) if guest.get("type") == "qemu" else None


def _gb(n: Any) -> str:
    return f"{(n or 0) / GiB:.1f} GB"


def _up(seconds: Any) -> str:
    s = int(seconds or 0)
    return f"up {s // 86400} d" if s >= 86400 else f"up {s // 3600} h" if s >= 3600 else f"up {s // 60} min"


def parse_thermal(text: str) -> dict[str, Any]:
    """``hisho-thermal-guard print`` key=value lines -> {"packages": {"package0": 62}, "guard_state": ...}."""
    out: dict[str, Any] = {"packages": {}}
    for line in text.splitlines():
        key, sep, value = line.strip().partition("=")
        if not sep:
            continue
        if re.fullmatch(r"package\d+", key) and value.isdigit():
            out["packages"][key] = int(value)
        else:
            out[key] = value
    return out


def read_thermal(entry: dict[str, Any]) -> dict[str, Any] | None:
    """CPU package temperatures from the server's thermal guard, over SSH (``thermal_ssh``).
    None when the entry has no ``thermal_ssh``; {"error": ...} when it can't be read."""
    target = str(entry.get("thermal_ssh") or "").strip()
    if not target:
        return None
    key = os.path.expanduser(str(entry.get("thermal_key") or THERMAL_KEY))
    try:
        res = subprocess.run(["ssh", "-i", key, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
                              "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=accept-new", target, "print"],
                             capture_output=True, text=True, timeout=15)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:160] or type(exc).__name__}
    if res.returncode != 0:
        return {"error": (res.stderr.strip().splitlines() or ["no answer"])[-1][:160]}
    found = parse_thermal(res.stdout)
    if not found["packages"]:
        return {"error": "the guard answered without any CPU temperatures"}
    return found


def _hottest(t: dict[str, Any]) -> tuple[str, int]:
    label, c = max(t["packages"].items(), key=lambda kv: kv[1])
    return f"CPU{int(label.removeprefix('package')) + 1}", c


def thermal_problem(name: str, t: dict[str, Any] | None) -> dict[str, Any] | None:
    """A hot server, in words, for Needs you and the phone. The title never carries the number,
    so it stays one alert that escalates instead of a new alert every reading."""
    if not t or t.get("error"):
        return None
    cpu, c = _hottest(t)
    warn = int(t.get("guard_warn_c") or WARN_C)
    stop = t.get("guard_shutdown_c") or "its limit"
    state = t.get("guard_state")
    if state in ("hot", "shutting-down"):
        return {"entry": name, "severity": "critical", "title": f"{name} is overheating",
                "detail": f"{cpu} is at {c} C. Its thermal guard shuts {name} down cleanly (VMs first) if it stays "
                          f"at {stop} C for {t.get('guard_sustain_s') or '60'} s, before the CPU cuts the power."}
    if c >= warn:
        return {"entry": name, "severity": "warn", "title": f"{name} is running hot",
                "detail": f"{cpu} is at {c} C (alert at {warn} C, clean shutdown at {stop} C). Lighten its load, "
                          "check its fans, or shut it down cleanly from its Proxmox card."}
    return None


def summary(entries: dict[str, dict[str, Any]], ctx: Any) -> dict[str, Any]:
    running = total = 0
    problems, down = [], 0
    for name, entry in entries.items():
        try:
            guests = _guests(_api(entry, ctx))
        except Exception as exc:  # noqa: BLE001
            down += 1
            problems.append({"entry": name, "severity": "warn", "title": f"Proxmox server {name} does not answer",
                             "detail": f"Hisho can't reach {server_url(entry['url'])} ({type(exc).__name__}), so it can't "
                                       "show or control the VMs on it. Check the server is on and on the network."})
            continue
        total += len(guests)
        running += sum(1 for g in guests if g.get("status") == "running")
        hot = thermal_problem(name, read_thermal(entry))
        if hot:
            problems.append(hot)
    n = len(entries)
    return {"state": "down" if down == n else "warn" if problems else "ok",
            "headline": f"{n} server{'s' * (n != 1)} · {running} of {total} VMs running"
                        + (f" · {down} not answering" if down else ""),
            "problems": problems}


def details(entries: dict[str, dict[str, Any]], ctx: Any) -> list[dict[str, Any]]:
    core = ctx.fleet.core_host
    busy = updating()
    sections = []
    for name, entry in entries.items():
        try:
            api = _api(entry, ctx)
            nodes = api.get("/nodes") or []
            guests = _guests(api)
        except Exception as exc:  # noqa: BLE001
            sections.append({"title": name, "entry": name, "state": "down",
                             "facts": [server_url(entry["url"]), f"not working: {str(exc)[:200] or type(exc).__name__}"],
                             "items": _asleep_item(name, busy)})
            continue
        facts = [server_url(entry["url"])] + [
            f"node {n.get('node')} {n.get('status')} · CPU {100 * (n.get('cpu') or 0):.0f}% · "
            f"memory {_gb(n.get('mem'))} of {_gb(n.get('maxmem'))} · {_up(n.get('uptime'))}" for n in nodes]
        items = [_node_item(name, entry if len(nodes) == 1 else {}, n, guests, ctx, busy) for n in nodes]
        for g in guests:
            vmid, state = int(g["vmid"]), g.get("status")
            machine = machine_of(g, ctx)
            gfacts = [f"{'VM' if g.get('type') == 'qemu' else 'container'} {vmid} on {g.get('node')}"]
            if machine:
                gfacts.append(f"Hisho machine {machine}")
            if state == "running":
                gfacts += [f"CPU {100 * (g.get('cpu') or 0):.0f}%", f"memory {_gb(g.get('mem'))} of {_gb(g.get('maxmem'))}",
                           _up(g.get("uptime"))]
            actions = []
            if state == "running" and machine != core:
                actions.append({"id": "shutdown", "label": "shut down", "danger": True,
                                "confirm": f"Shut down {g.get('name')}"
                                           + (f" (Hisho machine {machine})" if machine else "") + "?"})
            elif state != "running":
                actions.append({"id": "start", "label": "start", "confirm": f"Start {g.get('name')}?"})
            items.append({"id": str(vmid), "name": g.get("name") or str(vmid), "machine": machine,
                          "node": g.get("node"),
                          "state": "up" if state == "running" else "off", "facts": gfacts, "actions": actions})
        vms = [i for i in items if not i["id"].startswith("node-")]
        running = sum(1 for i in vms if i["state"] == "up")
        hot = any(i.get("hot") for i in items)
        sections.append({"title": name, "entry": name, "state": "warn" if hot else "ok",
                         "facts": facts + [f"{running} of {len(vms)} running"]
                                  + ([entry["note"]] if entry.get("note") else []), "items": items})
    return sections


def update_facts(node: str, busy: dict[str, str] | None = None) -> list[str]:
    """Hisho's own Proxmox updates for this server (``hisho.proxmox_update``, Core 2026-10-27 and later): its version,
    the updates waiting, the last update, and anything that waits on the owner. Nothing on an older Core."""
    try:
        from hisho import fleet_config, proxmox_update
    except ImportError:
        return []
    now = ["updating now (Hisho's guarded update is running)"] if (busy or {}).get(proxmox_update._name(node)) else []
    st = proxmox_update.host_status(node)
    if not st:
        return now
    import datetime as dt
    tz = fleet_config.owner_tz()

    def when(t: Any) -> str:
        return dt.datetime.fromtimestamp(float(t), tz).strftime("%b %-d, %-I:%M %p %Z")
    pending = st.get("pending")
    waiting = ("updates not checked yet" if pending is None else "up to date" if pending == 0
               else f"{pending} update{'s' * (pending != 1)} waiting")
    last = st.get("last_update") or {}
    line = (f"Proxmox VE {st.get('version') or '?'} · {waiting}"
            + (f" (checked {when(st['pending_at'])})" if st.get("pending_at") else "")
            + (f" · last updated {when(last['at'])}" if last.get("at") else " · not updated by Hisho yet"))
    out = now + [line]
    if st.get("reboot_waiting"):
        out.append(f"reboot into kernel {st['reboot_waiting']} waits on your OK (Approvals)")
    if st.get("problem"):
        out.append(f"update stopped at {st['problem'].get('step')}: {str(st['problem'].get('reason'))[:160]} (Needs you)")
    return out


def _updates() -> Any:
    """Core's ``hisho.proxmox_update`` when it can start a one-server update (Core 2026-10-27 and later), else None."""
    try:
        from hisho import proxmox_update
    except ImportError:
        return None
    return proxmox_update if hasattr(proxmox_update, "start") else None


def updating() -> dict[str, str]:
    """Servers updating now (``{"*": ...}`` while the weekly run goes); empty on an older Core."""
    pu = _updates()
    try:
        return pu.running() if pu else {}
    except Exception:  # noqa: BLE001 -- the card never fails over this
        return {}


def update_action(node: str, busy: dict[str, str], asleep: bool = False) -> dict[str, Any] | None:
    """"update now" on a server Hisho updates, unless an update is running. Its question says what will happen,
    reboots included: the same guarded run as the weekly one and Semaphore's per-server button."""
    pu = _updates()
    if pu is None or busy:
        return None
    try:
        conf = pu.settings().get(pu._name(node))
    except Exception:  # noqa: BLE001
        return None
    if not conf:
        return None
    n = conf["name"]
    note = str(conf.get("reboot_note") or "")   # Fleet.yaml proxmox_updates.hosts.<server>.reboot_note
    reboot = ("If a new kernel came, it does not reboot by itself: an Approvals card asks first"
              + (f" ({note})" if note else "") + "."
              if conf["reboot"] == "approve" else
              "If a new kernel came, it shuts its VMs down cleanly, reboots, and starts the same VMs again.")
    return {"id": "update-now", "label": "update now",
            "confirm": f"Update {n} now? " + ("It is asleep or not answering: Hisho wakes it first. " if asleep else "")
                       + "Hisho checks it, backs up its running VMs, installs the Proxmox point-release updates "
                       "(never a new major version) and checks it after. " + reboot
                       + " About 15-45 minutes; the weekly Sunday run does the same by itself."}


def _asleep_item(name: str, busy: dict[str, str]) -> list[dict[str, Any]]:
    """A server whose API doesn't answer (deep sleep, or down): its update line and "update now", if Hisho updates it."""
    facts = update_facts(name, busy)
    action = update_action(name, busy, asleep=True)
    if not facts and not action:
        return []
    return [{"id": f"node-{name.lower()}", "name": f"Server {name.lower()}", "node": name.lower(), "hot": False,
             "state": "off", "facts": facts or ["not answering"], "actions": [action] if action else []}]


def _hosts_core(node: str, guests: list[dict[str, Any]], ctx: Any) -> bool:
    return any(g.get("node") == node and machine_of(g, ctx) == ctx.fleet.core_host for g in guests)


def _node_item(name: str, entry: dict[str, Any], node: dict[str, Any], guests: list[dict[str, Any]],
               ctx: Any, busy: dict[str, str] | None = None) -> dict[str, Any]:
    """The server itself: its CPU temperatures (``thermal_ssh``, single-node entries) and a clean shutdown."""
    nname = str(node.get("node"))
    facts, hot = [], False
    t = read_thermal(entry)
    if t is not None:
        if t.get("error"):
            facts.append(f"CPU temperatures: can't read ({t['error']})")
        else:
            facts.append("CPU temperatures: " + ", ".join(
                f"CPU{int(k.removeprefix('package')) + 1} {c} C" for k, c in sorted(t["packages"].items())))
            if t.get("guard_state") and t["guard_state"] != "not-running":
                facts.append(f"thermal guard {t['guard_state']}: alert {t.get('guard_warn_c')} C, clean shutdown at "
                             f"{t.get('guard_shutdown_c')} C for {t.get('guard_sustain_s')} s"
                             + (" (DRY RUN)" if t.get("guard_dry_run") == "1" else ""))
            else:
                facts.append("thermal guard not running")
            hot = thermal_problem(name, t) is not None
    facts += update_facts(nname, busy)
    actions = []
    update = update_action(nname, busy or {})
    if update:
        actions.append(update)
    if node.get("status") == "online" and not _hosts_core(nname, guests, ctx):
        actions.append({"id": "node-shutdown", "label": "shut down server cleanly", "danger": True,
                        "confirm": f"Shut down the whole server {nname}? Proxmox stops its VMs gracefully first, "
                                   "then powers off. Turn it back on with its IPMI card."})
    return {"id": f"node-{nname}", "name": f"Server {nname}", "node": nname, "hot": hot,
            "state": "warn" if hot else "up" if node.get("status") == "online" else "off",
            "facts": facts or [f"node {node.get('status')}"], "actions": actions}


def test(entry: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """The page's Test button: reach the server, learn its certificate fingerprint if none is set
    (filled into the form), log in with the token and count nodes and VMs."""
    from hisho.proxmox_create import ProxmoxAPI, ProxmoxError, split_url
    fill: dict[str, Any] = {}
    fp = entry.get("fingerprint")
    if not fp:
        try:
            probe = ProxmoxAPI.__new__(ProxmoxAPI)
            probe.host, probe.port, probe.timeout = *split_url(server_url(entry["url"])), 8
            fp = probe.server_fingerprint()
            fill["fingerprint"] = fp
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"can't reach {entry.get('url')}: {str(exc)[:160] or type(exc).__name__}"}
    try:
        api = _api(entry, ctx, fingerprint=fp)
        nodes = api.get("/nodes") or []
        guests = _guests(api)
    except (ProxmoxError, ValueError) as exc:
        return {"ok": False, "error": str(exc)[:200], "fill": fill}
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "401" in msg or "authentication" in msg.lower():
            msg = "Proxmox refused the token (check Token ID and Secret, and that the token isn't expired)"
        return {"ok": False, "error": msg[:200], "fill": fill}
    note = "" if guests else " (none visible: give the token's user PVEAuditor, or untick Privilege Separation)"
    return {"ok": True, "fill": fill,
            "message": f"Connected: {len(nodes)} node{'s' * (len(nodes) != 1)}, {len(guests)} VMs/containers{note}"
                       + (" · certificate fingerprint filled in" if fill else "")}


def act(entries: dict[str, dict[str, Any]], ctx: Any, entry: str, item: str, action: str) -> dict[str, Any]:
    if action not in ("start", "shutdown", "node-shutdown", "update-now"):
        return {"ok": False, "error": f"unknown action {action}"}
    if action == "update-now":   # before any API call: a sleeping server is woken by the run itself
        pu = _updates()
        if pu is None:
            return {"ok": False, "error": "this Hisho Core can't start a Proxmox update; update Core first"}
        import os
        return pu.start(os.environ.get("HISHO_ROOT") or "~/Hisho", item.removeprefix("node-"), by="dashboard")
    api = _api(entries[entry], ctx)
    if action == "node-shutdown":
        node = item.removeprefix("node-")
        if _hosts_core(node, _guests(api), ctx):
            return {"ok": False, "error": f"{node} runs Hisho's Core; it is never shut down from here"}
        upid = api.post(f"/nodes/{node}/status", command="shutdown")
        return {"ok": True, "message": f"clean shutdown sent to server {node} (VMs stop first)", "task": str(upid)[:120]}
    guest = next((g for g in _guests(api) if str(g.get("vmid")) == item), None)
    if guest is None:
        return {"ok": False, "error": f"no VM {item} on {entry}"}
    if action == "shutdown" and machine_of(guest, ctx) == ctx.fleet.core_host:
        return {"ok": False, "error": "that VM runs Hisho's Core; it is never shut down from here"}
    kind = "qemu" if guest.get("type") == "qemu" else "lxc"
    upid = api.post(f"/nodes/{guest['node']}/{kind}/{item}/status/{action}",
                    **({"timeout": 180} if action == "shutdown" else {}))
    return {"ok": True, "message": f"{action} sent to {guest.get('name')}", "task": str(upid)[:120]}
