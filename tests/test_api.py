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

    def test_list_vms_filter_state(self):
        r = client.get("/api/vms?state=poweredOn")
        assert r.status_code == 200
        vms = r.json()
        assert all(v["state"] == "poweredOn" for v in vms)

    def test_list_vms_search(self):
        r = client.get("/api/vms?search=prod")
        assert r.status_code == 200
        vms = r.json()
        assert all("prod" in v["name"].lower() for v in vms)

    def test_list_vms_sort(self):
        r = client.get("/api/vms?sort=-cpu_pct")
        assert r.status_code == 200
        vms = r.json()
        assert len(vms) >= 2

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


class TestTemplates:
    def test_list_templates(self):
        r = client.get("/api/templates")
        assert r.status_code == 200
        data = r.json()
        assert "templates" in data
        assert data["total"] >= 1

    def test_deploy_template(self):
        r = client.post("/api/templates/template-ubuntu-22/deploy?new_name=deployed-test-01")
        assert r.status_code == 200
        assert r.json()["status"] == "deployed"
        client.delete("/api/vms/deployed-test-01")

    def test_deploy_with_overrides(self):
        r = client.post("/api/templates/template-ubuntu-22/deploy?new_name=deployed-test-02&cpu=8&ram_gb=16")
        assert r.status_code == 200
        assert r.json()["cpu"] == 8
        client.delete("/api/vms/deployed-test-02")


class TestSLA:
    def test_sla_status(self):
        r = client.get("/api/sla/status?hours=24")
        assert r.status_code == 200
        data = r.json()
        assert "uptime_pct" in data
        assert "sla_targets" in data
        assert data["uptime_pct"] >= 0
        assert data["uptime_pct"] <= 100


class TestHealthTrend:
    def test_health_trend(self):
        r = client.get("/api/health/trend?hours=24")
        assert r.status_code == 200
        data = r.json()
        assert "current_score" in data
        assert "average_score" in data


class TestNetworkPolicies:
    def test_list_policies(self):
        r = client.get("/api/network/policies")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_create_policy(self):
        r = client.post("/api/network/policies?name=allow-web-to-db&source_vm=prod-web-01&dest_vm=prod-db-primary&protocol=tcp&port=5432&action=allow")
        assert r.status_code == 200
        assert r.json()["status"] == "created"


class TestEventCorrelation:
    def test_correlated_events(self):
        # Generate some events first
        client.post("/api/vms/prod-web-01/action", json={"action": "stop"})
        client.post("/api/vms/prod-web-01/action", json={"action": "start"})
        r = client.get("/api/events/correlated?hours=1")
        assert r.status_code == 200
        data = r.json()
        assert "correlations" in data
        assert "total_events" in data


class TestRemediation:
    def test_remediate_with_issue(self):
        r = client.post("/api/ai/remediate", json={
            "issue": "Host CPU at 95%, what should I do?",
            "auto_execute": False,
        })
        assert r.status_code == 200
        data = r.json()
        assert "remediation" in data


class TestFederation:
    def test_list_clusters(self):
        r = client.get("/api/federation/clusters")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_register_cluster(self):
        r = client.post("/api/federation/clusters?name=dc2&url=http://10.1.1.10:8080")
        assert r.status_code == 200
        assert r.json()["status"] == "registered"

    def test_federation_overview(self):
        r = client.get("/api/federation/overview")
        assert r.status_code == 200
        data = r.json()
        assert "clusters" in data
        assert "total_vms" in data
        assert data["total_clusters"] >= 1


class TestGPU:
    def test_list_gpus(self):
        r = client.get("/api/hosts/local/gpus")
        assert r.status_code == 200
        data = r.json()
        assert "gpus" in data
        assert "total" in data


class TestComplianceDashboard:
    def test_compliance_dashboard(self):
        r = client.get("/api/compliance/dashboard")
        assert r.status_code == 200
        data = r.json()
        assert "compliance_score" in data
        assert "grade" in data
        assert "controls" in data
        assert "frameworks_covered" in data
        assert len(data["frameworks_covered"]) >= 4

    def test_compliance_has_controls(self):
        r = client.get("/api/compliance/dashboard")
        data = r.json()
        assert "access_control" in data["controls"]
        assert "audit_trail" in data["controls"]
        assert "encryption_transit" in data["controls"]


class TestBackup:
    def test_create_backup(self):
        r = client.post("/api/backup/create", json={
            "vm_name": "prod-db-primary", "destination": "/backup", "compress": True
        })
        assert r.status_code == 200
        assert r.json()["status"] == "completed"

    def test_list_backups(self):
        r = client.get("/api/backup/list")
        assert r.status_code == 200
        assert "backups" in r.json()

    def test_list_backups_filtered(self):
        r = client.get("/api/backup/list?vm_name=prod-db-primary")
        assert r.status_code == 200


class TestResourceLimits:
    def test_limits(self):
        r = client.get("/api/limits")
        assert r.status_code == 200
        data = r.json()
        assert "cpu" in data
        assert "memory" in data
        assert "allocated_vcpu" in data["cpu"]


class TestPagination:
    def test_paginated_list(self):
        r = client.get("/api/paginated/vms?page=1&per_page=3")
        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        assert "pagination" in data
        assert data["pagination"]["per_page"] == 3

    def test_paginated_page2(self):
        r = client.get("/api/paginated/vms?page=2&per_page=3")
        assert r.status_code == 200
        assert r.json()["pagination"]["page"] == 2


class TestInventoryExport:
    def test_export(self):
        r = client.get("/api/export/inventory")
        assert r.status_code == 200
        data = r.json()
        assert "cluster" in data
        assert "hosts" in data
        assert "virtual_machines" in data
        assert "totals" in data
        assert data["totals"]["vms"] >= 1


class TestAIAnalysis:
    def test_analyze_nonexistent_alert(self):
        r = client.post("/api/ai/analyze-alert?alert_id=99999")
        assert r.status_code == 404


class TestDependencyGraph:
    def test_dependency_graph(self):
        r = client.get("/api/dependencies/vms")
        assert r.status_code == 200
        data = r.json()
        assert "nodes" in data
        assert "edges" in data
        assert data["total_vms"] >= 1


class TestAllRecommendations:
    def test_all_recommendations(self):
        r = client.get("/api/recommendations/all")
        assert r.status_code == 200
        data = r.json()
        assert "sources" in data
        assert "rightsizing" in data["sources"]
        assert "drs" in data["sources"]
        assert "security" in data["sources"]
        assert "total_recommendations" in data


class TestHostProcesses:
    def test_list_processes(self):
        r = client.get("/api/hosts/local/processes?top_n=10")
        assert r.status_code == 200
        data = r.json()
        assert "processes" in data
        assert "total_processes" in data
        assert len(data["processes"]) <= 10


class TestVMNetwork:
    def test_vm_network_stats(self):
        r = client.get("/api/vms/prod-db-primary/network")
        assert r.status_code == 200
        data = r.json()
        assert data["vm"] == "prod-db-primary"
        assert "interfaces" in data
        assert len(data["interfaces"]) >= 1

    def test_vm_network_not_found(self):
        r = client.get("/api/vms/nonexistent/network")
        assert r.status_code == 404


class TestVMUptime:
    def test_vm_uptime(self):
        r = client.get("/api/vms/prod-db-primary/uptime")
        assert r.status_code == 200
        data = r.json()
        assert "uptime_seconds" in data
        assert "uptime_human" in data


class TestClusterSummary:
    def test_cluster_summary(self):
        r = client.get("/api/cluster/summary")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] in ("healthy", "degraded")
        assert "vms_running" in data


class TestProbes:
    def test_liveness(self):
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "alive"

    def test_readiness(self):
        r = client.get("/readyz")
        assert r.status_code == 200
        assert r.json()["status"] == "ready"


class TestVMImport:
    def test_import_vm(self):
        r = client.post("/api/vms/import", json={
            "name": "imported-vm-01",
            "source_path": "/tmp/test.vmdk",
            "cpu": 2,
            "ram_gb": 4,
        })
        assert r.status_code == 200
        assert r.json()["status"] == "imported"
        client.delete("/api/vms/imported-vm-01")


class TestDiskResize:
    def test_resize_disk(self):
        r = client.post("/api/vms/prod-db-primary/disks/vda/resize", json={"size_gb": 600})
        assert r.status_code == 200
        assert r.json()["new_size_gb"] == 600

    def test_resize_nonexistent(self):
        r = client.post("/api/vms/nonexistent/disks/vda/resize", json={"size_gb": 100})
        assert r.status_code == 404


class TestHostInterfaces:
    def test_list_interfaces(self):
        r = client.get("/api/hosts/local/interfaces")
        assert r.status_code == 200
        data = r.json()
        assert "interfaces" in data
        assert data["count"] >= 1
        assert "io" in data["interfaces"][0]


class TestVMComparison:
    def test_compare_vms(self):
        r = client.get("/api/compare/vms?names=prod-db-primary,prod-web-01")
        assert r.status_code == 200
        data = r.json()
        assert len(data["vms"]) == 2
        assert "most_efficient" in data

    def test_compare_no_names(self):
        r = client.get("/api/compare/vms?names=")
        assert r.status_code == 400


class TestComplianceExport:
    def test_audit_export(self):
        r = client.get("/api/compliance/audit-export?hours=24")
        assert r.status_code == 200
        data = r.json()
        assert "export_info" in data
        assert "security_posture" in data
        assert "audit_entries" in data


class TestTags:
    def test_list_tags(self):
        # Set a tag first
        client.put("/api/vms/prod-db-primary/notes?notes=test&tags=database,critical")
        r = client.get("/api/tags")
        assert r.status_code == 200
        assert "database" in r.json()["tags"]

    def test_vms_by_tag(self):
        client.put("/api/vms/prod-db-primary/notes?notes=test&tags=database,critical")
        r = client.get("/api/tags/database/vms")
        assert r.status_code == 200


class TestCostEstimation:
    def test_cost_estimate(self):
        r = client.get("/api/costs/estimate")
        assert r.status_code == 200
        data = r.json()
        assert "costs" in data
        assert "total_monthly" in data
        assert data["total_monthly"] > 0

    def test_custom_pricing(self):
        r = client.get("/api/costs/estimate?cost_per_vcpu_month=20&cost_per_gb_ram_month=10")
        assert r.status_code == 200
        assert r.json()["total_monthly"] > 0


class TestSecurityPosture:
    def test_security_posture(self):
        r = client.get("/api/security/posture")
        assert r.status_code == 200
        data = r.json()
        assert "score" in data
        assert "grade" in data
        assert "checks" in data
        assert len(data["checks"]) >= 3


class TestAnomalyDetection:
    def test_detect_anomalies(self):
        r = client.get("/api/anomalies")
        assert r.status_code == 200
        data = r.json()
        assert "anomalies" in data
        assert "analyzed_metrics" in data


class TestPowerSchedules:
    def test_list_schedules(self):
        r = client.get("/api/power-schedules")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_create_schedule(self):
        r = client.post("/api/power-schedules", json={
            "vm_name": "dev-sandbox-01", "action": "start", "schedule": "08:00"
        })
        assert r.status_code == 200

    def test_delete_schedule(self):
        client.post("/api/power-schedules", json={
            "vm_name": "test-sched-vm", "action": "stop", "schedule": "22:00"
        })
        r = client.delete("/api/power-schedules/test-sched-vm")
        assert r.status_code == 200


class TestReports:
    def test_resource_usage_report(self):
        r = client.get("/api/reports/resource-usage?hours=24")
        assert r.status_code == 200
        data = r.json()
        assert "host" in data
        assert "cpu_stats" in data
        assert "ram_stats" in data
        assert "vms" in data
        assert "activity" in data


class TestAIHistory:
    def test_get_history(self):
        r = client.get("/api/ai/history")
        assert r.status_code == 200
        assert "history" in r.json()

    def test_reset_conversation(self):
        r = client.post("/api/ai/reset")
        assert r.status_code == 200
        assert r.json()["history_length"] == 0


class TestSearch:
    def test_search_vms(self):
        r = client.get("/api/search?q=prod")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] >= 1
        assert any(res["type"] == "vm" for res in data["results"])

    def test_search_short_query(self):
        r = client.get("/api/search?q=x")
        assert r.status_code == 200
        assert r.json()["count"] == 0

    def test_search_no_results(self):
        r = client.get("/api/search?q=zzzznonexistent")
        assert r.status_code == 200
        assert r.json()["count"] == 0


class TestActivitySummary:
    def test_activity_summary(self):
        r = client.get("/api/activity/summary?hours=24")
        assert r.status_code == 200
        data = r.json()
        assert "total_actions" in data
        assert "top_actions" in data


class TestVMTimeline:
    def test_vm_timeline(self):
        # Generate some activity first
        client.post("/api/vms/prod-web-01/action", json={"action": "stop"})
        client.post("/api/vms/prod-web-01/action", json={"action": "start"})
        r = client.get("/api/vms/prod-web-01/timeline")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestAPIInfo:
    def test_api_info(self):
        r = client.get("/api/info")
        assert r.status_code == 200
        data = r.json()
        assert data["product"] == "NexusHV"
        assert data["version"] == "2.0.0"
        assert "features" in data
        assert data["endpoints_count"] >= 50


class TestEvents:
    def test_get_events(self):
        r = client.get("/api/events")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_get_events_filtered(self):
        r = client.get("/api/events?event_type=vm")
        assert r.status_code == 200


class TestDiagnostics:
    def test_diagnostics(self):
        r = client.get("/api/diagnostics")
        assert r.status_code == 200
        data = r.json()
        assert "checks" in data
        assert "overall" in data
        assert "score" in data
        assert len(data["checks"]) >= 4


class TestRequestID:
    def test_request_id_header(self):
        r = client.get("/health")
        assert "x-request-id" in r.headers
        assert len(r.headers["x-request-id"]) > 10


class TestPrometheus:
    def test_prometheus_metrics(self):
        r = client.get("/metrics")
        assert r.status_code == 200
        assert b"nexushv_" in r.content

    def test_prometheus_contains_key_metrics(self):
        r = client.get("/metrics")
        content = r.content.decode()
        assert "nexushv_api_requests_total" in content
        assert "nexushv_host_cpu_percent" in content


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


class TestPasswordChange:
    def test_refresh_token(self):
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        token = login.json()["token"]
        r = client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert "token" in r.json()


class TestSnapshotPolicies:
    def test_list_policies(self):
        r = client.get("/api/snapshot-policies")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_create_policy(self):
        r = client.post("/api/snapshot-policies", json={
            "vm_name": "prod-db-primary", "interval_hours": 24, "max_snapshots": 7
        })
        assert r.status_code == 200
        assert r.json()["status"] == "created"

    def test_delete_policy(self):
        client.post("/api/snapshot-policies", json={"vm_name": "test-policy-vm", "interval_hours": 12})
        r = client.delete("/api/snapshot-policies/test-policy-vm")
        assert r.status_code == 200


class TestStorageAnalytics:
    def test_analytics(self):
        r = client.get("/api/storage/analytics")
        assert r.status_code == 200
        data = r.json()
        assert "summary" in data
        assert "thin_provisioning" in data
        assert "projections" in data


class TestTasks:
    def test_list_tasks(self):
        r = client.get("/api/tasks")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestVMResourceSummary:
    def test_resource_summary(self):
        r = client.get("/api/vms/summary/resources")
        assert r.status_code == 200
        data = r.json()
        assert "total_vms" in data
        assert "resources" in data
        assert "top_cpu_vms" in data


class TestNetworkTopology:
    def test_network_topology(self):
        r = client.get("/api/network/topology")
        assert r.status_code == 200
        data = r.json()
        assert "bridges" in data
        assert "connections" in data
        assert len(data["bridges"]) >= 1


class TestHostProfile:
    def test_host_profile(self):
        r = client.get("/api/hosts/local/profile")
        assert r.status_code == 200
        data = r.json()
        assert "cpu" in data
        assert "memory" in data
        assert "storage" in data
        assert "network_interfaces" in data


class TestMaintenanceMode:
    def test_get_maintenance_status(self):
        r = client.get("/api/hosts/local/maintenance")
        assert r.status_code == 200
        assert "maintenance_mode" in r.json()

    def test_enter_exit_maintenance(self):
        r = client.post("/api/hosts/local/maintenance")
        assert r.status_code == 200
        r = client.get("/api/hosts/local/maintenance")
        assert r.json()["maintenance_mode"] is True
        r = client.post("/api/hosts/local/maintenance/exit")
        assert r.status_code == 200
        r = client.get("/api/hosts/local/maintenance")
        assert r.json()["maintenance_mode"] is False


class TestCapacityPlanning:
    def test_capacity_planning(self):
        r = client.get("/api/planning/capacity")
        assert r.status_code == 200
        data = r.json()
        assert "host" in data
        assert "allocation" in data
        assert "headroom" in data
        assert data["total_vms"] >= 1


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


# ── Additional Edge Case Tests ────────────────────────────────────────────

class TestEdgeCases:
    """Edge cases and error handling tests."""

    def test_health_contains_version(self):
        r = client.get("/health")
        assert r.json()["version"] == "2.0.0"

    def test_health_contains_checks(self):
        r = client.get("/health")
        checks = r.json()["checks"]
        assert "api" in checks
        assert "database" in checks

    def test_vm_action_on_nonexistent(self):
        r = client.post("/api/vms/nonexistent-vm-xyz/action", json={"action": "start"})
        assert r.status_code == 404

    def test_create_vm_empty_name(self):
        r = client.post("/api/vms", json={"name": "", "cpu": 1, "ram_gb": 1, "disk_gb": 10})
        assert r.status_code == 422

    def test_create_vm_negative_cpu(self):
        r = client.post("/api/vms", json={"name": "test-neg", "cpu": -1, "ram_gb": 1, "disk_gb": 10})
        assert r.status_code == 422

    def test_resize_zero_cpu(self):
        r = client.put("/api/vms/prod-web-01/resize", json={"cpu": 0})
        assert r.status_code == 422

    def test_search_empty(self):
        r = client.get("/api/search?q=")
        assert r.status_code == 200
        assert r.json()["count"] == 0

    def test_metrics_history_default(self):
        r = client.get("/api/metrics/history")
        assert r.status_code == 200

    def test_storage_has_fields(self):
        r = client.get("/api/storage")
        pools = r.json()
        for p in pools:
            assert "name" in p
            assert "capacity_gb" in p

    def test_networks_have_fields(self):
        r = client.get("/api/networks")
        nets = r.json()
        for n in nets:
            assert "name" in n
            assert "active" in n

    def test_mode_endpoint(self):
        r = client.get("/api/mode")
        data = r.json()
        assert "demo_mode" in data
        assert "version" in data

    def test_settings_roundtrip(self):
        client.put("/api/settings/test_roundtrip?value=hello123")
        r = client.get("/api/settings")
        assert "test_roundtrip" in r.json()

    def test_vm_notes_roundtrip(self):
        client.put("/api/vms/prod-web-01/notes?notes=TestNote&tags=test,api")
        r = client.get("/api/vms/prod-web-01/notes")
        data = r.json()
        assert data["notes"] == "TestNote"
        assert "test" in data["tags"]


class TestAPICompleteness:
    """Verify all major endpoint categories are accessible."""

    def test_dashboard_overview(self):
        r = client.get("/api/dashboard/overview")
        assert "vms" in r.json()
        assert "host" in r.json()

    def test_cluster_topology(self):
        r = client.get("/api/topology")
        assert "datacenter" in r.json()

    def test_cluster_compare(self):
        r = client.get("/api/cluster/compare")
        assert "hosts" in r.json()

    def test_vm_summary_resources(self):
        r = client.get("/api/vms/summary/resources")
        assert "total_vms" in r.json()

    def test_host_local(self):
        r = client.get("/api/hosts/local")
        data = r.json()
        assert "hostname" in data
        assert "cpu_count" in data
        assert "ram_total_gb" in data

    def test_ai_history_empty_after_reset(self):
        client.post("/api/ai/reset")
        r = client.get("/api/ai/history")
        assert r.json()["length"] == 0

    def test_power_schedules_lifecycle(self):
        # Create
        client.post("/api/power-schedules", json={"vm_name": "lifecycle-test", "action": "stop", "schedule": "23:00"})
        # List
        r = client.get("/api/power-schedules")
        assert any(s["vm_name"] == "lifecycle-test" for s in r.json())
        # Delete
        client.delete("/api/power-schedules/lifecycle-test")

    def test_snapshot_policy_lifecycle(self):
        client.post("/api/snapshot-policies", json={"vm_name": "snap-test", "interval_hours": 12, "max_snapshots": 3})
        r = client.get("/api/snapshot-policies")
        assert any(p["vm_name"] == "snap-test" for p in r.json())
        client.delete("/api/snapshot-policies/snap-test")

    def test_webhook_lifecycle(self):
        client.post("/api/webhooks?url=http://test-lifecycle.example.com&events=alert")
        r = client.get("/api/webhooks")
        assert any("test-lifecycle" in w["url"] for w in r.json())
        client.delete("/api/webhooks?url=http://test-lifecycle.example.com")
