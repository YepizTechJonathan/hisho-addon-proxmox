"""The Proxmox integration. Run with Hisho's venv at the repository's top:
~/code/hisho/.venv/bin/python -m pytest -q"""
import subprocess
from pathlib import Path

from hisho import addon_store, addons

HERE = Path(__file__).resolve().parents[1]


def ctx_for(addon):
    return addons.Context(addon, resolve=lambda name: f"value-of-{name}")


def test_manifest_is_valid_and_readme_matches(addon):
    assert addons.validate_manifest(addon.manifest) == []
    assert addon_store.readme_problems(HERE) == []


def test_the_proxmox_integration_lists_vms_and_never_shuts_down_core(fleet, monkeypatch):
    monkeypatch.setattr("hisho.proxmox_update.Deps.active_units", lambda self: [])
    px = addons.get("proxmox")
    addons.set_entry(px, "pve1", {"url": "https://pve1:8006", "token": "HISHO_PVE1"})
    guests = [{"vmid": 100, "name": "kihon", "status": "running", "type": "qemu", "node": "pve1", "uptime": 7200},
              {"vmid": 101, "name": "spare", "status": "stopped", "type": "qemu", "node": "pve1"}]
    posted = []

    class FakeAPI:
        def __init__(self, *a, **k): pass
        def get(self, path, **kw):
            return guests if path == "/cluster/resources" else [{"node": "pve1", "status": "online"}]
        def post(self, path, **kw):
            posted.append(path)
            return "UPID:x"
    monkeypatch.setattr("hisho.proxmox_create.ProxmoxAPI", FakeAPI)
    data = addons._fleet_data()
    data["vms"] = {"kihon": {"node": "pve1", "vmid": 100}}
    addons._write_fleet(data)
    ctx = ctx_for(px)
    assert addons.summarize(px, ctx)["headline"] == "1 server · 1 of 2 VMs running"
    [sec] = addons.details(px, ctx)
    server, kihon, spare = sec["items"]
    # the node runs Core: no server shutdown; "update now" stays (its reboot waits on Approvals)
    assert server["id"] == "node-pve1" and [a["id"] for a in server["actions"]] == ["update-now"]
    assert "1 of 2 running" in sec["facts"]
    assert kihon["actions"] == [] and "Hisho machine kihon" in kihon["facts"]
    assert spare["actions"][0]["id"] == "start"
    assert addons.act(px, "pve1", "100", "shutdown", ctx)["ok"] is False
    assert addons.act(px, "pve1", "101", "start", ctx)["ok"] and posted == ["/nodes/pve1/qemu/101/status/start"]
    assert addons.act(px, "pve1", "node-pve1", "node-shutdown", ctx)["ok"] is False


def _thermal_px(fleet, monkeypatch, printed: str, returncode: int = 0):
    monkeypatch.setattr("hisho.proxmox_update.Deps.active_units", lambda self: [])
    px = addons.get("proxmox")
    addons.set_entry(px, "pve", {"url": "https://pve:8006", "token": "HISHO_PVE", "thermal_ssh": "root@pve"})
    guests = [{"vmid": 521, "name": "TruenasCE", "status": "running", "type": "qemu", "node": "pve", "uptime": 60}]
    posted, ran = [], []

    class FakeAPI:
        def __init__(self, *a, **k): pass
        def get(self, path, **kw):
            return guests if path == "/cluster/resources" else [{"node": "pve", "status": "online"}]
        def post(self, path, **kw):
            posted.append((path, kw))
            return "UPID:x"

    def fake_run(cmd, **kw):
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout=printed, stderr="Permission denied (publickey).")
    monkeypatch.setattr("hisho.proxmox_create.ProxmoxAPI", FakeAPI)
    monkeypatch.setattr(subprocess, "run", fake_run)
    return px, ctx_for(px), posted, ran


GUARD_PRINT = ("host=pve\npackage0=50\npackage1={c}\nguard_checked_at=1\nguard_state={state}\nguard_hot_since=\n"
               "guard_max_c={c}\nguard_warn_c=90\nguard_shutdown_c=95\nguard_sustain_s=60\nguard_dry_run=0\n")


def test_proxmox_shows_cpu_temperatures_and_a_clean_server_shutdown(fleet, monkeypatch):
    px, ctx, posted, ran = _thermal_px(fleet, monkeypatch, GUARD_PRINT.format(c=62, state="ok"))
    assert addons.summarize(px, ctx)["problems"] == []
    [sec] = addons.details(px, ctx)
    server = sec["items"][0]
    assert "CPU temperatures: CPU1 50 C, CPU2 62 C" in server["facts"]
    assert any(f.startswith("thermal guard ok: alert 90 C, clean shutdown at 95 C for 60 s") for f in server["facts"])
    assert ran[0][-2:] == ["root@pve", "print"] and "IdentitiesOnly=yes" in ran[0]
    assert [a["id"] for a in server["actions"]] == ["update-now", "node-shutdown"]
    assert addons.act(px, "pve", "node-pve", "node-shutdown", ctx)["ok"]
    assert posted == [("/nodes/pve/status", {"command": "shutdown"})]


def test_a_hot_proxmox_server_is_one_alert_that_escalates(fleet, monkeypatch):
    px, ctx, _, _ = _thermal_px(fleet, monkeypatch, GUARD_PRINT.format(c=91, state="warn"))
    [warn] = addons.summarize(px, ctx)["problems"]
    assert warn["severity"] == "warn" and warn["title"] == "pve is running hot" and "CPU2 is at 91 C" in warn["detail"]
    px, ctx, _, _ = _thermal_px(fleet, monkeypatch, GUARD_PRINT.format(c=96, state="hot"))
    [crit] = addons.summarize(px, ctx)["problems"]
    assert crit["severity"] == "critical" and crit["title"] == "pve is overheating"
    assert "shuts pve down cleanly" in crit["detail"]


def test_unreadable_temperatures_show_on_the_card_but_do_not_page(fleet, monkeypatch):
    px, ctx, _, _ = _thermal_px(fleet, monkeypatch, "", returncode=255)
    assert addons.summarize(px, ctx)["problems"] == []
    [sec] = addons.details(px, ctx)
    assert "CPU temperatures: can't read (Permission denied (publickey).)" in sec["items"][0]["facts"]


def test_proxmox_takes_token_id_and_secret_and_test_fills_the_fingerprint(fleet, tmp_path, monkeypatch):
    monkeypatch.setattr(addons, "LOCAL_SECRETS", str(tmp_path / "secrets.env"))
    px = addons.get("proxmox")
    made = []

    class FakeAPI:
        def __init__(self, url, token, fingerprint=None, timeout=30):
            made.append((token, fingerprint))
        def server_fingerprint(self):
            return "AB:CD"
        def get(self, path, **kw):
            return [{"node": "pve1"}] if path == "/nodes" else [{"vmid": 1, "name": "a", "status": "running",
                                                                 "type": "qemu", "node": "pve1"}]
    FakeAPI.__new__ = lambda cls, *a, **k: object.__new__(cls)
    monkeypatch.setattr("hisho.proxmox_create.ProxmoxAPI", FakeAPI)
    monkeypatch.setattr("hisho.proxmox_create.split_url", lambda url: ("pve1", 8006))
    res = addons.test_entry(px, "pve1", {"url": "https://pve1:8006", "token_id": "root@pam!hisho",
                                         "token_secret": "1234-secret"})
    assert res["ok"] and res["fill"] == {"fingerprint": "AB:CD"} and "1 VMs/containers" in res["message"]
    assert made[-1] == ("root@pam!hisho=1234-secret", "AB:CD")
    addons.set_entry(px, "pve1", {"url": "https://pve1:8006", "token_id": "root@pam!hisho",
                                  "token_secret": "1234-secret", "fingerprint": "AB:CD"}, merge=True)
    assert addons.entries(px)["pve1"]["token_secret"] == "HISHO_ADDON_PROXMOX_PVE1_TOKEN_SECRET"
    assert "1234-secret" not in addons.config_path("proxmox").read_text()


def test_the_proxmox_form_asks_only_for_what_people_have():
    view = addons.view(addons.get("proxmox"))
    assert list(view["entry_form"]) == ["url", "token_id", "token_secret", "note"]
    assert "fingerprint" in view["entry_schema"] and "token" in view["entry_schema"]


def test_proxmox_takes_a_bare_ip_or_a_full_url():
    from importlib import util
    spec = util.spec_from_file_location("px_provider", HERE / "provider.py")
    mod = util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.server_url("198.51.100.11") == "https://198.51.100.11:8006"
    assert mod.server_url(" pve1 ") == "https://pve1:8006"
    assert mod.server_url("198.51.100.11:9443") == "https://198.51.100.11:9443"
    assert mod.server_url("https://pve.example:443/") == "https://pve.example:443"


def test_the_provider_matches_vms_to_fleet_machines_by_name_or_fleet_settings(monkeypatch):
    import importlib.util
    path = HERE / "provider.py"
    spec = importlib.util.spec_from_file_location("px_provider", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr("hisho.fleet_power._fleet_data",
                        lambda root=None: {"vms": {"hashira-2": {"node": "pve1", "vmid": 5221}}})

    class Ctx:
        class fleet:
            machines = ["kihon", "souko", "hashira-1", "hashira-2"]
    assert mod.machine_of({"vmid": 5222, "name": "Hashira-1", "type": "qemu", "node": "pve1"}, Ctx) == "hashira-1"
    assert mod.machine_of({"vmid": 5221, "name": "whatever", "type": "qemu", "node": "pve1"}, Ctx) == "hashira-2"
    assert mod.machine_of({"vmid": 5221, "name": "x", "type": "qemu", "node": "pve"}, Ctx) is None  # same id, other node
    assert mod.machine_of({"vmid": 106, "name": "AMP", "type": "qemu", "node": "pve1"}, Ctx) is None


def test_the_server_row_shows_hishos_proxmox_updates(fleet, monkeypatch, tmp_path):
    import importlib.util
    monkeypatch.setenv("HISHO_STATE_DIR", str(tmp_path / "state"))
    from hisho import proxmox_update
    spec = importlib.util.spec_from_file_location("px_provider", HERE / "provider.py")
    provider = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provider)
    assert provider.update_facts("pve1") == []   # never checked: nothing to say
    proxmox_update._set("pve1", facts={"pve_manager": "9.2.21"}, pending=0, pending_at=1793100000,
                        last_update={"at": 1793100000, "from": "9.1.4", "to": "9.2.21"},
                        reboot_waiting={"kernel": "7.0.14-25-pve"})
    line, reboot = provider.update_facts("pve1")
    assert line.startswith("Proxmox VE 9.2.21 · up to date (checked ") and "· last updated " in line
    assert ("PDT" in line or "PST" in line) and reboot == "reboot into kernel 7.0.14-25-pve waits on your OK (Approvals)"


def _updates_px(fleet, monkeypatch, tmp_path, running=(), answers=True):
    from hisho import proxmox_update
    monkeypatch.setenv("HISHO_STATE_DIR", str(tmp_path / "state"))
    started = []
    monkeypatch.setattr(proxmox_update.Deps, "active_units", lambda self: list(running))
    monkeypatch.setattr(proxmox_update.Deps, "start_unit",
                        lambda self, unit: started.append(unit) or subprocess.CompletedProcess([], 0, "", ""))
    px = addons.get("proxmox")
    addons.set_entry(px, "Pve1", {"url": "https://192.0.2.11:8006", "token": "HISHO_P1"})

    class FakeAPI:
        def __init__(self, *a, **k):
            if not answers:
                raise OSError("[Errno 113] No route to host")
        def get(self, path, **kw):
            return [] if path == "/cluster/resources" else [{"node": "pve1", "status": "online"}]
    monkeypatch.setattr("hisho.proxmox_create.ProxmoxAPI", FakeAPI)
    data = addons._fleet_data()
    data["proxmox_updates"] = {"hosts": {"pve1": {}}}
    addons._write_fleet(data)
    return px, ctx_for(px), started


def test_update_now_runs_the_guarded_update_and_says_how_it_reboots(fleet, monkeypatch, tmp_path):
    px, ctx, started = _updates_px(fleet, monkeypatch, tmp_path)
    [sec] = addons.details(px, ctx)
    [update] = [a for a in sec["items"][0]["actions"] if a["id"] == "update-now"]
    assert "reboots, and starts the same VMs again" in update["confirm"] and "never a new major" in update["confirm"]
    res = addons.act(px, "Pve1", "node-pve1", "update-now", ctx)
    assert res["ok"] and started == ["hisho-proxmox-update@pve1.service"]


def test_an_approve_server_says_its_reboot_waits_on_approvals(fleet, monkeypatch, tmp_path):
    import importlib.util
    _updates_px(fleet, monkeypatch, tmp_path)
    data = addons._fleet_data()
    data["proxmox_updates"] = {"hosts": {"pve1": {"reboot": "approve"}}}
    addons._write_fleet(data)
    spec = importlib.util.spec_from_file_location("px_provider", HERE / "provider.py")
    provider = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provider)
    assert "an Approvals card asks first." in provider.update_action("pve1", {})["confirm"]
    data["proxmox_updates"] = {"hosts": {"pve1": {"reboot": "approve", "reboot_note": "the NAS runs on it"}}}
    addons._write_fleet(data)
    assert "asks first (the NAS runs on it)." in provider.update_action("pve1", {})["confirm"]   # the owner's words


def test_a_sleeping_server_still_shows_its_updates_and_can_be_updated(fleet, monkeypatch, tmp_path):
    px, ctx, started = _updates_px(fleet, monkeypatch, tmp_path, answers=False)
    [sec] = addons.details(px, ctx)
    [row] = sec["items"]
    assert sec["state"] == "down" and row["id"] == "node-pve1"
    assert "Hisho wakes it first" in row["actions"][0]["confirm"]
    assert addons.act(px, "Pve1", "node-pve1", "update-now", ctx)["ok"] and started


def test_no_update_button_while_an_update_runs(fleet, monkeypatch, tmp_path):
    px, ctx, started = _updates_px(fleet, monkeypatch, tmp_path, running=["hisho-proxmox-update.service"])
    [sec] = addons.details(px, ctx)
    assert not [a for a in sec["items"][0]["actions"] if a["id"] == "update-now"]
    res = addons.act(px, "Pve1", "node-pve1", "update-now", ctx)
    assert not res["ok"] and "weekly run" in res["error"] and started == []


# ── Find it and Sign in once ──────────────────────────────────────────────

import json
import urllib.parse

import pytest

ADMIN_PRIVS = {p: 1 for p in ("VM.Audit", "VM.PowerMgmt", "VM.Allocate", "VM.Console", "VM.Config.Disk",
                              "VM.Config.CDROM", "VM.Config.CPU", "VM.Config.Memory", "VM.Config.Network",
                              "VM.Config.Cloudinit", "VM.Config.Options", "VM.Config.HWType", "Datastore.AllocateSpace",
                              "Datastore.AllocateTemplate", "Datastore.Audit", "Sys.Audit", "Sys.Modify", "Sys.PowerMgmt",
                              "Permissions.Modify")}   # no SDN.Use on this (pretend) older Proxmox

#: what a live Hisho already has on this Proxmox: its own user, roles, token and permission
LIVE = {"users": ["hisho@pve"], "roles": ["Hisho", "HishoCreate"], "tokens": {"hisho@pve": ["hisho"]},
        "acl": [{"path": "/", "type": "user", "ugid": "hisho@pve", "roleid": "Hisho", "propagate": 1},
                {"path": "/", "type": "user", "ugid": "hisho@pve", "roleid": "HishoCreate", "propagate": 1}]}


class FakePVE:
    """A Proxmox API in memory: users, roles, tokens and permissions, what Hisho sends and what it answers."""

    def __init__(self, password="right-pass", users=(), tokens=None, roles=(), acl=()):
        self.password = password
        self.users = ["root@pam", *users]
        self.roles = ["Administrator", "PVEAuditor", *roles]
        self.tokens = {u: list(t) for u, t in (tokens or {}).items()}
        self.acl = [dict(a) for a in acl]
        self.calls = []
        self.fail = None                       # (method, path) that answers 500

    def __call__(self, host, port, fingerprint, method, path, params, headers):
        self.calls.append((method, path, dict(params), dict(headers)))
        assert fingerprint == "AA:BB" and host == "192.0.2.10" and port == 8006
        ok = lambda data=None: (200, json.dumps({"data": data}))
        if path == "/access/ticket":
            if params["password"] != self.password:
                return 401, '{"data":null}'
            return ok({"ticket": "PVE:root@pam:TICKET", "CSRFPreventionToken": "CSRF1", "username": params["username"]})
        assert headers["Cookie"] == "PVEAuthCookie=PVE:root@pam:TICKET" and headers["CSRFPreventionToken"] == "CSRF1"
        if self.fail == (method, path):
            return 500, '{"data":null,"message":"boom"}'
        parts = path.split("/")
        if (method, path) == ("GET", "/access/roles/Administrator"):
            return ok(ADMIN_PRIVS)
        if path == "/access/roles":
            if method == "POST":
                assert params["roleid"] not in self.roles, "a role that exists was created again"
                self.roles.append(params["roleid"])
                return ok()
            return ok([{"roleid": r} for r in self.roles])
        if path.startswith("/access/roles/") and method == "DELETE":
            self.roles.remove(parts[3])
            return ok()
        if path == "/access/users":
            if method == "POST":
                assert params["userid"] not in self.users, "a user that exists was created again"
                self.users.append(params["userid"])
                return ok()
            return ok([{"userid": u} for u in self.users])
        if path == "/access/acl":
            if method == "GET":
                return ok(self.acl)
            kind, ugid = ("token", params["tokens"]) if "tokens" in params else ("user", params["users"])
            for role in str(params["roles"]).split(","):
                row = {"path": params["path"], "type": kind, "ugid": ugid, "roleid": role}
                if params.get("delete"):
                    self.acl = [a for a in self.acl if {k: a[k] for k in row} != row]
                else:
                    self.acl.append({**row, "propagate": params.get("propagate", 1)})
            return ok()
        if path.startswith("/access/users/"):
            user = urllib.parse.unquote(parts[3])
            if user not in self.users:
                return 500, '{"data":null}'
            if len(parts) == 4 and method == "DELETE":
                self.users.remove(user)
                self.tokens.pop(user, None)
                self.acl = [a for a in self.acl if a["ugid"] != user]
                return ok()
            if parts[4:] == ["token"] and method == "GET":
                return ok([{"tokenid": t} for t in self.tokens.get(user, [])])
            if len(parts) == 6 and parts[4] == "token":
                name = parts[5]
                if method == "POST":
                    assert name not in self.tokens.get(user, []), "a token that exists was made again"
                    self.tokens.setdefault(user, []).append(name)
                    return ok({"full-tokenid": f"{user}!{name}", "value": "1111-2222-secret", "info": {"privsep": 0}})
                if method == "DELETE":
                    self.tokens[user].remove(name)
                    return ok()
        raise AssertionError(f"unexpected {method} {path}")

    def writes(self):
        return [(m, p, prm) for m, p, prm, _ in self.calls if m in ("POST", "PUT", "DELETE") and p != "/access/ticket"]


@pytest.fixture
def pve(monkeypatch, addon):
    mod = addons.provider(addon)
    fake = FakePVE()
    monkeypatch.setattr(mod, "_learn_fingerprint", lambda host, port: "AA:BB")
    monkeypatch.setattr(mod, "_send", fake)
    monkeypatch.setattr("hisho.fleet_config.local_name", lambda: "Test_Box")    # this machine: test-box
    return mod, fake


def test_found_on_your_network_looks_for_proxmoxs_sign_in_page(addon):
    from hisho import connect as hconnect
    [probe] = addon.manifest["discover"]
    assert probe["http"] == "https://{ip}:8006/" and probe["fill"] == {"url": "{ip}"}
    tcp = lambda ip, port, banner="": port == 8006 and ip == "192.0.2.10"
    http = lambda url, match: url == "https://192.0.2.10:8006/" and "Proxmox Virtual Environment" in match
    got = hconnect.scan(addon, ["192.0.2.0/28"], tcp=tcp, http=http, rmcp=lambda *a: False, workers=2)
    assert got == [{"ip": "192.0.2.10", "title": "Proxmox at 192.0.2.10", "fill": {"url": "192.0.2.10"}}]
    assert addon.manifest["connect"]["form"] == ["url"]
    assert set(addon.manifest["connect"]["fields"]) == {"user", "password"}
    assert set(addon.manifest["disconnect"]["fields"]) == {"user", "password"}
    assert addon.manifest["entry_schema"]["made"] == "map?"


MADE = {"user": "hisho-test-box@pve", "roles": ["Hisho-test-box", "HishoCreate-test-box"], "tokens": ["hisho"],
        "acl": ["/"]}


def test_sign_in_once_makes_its_own_user_roles_and_token_named_after_this_machine(pve):
    mod, fake = pve
    out = mod.connect({"url": "192.0.2.10", "user": "root", "password": "right-pass"}, None)
    assert out["ok"]
    assert out["entry"] == {"url": "192.0.2.10", "token_id": "hisho-test-box@pve!hisho", "token_secret": "1111-2222-secret",
                            "fingerprint": "AA:BB", "made": MADE}
    assert "1111" not in json.dumps(out["entry"]["made"])                   # names only, never the secret
    assert fake.calls[0][2] == {"username": "root@pam", "password": "right-pass"}   # a bare root means root@pam
    w = fake.writes()
    roles = {prm["roleid"]: prm["privs"].split(",") for m, p, prm in w if p == "/access/roles"}
    assert roles["Hisho-test-box"] == ["VM.Audit", "VM.PowerMgmt", "Sys.Audit", "Sys.PowerMgmt"]
    assert "Sys.Modify" in roles["HishoCreate-test-box"] and "SDN.Use" not in roles["HishoCreate-test-box"]
    assert not any("Permissions" in x or "User" in x or "Sys.Console" == x for r in roles.values() for x in r)
    assert ("POST", "/access/users", {"userid": "hisho-test-box@pve", "comment": w[2][2]["comment"]}) in w
    assert ("PUT", "/access/acl", {"path": "/", "users": "hisho-test-box@pve",
                                   "roles": "Hisho-test-box,HishoCreate-test-box", "propagate": 1}) in w
    assert w[-1][1] == "/access/users/hisho-test-box%40pve/token/hisho" and w[-1][2]["privsep"] == 0
    assert not any(m == "PUT" and p.startswith("/access/roles") for m, p, _ in w)   # no role is ever rewritten
    assert "right-pass" not in json.dumps(out) and "user hisho-test-box@pve" in out["message"]


def test_next_to_a_live_hisho_nothing_of_its_is_changed_and_taken_names_get_a_suffix(pve):
    mod, fake = pve
    fake.users += [*LIVE["users"], "hisho-test-box@pve"]          # another Hisho on a machine named test-box, too
    fake.roles += [*LIVE["roles"], "HishoCreate-test-box"]
    fake.tokens = {u: list(t) for u, t in LIVE["tokens"].items()} | {"hisho-test-box@pve": ["hisho"]}
    fake.acl = [dict(a) for a in LIVE["acl"]]
    before = (list(fake.users), list(fake.roles), json.dumps(fake.tokens), json.dumps(fake.acl))
    out = mod.connect({"url": "192.0.2.10", "user": "root@pam", "password": "right-pass"}, None)
    assert out["entry"]["token_id"] == "hisho-test-box-2@pve!hisho"
    assert out["entry"]["made"] == {"user": "hisho-test-box-2@pve", "roles": ["Hisho-test-box-2", "HishoCreate-test-box-2"],
                                    "tokens": ["hisho"], "acl": ["/"]}
    existing = {"/access/roles/Hisho", "/access/roles/HishoCreate", "/access/roles/HishoCreate-test-box",
                "/access/users/hisho%40pve", "/access/users/hisho-test-box%40pve"}
    for m, p, prm in fake.writes():
        assert not any(p == e or p.startswith(e + "/") for e in existing), (m, p)
        assert m != "PUT" or p == "/access/acl" and prm["users"] == "hisho-test-box-2@pve" and not prm.get("delete")
        assert m != "DELETE"
        assert prm.get("roleid") not in before[1] and prm.get("userid") not in before[0]
    # everything the live Hisho had is still there, as it was
    assert set(before[0]) <= set(fake.users) and set(before[1]) <= set(fake.roles)
    assert all(a in fake.acl for a in json.loads(before[3]))
    assert fake.tokens["hisho@pve"] == ["hisho"] and fake.tokens["hisho-test-box@pve"] == ["hisho"]
    assert mod.names_for("souko", 3) == {"user": "hisho-souko-3@pve",
                                         "roles": {"Hisho-souko-3": "Hisho", "HishoCreate-souko-3": "HishoCreate"}}


def test_a_sign_in_that_fails_halfway_deletes_only_what_it_just_made(pve):
    mod, fake = pve
    fake.users, fake.roles = [*fake.users, *LIVE["users"]], [*fake.roles, *LIVE["roles"]]
    fake.acl = [dict(a) for a in LIVE["acl"]]
    fake.fail = ("POST", "/access/users/hisho-test-box%40pve/token/hisho")
    with pytest.raises(mod.ConnectError, match="undid what it had made"):
        mod.connect({"url": "192.0.2.10", "user": "root@pam", "password": "right-pass"}, None)
    assert fake.users == ["root@pam", "hisho@pve"] and fake.roles == ["Administrator", "PVEAuditor", "Hisho", "HishoCreate"]
    assert fake.acl == LIVE["acl"]


def test_remove_from_proxmox_deletes_exactly_what_was_made_and_nothing_else(pve):
    mod, fake = pve
    fake.users += LIVE["users"]
    fake.roles += LIVE["roles"]
    fake.tokens = {u: list(t) for u, t in LIVE["tokens"].items()}
    fake.acl = [dict(a) for a in LIVE["acl"]]
    entry = mod.connect({"url": "192.0.2.10", "user": "root@pam", "password": "right-pass"}, None)["entry"]
    fake.users.append("someone@pve")
    fake.tokens["someone@pve"] = ["hisho"]                               # a token named hisho of another user
    fake.calls.clear()
    out = mod.disconnect(entry, {"user": "root@pam", "password": "right-pass"}, None)
    assert out["ok"] and "right-pass" not in out["message"]
    deletes = [(m, p, prm) for m, p, prm in fake.writes()]
    assert [(m, p) for m, p, _ in deletes] == [
        ("DELETE", "/access/users/hisho-test-box%40pve/token/hisho"),
        ("PUT", "/access/acl"), ("PUT", "/access/acl"),
        ("DELETE", "/access/users/hisho-test-box%40pve"),
        ("DELETE", "/access/roles/Hisho-test-box"), ("DELETE", "/access/roles/HishoCreate-test-box")]
    assert all(prm["delete"] == 1 and prm["users"] == "hisho-test-box@pve" for m, p, prm in deletes if m == "PUT")
    assert fake.users == ["root@pam", "hisho@pve", "someone@pve"]
    assert fake.roles == ["Administrator", "PVEAuditor", "Hisho", "HishoCreate"]
    assert fake.tokens == {"hisho@pve": ["hisho"], "someone@pve": ["hisho"]} and fake.acl == LIVE["acl"]
    assert "deleted token hisho" in out["message"] and "user hisho-test-box@pve" in out["message"]


def test_remove_says_what_was_already_gone_and_keeps_a_role_someone_else_is_given(pve):
    mod, fake = pve
    entry = mod.connect({"url": "192.0.2.10", "user": "root@pam", "password": "right-pass"}, None)["entry"]
    fake.tokens["hisho-test-box@pve"].remove("hisho")                     # the owner removed the token by hand
    fake.acl.append({"path": "/vms", "type": "user", "ugid": "root@pam", "roleid": "Hisho-test-box", "propagate": 1})
    out = mod.disconnect(entry, {"user": "root@pam", "password": "right-pass"}, None)
    assert out["ok"]
    assert "already gone: token hisho" in out["message"] and "kept role Hisho-test-box" in out["message"]
    assert "Hisho-test-box" in fake.roles and "HishoCreate-test-box" not in fake.roles
    assert fake.users == ["root@pam"]
    fake.calls.clear()
    again = mod.disconnect(entry, {"user": "root@pam", "password": "right-pass"}, None)   # all gone now: fine
    assert again["ok"] and "already gone: user hisho-test-box@pve" in again["message"]
    assert [(m, p) for m, p, _ in fake.writes()] == []


def test_remove_refuses_a_changed_certificate_and_names_hisho_never_made(pve, monkeypatch):
    mod, fake = pve
    entry = {"url": "192.0.2.10", "fingerprint": "CC:DD", "made": MADE}
    with pytest.raises(mod.ConnectError, match="not the one Hisho remembers"):
        mod.disconnect(entry, {"user": "root@pam", "password": "right-pass"}, None)
    assert fake.calls == []                                               # the login was never sent
    with pytest.raises(mod.ConnectError, match="not a user Hisho makes"):
        mod.disconnect({"url": "192.0.2.10", "made": {"user": "root@pam"}},
                       {"user": "root@pam", "password": "right-pass"}, None)
    with pytest.raises(mod.ConnectError, match="not one Hisho makes"):
        mod.disconnect({"url": "192.0.2.10", "made": {"user": "hisho-x@pve", "roles": ["Administrator"]}},
                       {"user": "root@pam", "password": "right-pass"}, None)
    assert fake.writes() == []
    assert not mod.disconnect({"url": "192.0.2.10", "token_id": "me@pam!t"}, {}, None)["ok"]   # made by hand


def test_a_wrong_password_says_so_without_the_password(pve):
    mod, fake = pve
    with pytest.raises(mod.ConnectError) as e:
        mod.connect({"url": "192.0.2.10", "user": "root@pam", "password": "wrong-pass"}, None)
    assert "refused the sign-in for root@pam" in str(e.value) and "wrong-pass" not in str(e.value)
    assert fake.writes() == []                               # nothing made


def test_the_machine_name_falls_back_to_the_hostname(addon, monkeypatch):
    mod = addons.provider(addon)
    monkeypatch.setattr("hisho.fleet_config.local_name", lambda: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr("socket.gethostname", lambda: "My_Box.example.lan")
    assert mod._machine() == "my-box"


def test_two_factor_and_an_unreachable_server_are_said_plainly(monkeypatch, addon):
    mod = addons.provider(addon)
    monkeypatch.setattr(mod, "_learn_fingerprint", lambda h, p: (_ for _ in ()).throw(ConnectionRefusedError()))
    with pytest.raises(mod.ConnectError, match="can't reach 192.0.2.10:8006"):
        mod.connect({"url": "192.0.2.10", "user": "root@pam", "password": "x"}, None)
    monkeypatch.setattr(mod, "_learn_fingerprint", lambda h, p: "AA:BB")
    monkeypatch.setattr(mod, "_send", lambda *a: (200, json.dumps({"data": {"ticket": "t", "NeedTFA": 1}})))
    with pytest.raises(mod.ConnectError, match="two-factor"):
        mod.connect({"url": "192.0.2.10", "user": "root@pam", "password": "x"}, None)


def test_sign_in_once_and_remove_through_core(fleet, pve, monkeypatch, tmp_path):
    from hisho import connect as hconnect
    monkeypatch.setattr(addons, "LOCAL_SECRETS", str(tmp_path / "secrets.env"))
    mod, fake = pve
    seen = []

    class FakeAPI:
        def __init__(self, url, token, fingerprint=None, timeout=8):
            seen.append((url, token, fingerprint))
        def get(self, path, **kw):
            return [{"node": "pve1", "status": "online"}] if path == "/nodes" else []
    monkeypatch.setattr("hisho.proxmox_create.ProxmoxAPI", FakeAPI)
    px = addons.get("proxmox")
    out = hconnect.connect(px, "pve1", {"url": "192.0.2.10", "user": "root@pam", "password": "right-pass"})
    assert out["ok"], out
    assert seen[-1] == ("https://192.0.2.10:8006", "hisho-test-box@pve!hisho=1111-2222-secret", "AA:BB")
    e = addons.read_config("proxmox")["entries"]["pve1"]
    assert e["token_id"] == "hisho-test-box@pve!hisho" and e["fingerprint"] == "AA:BB" and "password" not in e
    assert e["made"] == MADE
    assert "1111-2222-secret" not in Path(addons.config_path("proxmox")).read_text()
    gone = hconnect.disconnect(px, "pve1", {"user": "root@pam", "password": "right-pass"})
    assert gone["ok"], gone
    assert "pve1" not in (addons.read_config("proxmox").get("entries") or {})
    assert fake.users == ["root@pam"] and fake.roles == ["Administrator", "PVEAuditor"]
