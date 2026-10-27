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
