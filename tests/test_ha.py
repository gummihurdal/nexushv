"""
NexusHV HA Engine — Unit Tests
Run: python -m pytest tests/test_ha.py -v
"""
import sys
import os
import time
import asyncio
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ha"))

from nexushv_ha import HAEngine, HostState, VMPriority, VMPolicy, ClusterHealth


class TestHAEngine:
    def setup_method(self):
        self.ha = HAEngine(local_ip="10.0.1.10", peers=["10.0.1.11", "10.0.1.12"], standalone=True)

    def test_initialization(self):
        assert self.ha.local_ip == "10.0.1.10"
        assert self.ha.standalone is True
        assert self.ha.is_master is True
        assert len(self.ha.peers) == 2

    def test_default_vm_policies(self):
        assert len(self.ha.vm_policies) >= 5
        assert "prod-db-primary" in self.ha.vm_policies
        assert self.ha.vm_policies["prod-db-primary"].priority == VMPriority.HIGH

    def test_has_quorum_all_alive(self):
        for peer in self.ha.peers.values():
            peer.state = HostState.ALIVE
        assert self.ha.has_quorum() is True

    def test_has_quorum_one_dead(self):
        peers = list(self.ha.peers.values())
        peers[0].state = HostState.ALIVE
        peers[1].state = HostState.DEAD
        # 2 alive (self + 1 peer) out of 3 total = quorum met
        assert self.ha.has_quorum() is True

    def test_no_quorum_all_dead(self):
        for peer in self.ha.peers.values():
            peer.state = HostState.DEAD
        # Only self alive = 1/3 = no quorum
        assert self.ha.has_quorum() is False

    def test_cluster_health_green(self):
        for peer in self.ha.peers.values():
            peer.state = HostState.ALIVE
        self.ha._update_cluster_health()
        assert self.ha.cluster_health == ClusterHealth.GREEN

    def test_cluster_health_yellow(self):
        list(self.ha.peers.values())[0].state = HostState.SUSPECT
        self.ha._update_cluster_health()
        assert self.ha.cluster_health == ClusterHealth.YELLOW

    def test_cluster_health_red(self):
        list(self.ha.peers.values())[0].state = HostState.DEAD
        self.ha._update_cluster_health()
        assert self.ha.cluster_health == ClusterHealth.RED

    def test_restart_order(self):
        order = self.ha._restart_order(list(self.ha.vm_policies.keys()))
        # HIGH priority VMs should come first
        priorities = [self.ha.vm_policies[vm].priority for vm in order]
        # Filter out NONE priority
        non_none = [p for p in priorities if p != VMPriority.NONE]
        for i in range(len(non_none) - 1):
            assert non_none[i].value <= non_none[i + 1].value

    def test_select_failover_host(self):
        for peer in self.ha.peers.values():
            peer.state = HostState.ALIVE
            peer.vm_count = 2
        host = self.ha._select_failover_host("prod-db-primary")
        assert host in self.ha.peers

    def test_select_failover_host_prefers_least_loaded(self):
        peers = list(self.ha.peers.values())
        peers[0].state = HostState.ALIVE
        peers[0].vm_count = 10
        peers[1].state = HostState.ALIVE
        peers[1].vm_count = 2
        host = self.ha._select_failover_host("prod-db-primary")
        assert host == peers[1].ip

    def test_event_logging(self):
        self.ha._log_event("test_event", "10.0.1.10", "test-vm", "test detail")
        assert len(self.ha.events) == 1
        assert self.ha.events[0].event_type == "test_event"
        assert self.ha.events[0].vm == "test-vm"

    def test_event_limit(self):
        for i in range(600):
            self.ha._log_event("test", "10.0.1.10", detail=f"event {i}")
        assert len(self.ha.events) <= 500

    def test_get_status(self):
        status = self.ha.get_status()
        assert "local_ip" in status
        assert "is_master" in status
        assert "cluster_health" in status
        assert "peers" in status
        assert "vm_policies" in status
        assert "events" in status

    def test_split_brain_detection_no_split(self):
        for peer in self.ha.peers.values():
            peer.state = HostState.ALIVE
        assert self.ha.detect_split_brain() is False

    def test_vm_policy_priority_ordering(self):
        assert VMPriority.HIGH < VMPriority.MEDIUM
        assert VMPriority.MEDIUM < VMPriority.LOW

    def test_host_states(self):
        assert HostState.ALIVE.value == "alive"
        assert HostState.DEAD.value == "dead"
        assert HostState.FENCING.value == "fencing"


class TestHAAPI:
    """Test HA REST API endpoints (requires HA daemon to be running)."""

    def test_ha_status_via_main_api(self):
        """Test that the HA proxy endpoint works."""
        from fastapi.testclient import TestClient
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
        from nexushv_api import app
        client = TestClient(app)
        r = client.get("/api/ha/status")
        assert r.status_code == 200
