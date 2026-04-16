"""
NexusHV API — Unit & Integration Tests
Run: python -m pytest tests/ -v
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from fastapi.testclient import TestClient
from nexushv_api import app, create_token, decode_token, init_db

client = TestClient(app)


class TestHealth:
    def test_health_endpoint(self):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] in ("ok", "warning", "critical")
        assert data["product"] == "NexusHV"
        assert data["version"] == "2.0.0"
        assert "checks" in data

    def test_mode_endpoint(self):
        r = client.get("/api/mode")
        assert r.status_code == 200
        data = r.json()
        assert "demo_mode" in data
        assert "ai_available" in data
        assert data["version"] == "2.0.0"


class TestAuth:
    def test_login_success(self):
        r = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert r.status_code == 200
        data = r.json()
        assert "token" in data
        assert data["username"] == "admin"
        assert data["role"] == "admin"

    def test_login_failure(self):
        r = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        assert r.status_code == 401

    def test_me_authenticated(self):
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        token = login.json()["token"]
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["username"] == "admin"

    def test_me_unauthenticated(self):
        r = client.get("/api/auth/me")
        assert r.status_code == 401

    def test_jwt_creation_and_decode(self):
        token = create_token("testuser", "operator")
        decoded = decode_token(token)
        assert decoded.username == "testuser"
        assert decoded.role == "operator"

    def test_create_user_as_admin(self):
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        token = login.json()["token"]
        r = client.post(
            "/api/auth/users",
            json={"username": "testop", "password": "testpassword", "role": "operator"},
            headers={"Authorization": f"Bearer {token}"},
        )
        # May already exist from previous test run
        assert r.status_code in (200, 409)

    def test_list_users_as_admin(self):
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        token = login.json()["token"]
        r = client.get("/api/auth/users", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        users = r.json()
        assert len(users) >= 1
        assert any(u["username"] == "admin" for u in users)


class TestVMs:
    def test_list_vms(self):
        r = client.get("/api/vms")
        assert r.status_code == 200
        vms = r.json()
        assert len(vms) >= 1
        assert "name" in vms[0]
        assert "state" in vms[0]

    def test_get_vm(self):
        r = client.get("/api/vms/prod-db-primary")
        assert r.status_code == 200
        vm = r.json()
        assert vm["name"] == "prod-db-primary"
        assert vm["state"] == "poweredOn"

    def test_get_vm_not_found(self):
        r = client.get("/api/vms/nonexistent-vm")
        assert r.status_code == 404

    def test_vm_action_stop(self):
        r = client.post("/api/vms/prod-web-01/action", json={"action": "stop"})
        assert r.status_code == 200
        assert r.json()["action"] == "stop"
        # Restart it
        client.post("/api/vms/prod-web-01/action", json={"action": "start"})

    def test_vm_action_invalid(self):
        r = client.post("/api/vms/prod-web-01/action", json={"action": "explode"})
        assert r.status_code == 400

    def test_create_vm(self):
        r = client.post("/api/vms", json={
            "name": "test-vm-001",
            "os": "ubuntu22.04",
            "cpu": 2,
            "ram_gb": 4,
            "disk_gb": 50,
        })
        assert r.status_code == 200
        assert r.json()["status"] == "created"
        # Clean up
        client.delete("/api/vms/test-vm-001")

    def test_create_vm_invalid_name(self):
        r = client.post("/api/vms", json={
            "name": "invalid name with spaces!",
            "cpu": 2,
            "ram_gb": 4,
            "disk_gb": 50,
        })
        assert r.status_code == 422  # validation error

    def test_create_vm_duplicate(self):
        client.post("/api/vms", json={"name": "dup-test-vm", "cpu": 1, "ram_gb": 1, "disk_gb": 10})
        r = client.post("/api/vms", json={"name": "dup-test-vm", "cpu": 1, "ram_gb": 1, "disk_gb": 10})
        assert r.status_code == 409
        client.delete("/api/vms/dup-test-vm")

    def test_delete_vm(self):
        client.post("/api/vms", json={"name": "to-delete-vm", "cpu": 1, "ram_gb": 1, "disk_gb": 10})
        r = client.delete("/api/vms/to-delete-vm")
        assert r.status_code == 200


class TestSnapshots:
    def test_list_snapshots(self):
        r = client.get("/api/vms/prod-db-primary/snapshots")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_create_snapshot(self):
        r = client.post("/api/vms/prod-db-primary/snapshots", json={
            "name": "test-snap", "description": "Test snapshot"
        })
        assert r.status_code == 200

    def test_revert_snapshot(self):
        r = client.post("/api/vms/prod-db-primary/snapshots/test-snap/revert")
        assert r.status_code == 200


class TestHost:
    def test_local_host_info(self):
        r = client.get("/api/hosts/local")
        assert r.status_code == 200
        data = r.json()
        assert "hostname" in data
        assert "cpu_count" in data
        assert "ram_total_gb" in data
        assert "cpu_pct" in data
        assert "disk_total_gb" in data
        assert "load_avg_1m" in data

    def test_system_metrics(self):
        r = client.get("/api/metrics/system")
        assert r.status_code == 200
        data = r.json()
        assert "cpu" in data
        assert "memory" in data
        assert "disk_io" in data
        assert "network" in data


class TestStorage:
    def test_list_storage(self):
        r = client.get("/api/storage")
        assert r.status_code == 200
        pools = r.json()
        assert len(pools) >= 1
        assert "name" in pools[0]
        assert "capacity_gb" in pools[0]


class TestNetworks:
    def test_list_networks(self):
        r = client.get("/api/networks")
        assert r.status_code == 200
        nets = r.json()
        assert len(nets) >= 1
        assert "name" in nets[0]


class TestMigration:
    def test_migrate_vm(self):
        r = client.post("/api/vms/prod-web-01/migrate", json={
            "dest_host": "10.0.1.11",
            "live": True,
        })
        assert r.status_code == 200
        assert r.json()["status"] == "migrated"


class TestAI:
    def test_ai_health(self):
        r = client.get("/api/ai/health")
        assert r.status_code == 200
        data = r.json()
        assert "ai_module" in data

    def test_ai_chat(self):
        r = client.post("/api/ai/chat", json={"message": "What is KVM?"})
        assert r.status_code == 200
        assert "response" in r.json()

    def test_ai_scan(self):
        r = client.post("/api/ai/scan")
        assert r.status_code == 200
        assert "issues" in r.json()


class TestAlerts:
    def test_get_alerts(self):
        r = client.get("/api/alerts")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestAudit:
    def test_get_audit_log(self):
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        token = login.json()["token"]
        r = client.get("/api/audit", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestSettings:
    def test_get_settings(self):
        r = client.get("/api/settings")
        assert r.status_code == 200

    def test_set_setting(self):
        r = client.put("/api/settings/test_key?value=test_value")
        assert r.status_code == 200


class TestVMNotes:
    def test_get_notes(self):
        r = client.get("/api/vms/prod-db-primary/notes")
        assert r.status_code == 200

    def test_set_notes(self):
        r = client.put("/api/vms/prod-db-primary/notes?notes=Test%20note&tags=prod,db")
        assert r.status_code == 200


class TestMetricsHistory:
    def test_get_history(self):
        r = client.get("/api/metrics/history?metric_type=host_cpu&hours=1")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestConsole:
    def test_get_console(self):
        r = client.get("/api/vms/prod-db-primary/console")
        assert r.status_code == 200
        data = r.json()
        assert "port" in data
        assert "novnc_url" in data


class TestDiskManagement:
    def test_list_vm_disks(self):
        r = client.get("/api/vms/prod-db-primary/disks")
        assert r.status_code == 200
        disks = r.json()
        assert len(disks) >= 1
        assert "device" in disks[0]

    def test_list_disks_not_found(self):
        r = client.get("/api/vms/nonexistent/disks")
        assert r.status_code == 404


class TestBatchOperations:
    def test_batch_stop(self):
        r = client.post("/api/batch/vm-action", json={
            "vm_names": ["k8s-worker-01", "backup-appliance"],
            "action": "stop"
        })
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        assert data["succeeded"] >= 0
        # Restart them
        client.post("/api/batch/vm-action", json={
            "vm_names": ["k8s-worker-01", "backup-appliance"],
            "action": "start"
        })

    def test_batch_invalid_action(self):
        r = client.post("/api/batch/vm-action", json={
            "vm_names": ["prod-web-01"],
            "action": "explode"
        })
        assert r.status_code == 400


class TestExport:
    def test_export_vm_config(self):
        r = client.get("/api/vms/prod-db-primary/export")
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "prod-db-primary"
        assert "exported_at" in data

    def test_export_not_found(self):
        r = client.get("/api/vms/nonexistent/export")
        assert r.status_code == 404


class TestClone:
    def test_clone_vm(self):
        r = client.post("/api/vms/prod-web-01/clone", json={"new_name": "prod-web-01-clone", "full_clone": True})
        assert r.status_code == 200
        assert r.json()["status"] == "cloned"
        # Clean up
        client.delete("/api/vms/prod-web-01-clone")

    def test_clone_nonexistent(self):
        r = client.post("/api/vms/nonexistent/clone", json={"new_name": "clone-test"})
        assert r.status_code == 404

    def test_clone_duplicate_name(self):
        r = client.post("/api/vms/prod-web-01/clone", json={"new_name": "prod-db-primary"})
        assert r.status_code == 409


class TestResize:
    def test_resize_cpu(self):
        r = client.put("/api/vms/prod-web-01/resize", json={"cpu": 8})
        assert r.status_code == 200
        assert r.json()["changes"]["cpu"] == 8

    def test_resize_ram(self):
        r = client.put("/api/vms/prod-web-01/resize", json={"ram_mb": 32768})
        assert r.status_code == 200
        assert r.json()["changes"]["ram_mb"] == 32768

    def test_resize_both(self):
        r = client.put("/api/vms/prod-web-01/resize", json={"cpu": 4, "ram_mb": 16384})
        assert r.status_code == 200

    def test_resize_nonexistent(self):
        r = client.put("/api/vms/nonexistent/resize", json={"cpu": 2})
        assert r.status_code == 404


class TestPrometheus:
    def test_prometheus_metrics(self):
        r = client.get("/metrics")
        assert r.status_code == 200
        assert b"nexushv_" in r.content


class TestWebhooks:
    def test_list_webhooks(self):
        r = client.get("/api/webhooks")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_add_webhook(self):
        r = client.post("/api/webhooks?url=http://example.com/hook&events=alert,vm_action")
        assert r.status_code == 200
        assert r.json()["status"] == "created"

    def test_remove_webhook(self):
        client.post("/api/webhooks?url=http://example.com/to-remove")
        r = client.delete("/api/webhooks?url=http://example.com/to-remove")
        assert r.status_code == 200


class TestRightSizing:
    def test_rightsizing_recommendations(self):
        r = client.get("/api/recommendations/rightsizing")
        assert r.status_code == 200
        data = r.json()
        assert "recommendations" in data
        assert "summary" in data


class TestDashboard:
    def test_dashboard_overview(self):
        r = client.get("/api/dashboard/overview")
        assert r.status_code == 200
        data = r.json()
        assert "vms" in data
        assert "host" in data
        assert "resources" in data
        assert "alerts" in data


class TestDRS:
    def test_drs_recommendations(self):
        r = client.get("/api/recommendations/drs")
        assert r.status_code == 200
        data = r.json()
        assert "recommendations" in data
        assert "cluster_balance" in data
        assert "hosts" in data

    def test_cluster_topology(self):
        r = client.get("/api/topology")
        assert r.status_code == 200
        data = r.json()
        assert "datacenter" in data
        assert "clusters" in data
        assert len(data["clusters"]) >= 1


class TestHAProxy:
    def test_ha_status(self):
        r = client.get("/api/ha/status")
        assert r.status_code == 200
        # May return error if HA daemon not running

    def test_ha_health(self):
        r = client.get("/api/ha/health")
        assert r.status_code == 200

    def test_ha_events(self):
        r = client.get("/api/ha/events")
        assert r.status_code == 200
