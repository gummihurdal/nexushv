"""
NexusHV AI Module — Unit Tests
Run: python -m pytest tests/test_ai.py -v
"""
import sys
import os
import json
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ai"))

from nexushv_ai_local import (
    NexusAI, ClusterContext, is_safe_command,
    SAFE_COMMANDS, SYSTEM_PROMPT
)


class TestSafeCommands:
    def test_safe_commands_exist(self):
        assert len(SAFE_COMMANDS) > 10

    def test_virsh_list_is_safe(self):
        assert is_safe_command("virsh list")

    def test_virsh_dominfo_is_safe(self):
        assert is_safe_command("virsh dominfo myvm")

    def test_cat_proc_cpuinfo_is_safe(self):
        assert is_safe_command("cat /proc/cpuinfo")

    def test_free_is_safe(self):
        assert is_safe_command("free -h")

    def test_rm_is_not_safe(self):
        assert is_safe_command("rm -rf /") is False

    def test_reboot_is_not_safe(self):
        assert is_safe_command("reboot") is False

    def test_shutdown_is_not_safe(self):
        assert is_safe_command("shutdown -h now") is False

    def test_virsh_destroy_is_not_safe(self):
        assert is_safe_command("virsh destroy myvm") is False

    def test_dd_is_not_safe(self):
        assert is_safe_command("dd if=/dev/zero of=/dev/sda") is False

    def test_empty_command(self):
        assert is_safe_command("") is False


class TestClusterContext:
    def test_create_context(self):
        ctx = ClusterContext(
            hosts=[{"name": "host1", "ip": "10.0.1.10", "status": "connected", "cpu_pct": 50, "ram_pct": 60, "vm_count": 3}],
            vms=[{"name": "vm1", "host": "host1", "state": "running", "cpu_pct": 30, "ram_pct": 40, "disk_pct": 50}],
            storage=[{"name": "pool1", "type": "NVMe", "used_pct": 45, "free_gb": 500}],
            networks=[{"name": "net1", "type": "bridge", "vm_count": 3, "uplink_count": 2}],
            events=[],
            timestamp=time.time(),
        )
        assert len(ctx.hosts) == 1
        assert len(ctx.vms) == 1

    def test_context_to_prompt_string(self):
        ctx = ClusterContext(
            hosts=[{"name": "host1", "ip": "10.0.1.10", "status": "connected", "cpu_pct": 50, "ram_pct": 60, "vm_count": 3}],
            vms=[{"name": "vm1", "host": "host1", "state": "running", "cpu_pct": 30, "ram_pct": 40, "disk_pct": 50}],
            storage=[{"name": "pool1", "type": "NVMe", "used_pct": 45, "free_gb": 500}],
            networks=[{"name": "net1", "type": "bridge", "vm_count": 3, "uplink_count": 2}],
            events=[],
            timestamp=time.time(),
        )
        prompt = ctx.to_prompt_string()
        assert "LIVE CLUSTER STATE" in prompt
        assert "host1" in prompt
        assert "vm1" in prompt
        assert "pool1" in prompt
        assert "net1" in prompt

    def test_context_warns_high_cpu(self):
        ctx = ClusterContext(
            hosts=[{"name": "host1", "ip": "10.0.1.10", "status": "connected", "cpu_pct": 95, "ram_pct": 60, "vm_count": 3}],
            vms=[], storage=[], networks=[], events=[], timestamp=time.time(),
        )
        prompt = ctx.to_prompt_string()
        assert "[HIGH]" in prompt

    def test_context_warns_high_disk(self):
        ctx = ClusterContext(
            hosts=[],
            vms=[{"name": "vm1", "host": "h1", "state": "running", "cpu_pct": 10, "ram_pct": 10, "disk_pct": 90}],
            storage=[], networks=[], events=[], timestamp=time.time(),
        )
        prompt = ctx.to_prompt_string()
        assert "DISK 90% FULL" in prompt

    def test_context_warns_single_uplink(self):
        ctx = ClusterContext(
            hosts=[], vms=[], storage=[],
            networks=[{"name": "net1", "type": "bridge", "vm_count": 3, "uplink_count": 1}],
            events=[], timestamp=time.time(),
        )
        prompt = ctx.to_prompt_string()
        assert "SINGLE UPLINK" in prompt

    def test_empty_context(self):
        ctx = ClusterContext(hosts=[], vms=[], storage=[], networks=[], events=[])
        prompt = ctx.to_prompt_string()
        assert "LIVE CLUSTER STATE" in prompt


class TestNexusAI:
    def test_init(self):
        ai = NexusAI()
        assert ai.model == "nexushv-ai"
        assert ai.history == []

    def test_build_messages(self):
        ai = NexusAI()
        ctx = ClusterContext(
            hosts=[{"name": "h1", "ip": "10.0.1.10", "status": "connected", "cpu_pct": 50, "ram_pct": 60, "vm_count": 1}],
            vms=[], storage=[], networks=[], events=[], timestamp=time.time(),
        )
        messages = ai._build_messages("Hello", ctx)
        assert len(messages) >= 2  # system + user
        assert messages[0]["role"] == "system"
        assert "NEXUS AI" in messages[0]["content"]
        assert "Hello" in messages[-1]["content"]
        assert "LIVE CLUSTER STATE" in messages[-1]["content"]

    def test_build_messages_no_context(self):
        ai = NexusAI()
        messages = ai._build_messages("Hello", None)
        assert len(messages) >= 2
        assert "Hello" in messages[-1]["content"]

    def test_reset_conversation(self):
        ai = NexusAI()
        ai.history = [{"role": "user", "content": "test"}]
        ai.reset_conversation()
        assert ai.history == []


class TestSystemPrompt:
    def test_prompt_exists(self):
        assert len(SYSTEM_PROMPT) > 100

    def test_prompt_covers_kvm(self):
        assert "KVM" in SYSTEM_PROMPT

    def test_prompt_covers_qemu(self):
        assert "QEMU" in SYSTEM_PROMPT

    def test_prompt_covers_storage(self):
        assert "QCOW2" in SYSTEM_PROMPT

    def test_prompt_covers_networking(self):
        assert "virtio-net" in SYSTEM_PROMPT

    def test_prompt_covers_security(self):
        assert "seccomp" in SYSTEM_PROMPT


class TestTrainingData:
    def test_dataset_exists(self):
        path = os.path.join(os.path.dirname(__file__), "..", "ai", "training", "nexushv_dataset.jsonl")
        assert os.path.exists(path)

    def test_dataset_valid_json(self):
        path = os.path.join(os.path.dirname(__file__), "..", "ai", "training", "nexushv_dataset.jsonl")
        valid = 0
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                assert "instruction" in d
                assert "output" in d
                assert len(d["output"]) > 50
                valid += 1
        assert valid >= 100, f"Expected 100+ entries, got {valid}"

    def test_dataset_covers_key_topics(self):
        path = os.path.join(os.path.dirname(__file__), "..", "ai", "training", "nexushv_dataset.jsonl")
        topics_found = set()
        keywords = ["qcow2", "ept", "sr-iov", "vhost", "numa", "kvm", "qemu", "migrate", "snapshot", "virtio"]
        with open(path) as f:
            for line in f:
                d = json.loads(line.strip())
                instr = d["instruction"].lower()
                for kw in keywords:
                    if kw in instr:
                        topics_found.add(kw)
        assert len(topics_found) >= 5, f"Only found topics: {topics_found}"
