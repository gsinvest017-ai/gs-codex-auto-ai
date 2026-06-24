"""
test_audit.py — Tests for the tamper-evident AuditLog (SECGOV-R8 / C8).

All tests use pytest's tmp_path fixture so there are no hardcoded paths
and they run cleanly on Windows and Linux alike.
"""

import json
import pytest

from src.codexautoai_v2.audit import AuditLog, GENESIS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log_path(tmp_path, name="audit.jsonl"):
    return str(tmp_path / name)


# ---------------------------------------------------------------------------
# Basic construction
# ---------------------------------------------------------------------------

class TestEmptyLog:
    def test_entries_empty(self, tmp_path):
        log = AuditLog(_log_path(tmp_path))
        assert log.entries() == []

    def test_head_returns_genesis_when_empty(self, tmp_path):
        log = AuditLog(_log_path(tmp_path))
        assert log.head() == GENESIS

    def test_verify_true_when_empty(self, tmp_path):
        log = AuditLog(_log_path(tmp_path))
        assert log.verify() is True


# ---------------------------------------------------------------------------
# Appending + valid chain
# ---------------------------------------------------------------------------

class TestAppend:
    def test_append_returns_hash_string(self, tmp_path):
        log = AuditLog(_log_path(tmp_path))
        h = log.append({"event": "start"})
        assert isinstance(h, str) and len(h) == 64

    def test_single_entry_prev_hash_is_genesis(self, tmp_path):
        log = AuditLog(_log_path(tmp_path))
        log.append({"event": "boot"})
        entry = log.entries()[0]
        assert entry["prev_hash"] == GENESIS

    def test_chain_links_correctly(self, tmp_path):
        log = AuditLog(_log_path(tmp_path))
        h0 = log.append({"seq": 0})
        h1 = log.append({"seq": 1})
        h2 = log.append({"seq": 2})

        entries = log.entries()
        assert entries[0]["hash"] == h0
        assert entries[1]["prev_hash"] == h0
        assert entries[1]["hash"] == h1
        assert entries[2]["prev_hash"] == h1
        assert entries[2]["hash"] == h2

    def test_head_tracks_last_hash(self, tmp_path):
        log = AuditLog(_log_path(tmp_path))
        log.append({"a": 1})
        h = log.append({"b": 2})
        assert log.head() == h

    def test_verify_true_for_valid_chain(self, tmp_path):
        log = AuditLog(_log_path(tmp_path))
        for i in range(5):
            log.append({"i": i})
        assert log.verify() is True

    def test_seq_numbers_are_sequential(self, tmp_path):
        log = AuditLog(_log_path(tmp_path))
        for i in range(4):
            log.append({"v": i})
        for idx, entry in enumerate(log.entries()):
            assert entry["seq"] == idx

    def test_persistence_across_instances(self, tmp_path):
        p = _log_path(tmp_path)
        log1 = AuditLog(p)
        log1.append({"x": 1})

        log2 = AuditLog(p)
        log2.append({"x": 2})

        log3 = AuditLog(p)
        assert len(log3.entries()) == 2
        assert log3.verify() is True


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------

class TestTamperDetection:
    def _write_back(self, path, entries):
        """Overwrite the file with the given list of entry dicts."""
        with open(path, "w", encoding="utf-8") as fh:
            for e in entries:
                fh.write(
                    json.dumps(e, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                    + "\n"
                )

    def test_modify_payload_detected(self, tmp_path):
        p = _log_path(tmp_path)
        log = AuditLog(p)
        log.append({"action": "login", "user": "alice"})
        log.append({"action": "transfer", "amount": 100})

        entries = log.entries()
        # Tamper: change the amount in entry 1
        entries[1]["payload"]["amount"] = 9999
        self._write_back(p, entries)

        log2 = AuditLog(p)
        assert log2.verify() is False

    def test_modify_first_entry_detected(self, tmp_path):
        p = _log_path(tmp_path)
        log = AuditLog(p)
        log.append({"role": "admin"})
        log.append({"role": "user"})

        entries = log.entries()
        entries[0]["payload"]["role"] = "superadmin"
        self._write_back(p, entries)

        assert AuditLog(p).verify() is False

    def test_delete_middle_entry_detected(self, tmp_path):
        p = _log_path(tmp_path)
        log = AuditLog(p)
        for i in range(4):
            log.append({"i": i})

        entries = log.entries()
        # Remove entry at index 2
        del entries[2]
        self._write_back(p, entries)

        assert AuditLog(p).verify() is False

    def test_delete_first_entry_detected(self, tmp_path):
        p = _log_path(tmp_path)
        log = AuditLog(p)
        log.append({"first": True})
        log.append({"second": True})

        entries = log.entries()
        del entries[0]
        self._write_back(p, entries)

        assert AuditLog(p).verify() is False

    def test_reorder_entries_detected(self, tmp_path):
        p = _log_path(tmp_path)
        log = AuditLog(p)
        log.append({"order": 0})
        log.append({"order": 1})
        log.append({"order": 2})

        entries = log.entries()
        # Swap entries 0 and 1
        entries[0], entries[1] = entries[1], entries[0]
        self._write_back(p, entries)

        assert AuditLog(p).verify() is False

    def test_insert_forged_entry_detected(self, tmp_path):
        p = _log_path(tmp_path)
        log = AuditLog(p)
        log.append({"a": 1})
        log.append({"a": 2})

        entries = log.entries()
        # Forge and insert a plausible-looking entry in the middle
        forged = {
            "seq": 99,
            "prev_hash": entries[0]["hash"],
            "payload": {"injected": True},
            "hash": "deadbeef" * 8,
        }
        entries.insert(1, forged)
        self._write_back(p, entries)

        assert AuditLog(p).verify() is False

    def test_modify_hash_field_directly_detected(self, tmp_path):
        """Changing just the stored hash without touching payload is also caught."""
        p = _log_path(tmp_path)
        log = AuditLog(p)
        log.append({"k": "v"})

        entries = log.entries()
        entries[0]["hash"] = "a" * 64
        self._write_back(p, entries)

        assert AuditLog(p).verify() is False


# ---------------------------------------------------------------------------
# Genesis constant
# ---------------------------------------------------------------------------

class TestGenesisConstant:
    def test_genesis_is_64_zeros(self):
        assert GENESIS == "0" * 64

    def test_first_entry_prev_hash_equals_genesis(self, tmp_path):
        log = AuditLog(_log_path(tmp_path))
        log.append({"boot": True})
        assert log.entries()[0]["prev_hash"] == GENESIS

    def test_head_equals_genesis_for_empty_log(self, tmp_path):
        log = AuditLog(_log_path(tmp_path))
        assert log.head() == GENESIS
