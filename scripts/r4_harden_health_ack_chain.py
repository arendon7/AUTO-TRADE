from __future__ import annotations

from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    if old not in text:
        raise SystemExit(f"{label} not found")
    path.write_text(text.replace(old, new, 1))


path = Path("src/autotrade/research/health.py")

replace_once(
    path,
    '''    last_assessment_fingerprint: str\n    updated_at: datetime\n\n    def __post_init__(self) -> None:\n''',
    '''    last_assessment_fingerprint: str\n    updated_at: datetime\n    recovery_ack_head: str = "GENESIS"\n\n    def __post_init__(self) -> None:\n''',
    "HealthControlState field anchor",
)
replace_once(
    path,
    '''        if self.last_assessment_fingerprint:\n            _hash_value(self.last_assessment_fingerprint, "last_assessment_fingerprint")\n        if not _aware(self.updated_at):\n''',
    '''        if self.last_assessment_fingerprint:\n            _hash_value(self.last_assessment_fingerprint, "last_assessment_fingerprint")\n        if self.recovery_ack_head != "GENESIS":\n            _hash_value(self.recovery_ack_head, "recovery_ack_head")\n        if not _aware(self.updated_at):\n''',
    "HealthControlState ack validation",
)
replace_once(
    path,
    '''                "last_assessment_fingerprint": self.last_assessment_fingerprint,\n                "updated_at": self.updated_at.isoformat(),\n''',
    '''                "last_assessment_fingerprint": self.last_assessment_fingerprint,\n                "updated_at": self.updated_at.isoformat(),\n                "recovery_ack_head": self.recovery_ack_head,\n''',
    "HealthControlState fingerprint ack head",
)

replace_once(
    path,
    '''                    last_assessment_fingerprint TEXT NOT NULL,\n                    updated_at TEXT NOT NULL,\n                    state_hash TEXT NOT NULL,\n''',
    '''                    last_assessment_fingerprint TEXT NOT NULL,\n                    updated_at TEXT NOT NULL,\n                    recovery_ack_head TEXT NOT NULL DEFAULT 'GENESIS',\n                    state_hash TEXT NOT NULL,\n''',
    "health state schema ack head",
)
replace_once(
    path,
    '''                CREATE TABLE IF NOT EXISTS health_transitions_v2 (\n''',
    '''                CREATE TABLE IF NOT EXISTS health_recovery_acks_v3 (\n                    entity_kind TEXT NOT NULL,\n                    entity_id TEXT NOT NULL,\n                    ack_seq INTEGER NOT NULL CHECK(ack_seq > 0),\n                    recovery_id TEXT NOT NULL,\n                    request_fingerprint TEXT NOT NULL,\n                    confirmed_by TEXT NOT NULL,\n                    applied_at TEXT NOT NULL,\n                    previous_ack_hash TEXT NOT NULL,\n                    ack_hash TEXT NOT NULL,\n                    PRIMARY KEY(entity_kind, entity_id, recovery_id),\n                    UNIQUE(entity_kind, entity_id, ack_seq)\n                );\n                CREATE TABLE IF NOT EXISTS health_transitions_v2 (\n''',
    "v3 recovery ack schema",
)
replace_once(
    path,
    '''            legacy = conn.execute(\n                "SELECT name FROM sqlite_master WHERE type='table' AND name='health_state'"\n            ).fetchone()\n''',
    '''            state_columns = {\n                row["name"] for row in conn.execute("PRAGMA table_info(health_state_v2)").fetchall()\n            }\n            if "recovery_ack_head" not in state_columns:\n                state_count = int(conn.execute("SELECT COUNT(*) FROM health_state_v2").fetchone()[0])\n                if state_count:\n                    raise HealthStateConflict(\n                        "pre-ACK-chain health state requires explicit migration/rebaseline"\n                    )\n                conn.execute(\n                    "ALTER TABLE health_state_v2 ADD COLUMN recovery_ack_head TEXT NOT NULL DEFAULT 'GENESIS'"\n                )\n\n            legacy_ack_count = int(\n                conn.execute("SELECT COUNT(*) FROM health_recovery_acks_v2").fetchone()[0]\n            )\n            v3_ack_count = int(\n                conn.execute("SELECT COUNT(*) FROM health_recovery_acks_v3").fetchone()[0]\n            )\n            if legacy_ack_count and not v3_ack_count:\n                raise HealthStateConflict(\n                    "pre-chain recovery acknowledgements require explicit migration/rebaseline"\n                )\n\n            legacy = conn.execute(\n                "SELECT name FROM sqlite_master WHERE type='table' AND name='health_state'"\n            ).fetchone()\n''',
    "ACK-chain migration guards",
)

# get() must verify the ACK chain while the same connection is open.
replace_once(
    path,
    '''        conn = self._connect()\n        try:\n            row = conn.execute(\n                "SELECT * FROM health_state_v2 WHERE entity_kind=? AND entity_id=?",\n                (entity_kind.value, entity_id),\n            ).fetchone()\n        finally:\n            conn.close()\n        return None if row is None else _state_from_row(row)\n''',
    '''        conn = self._connect()\n        try:\n            row = conn.execute(\n                "SELECT * FROM health_state_v2 WHERE entity_kind=? AND entity_id=?",\n                (entity_kind.value, entity_id),\n            ).fetchone()\n            if row is None:\n                return None\n            state = _state_from_row(row)\n            self._verify_recovery_ack_chain(conn, state)\n            return state\n        finally:\n            conn.close()\n''',
    "health get chain verification",
)

# Assessments preserve the anchored ACK head and verify it before state mutation.
replace_once(
    path,
    '''                current = _state_from_row(row)\n                self._assert_binding(\n                    current,\n''',
    '''                current = _state_from_row(row)\n                self._verify_recovery_ack_chain(conn, current)\n                self._assert_binding(\n                    current,\n''',
    "assessment chain verification",
)
replace_once(
    path,
    '''                last_assessment_fingerprint=assessment.fingerprint,\n                updated_at=now,\n            )\n''',
    '''                last_assessment_fingerprint=assessment.fingerprint,\n                updated_at=now,\n                recovery_ack_head=(current.recovery_ack_head if current is not None else "GENESIS"),\n            )\n''',
    "assessment ack head preservation",
)

# In recovery, verify chain before looking up the idempotency key, and use v3.
# The first occurrence of this block is in acknowledge_recovery because the assessment occurrence was already replaced.
text = path.read_text()
ack_method_start = text.index("    def acknowledge_recovery(\n")
old = '''            current = _state_from_row(row)\n            self._assert_binding(\n                current,\n                baseline_fingerprint=baseline.fingerprint,\n'''
pos = text.index(old, ack_method_start)
new = '''            current = _state_from_row(row)\n            self._verify_recovery_ack_chain(conn, current)\n            self._assert_binding(\n                current,\n                baseline_fingerprint=baseline.fingerprint,\n'''
text = text[:pos] + text[pos:].replace(old, new, 1)
text = text.replace(
    '''                SELECT request_fingerprint FROM health_recovery_acks_v2\n                WHERE entity_kind=? AND entity_id=? AND recovery_id=?\n''',
    '''                SELECT request_fingerprint FROM health_recovery_acks_v3\n                WHERE entity_kind=? AND entity_id=? AND recovery_id=?\n''',
    1,
)

# Replace the HEALTHY no-op insert/return with a versioned state update anchored to the ACK chain.
old = '''            if current.state is HealthState.HEALTHY:\n                conn.execute(\n                    """\n                    INSERT INTO health_recovery_acks_v2(\n                        entity_kind,entity_id,recovery_id,request_fingerprint,confirmed_by,applied_at\n                    ) VALUES(?,?,?,?,?,?)\n                    """,\n                    (\n                        current.entity_kind.value,\n                        current.entity_id,\n                        recovery_id,\n                        request_fingerprint,\n                        confirmed_by,\n                        now.isoformat(),\n                    ),\n                )\n                conn.commit()\n                return current\n'''
new = '''            if current.state is HealthState.HEALTHY:\n                ack_seq, ack_hash = self._append_recovery_ack(\n                    conn,\n                    current=current,\n                    recovery_id=recovery_id,\n                    request_fingerprint=request_fingerprint,\n                    confirmed_by=confirmed_by,\n                    now=now,\n                )\n                updated = HealthControlState(\n                    entity_id=current.entity_id,\n                    entity_kind=current.entity_kind,\n                    state=current.state,\n                    version=current.version + 1,\n                    distinct_quarantine_count=current.distinct_quarantine_count,\n                    baseline_fingerprint=current.baseline_fingerprint,\n                    policy_fingerprint=current.policy_fingerprint,\n                    last_assessment_fingerprint=current.last_assessment_fingerprint,\n                    updated_at=now,\n                    recovery_ack_head=ack_hash,\n                )\n                self._upsert_state(conn, updated)\n                self._verify_recovery_ack_chain(conn, updated)\n                conn.commit()\n                return updated\n'''
if old not in text:
    raise SystemExit("healthy recovery ACK block not found")
text = text.replace(old, new, 1)

# Recovery state change must append ACK first, anchor its hash in the new state, and remove old v2 insert.
old = '''            updated = HealthControlState(\n                entity_id=current.entity_id,\n                entity_kind=current.entity_kind,\n                state=target,\n                version=current.version + 1,\n                distinct_quarantine_count=current.distinct_quarantine_count,\n                baseline_fingerprint=current.baseline_fingerprint,\n                policy_fingerprint=current.policy_fingerprint,\n                last_assessment_fingerprint=assessment.fingerprint,\n                updated_at=now,\n            )\n            self._upsert_state(conn, updated)\n            conn.execute(\n                """\n                INSERT INTO health_recovery_acks_v2(\n                    entity_kind,entity_id,recovery_id,request_fingerprint,confirmed_by,applied_at\n                ) VALUES(?,?,?,?,?,?)\n                """,\n                (\n                    current.entity_kind.value,\n                    current.entity_id,\n                    recovery_id,\n                    request_fingerprint,\n                    confirmed_by,\n                    now.isoformat(),\n                ),\n            )\n'''
new = '''            ack_seq, ack_hash = self._append_recovery_ack(\n                conn,\n                current=current,\n                recovery_id=recovery_id,\n                request_fingerprint=request_fingerprint,\n                confirmed_by=confirmed_by,\n                now=now,\n            )\n            updated = HealthControlState(\n                entity_id=current.entity_id,\n                entity_kind=current.entity_kind,\n                state=target,\n                version=current.version + 1,\n                distinct_quarantine_count=current.distinct_quarantine_count,\n                baseline_fingerprint=current.baseline_fingerprint,\n                policy_fingerprint=current.policy_fingerprint,\n                last_assessment_fingerprint=assessment.fingerprint,\n                updated_at=now,\n                recovery_ack_head=ack_hash,\n            )\n            self._upsert_state(conn, updated)\n            self._verify_recovery_ack_chain(conn, updated)\n'''
if old not in text:
    raise SystemExit("recovery state change ACK block not found")
text = text.replace(old, new, 1)
path.write_text(text)

# Upsert/read include the chain head.
replace_once(
    path,
    '''                baseline_fingerprint, policy_fingerprint, last_assessment_fingerprint,\n                updated_at, state_hash\n            ) VALUES(?,?,?,?,?,?,?,?,?,?)\n''',
    '''                baseline_fingerprint, policy_fingerprint, last_assessment_fingerprint,\n                updated_at, recovery_ack_head, state_hash\n            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)\n''',
    "upsert columns ACK head",
)
replace_once(
    path,
    '''                last_assessment_fingerprint=excluded.last_assessment_fingerprint,\n                updated_at=excluded.updated_at,\n                state_hash=excluded.state_hash\n''',
    '''                last_assessment_fingerprint=excluded.last_assessment_fingerprint,\n                updated_at=excluded.updated_at,\n                recovery_ack_head=excluded.recovery_ack_head,\n                state_hash=excluded.state_hash\n''',
    "upsert update ACK head",
)
replace_once(
    path,
    '''                state.last_assessment_fingerprint,\n                state.updated_at.isoformat(),\n                state.fingerprint,\n''',
    '''                state.last_assessment_fingerprint,\n                state.updated_at.isoformat(),\n                state.recovery_ack_head,\n                state.fingerprint,\n''',
    "upsert values ACK head",
)
replace_once(
    path,
    '''            last_assessment_fingerprint=row["last_assessment_fingerprint"],\n            updated_at=datetime.fromisoformat(row["updated_at"]),\n''',
    '''            last_assessment_fingerprint=row["last_assessment_fingerprint"],\n            updated_at=datetime.fromisoformat(row["updated_at"]),\n            recovery_ack_head=row["recovery_ack_head"],\n''',
    "row parser ACK head",
)

# Insert helper methods before _assert_binding.
text = path.read_text()
marker = "    def _assert_binding(\n"
helpers = '''    def _append_recovery_ack(\n        self,\n        conn: sqlite3.Connection,\n        *,\n        current: HealthControlState,\n        recovery_id: str,\n        request_fingerprint: str,\n        confirmed_by: str,\n        now: datetime,\n    ) -> tuple[int, str]:\n        row = conn.execute(\n            """\n            SELECT COALESCE(MAX(ack_seq), 0) AS max_seq\n            FROM health_recovery_acks_v3\n            WHERE entity_kind=? AND entity_id=?\n            """,\n            (current.entity_kind.value, current.entity_id),\n        ).fetchone()\n        ack_seq = int(row["max_seq"]) + 1\n        previous_ack_hash = current.recovery_ack_head\n        ack_hash = _hash(\n            {\n                "entity_kind": current.entity_kind.value,\n                "entity_id": current.entity_id,\n                "ack_seq": ack_seq,\n                "recovery_id": recovery_id,\n                "request_fingerprint": request_fingerprint,\n                "confirmed_by": confirmed_by,\n                "applied_at": now.isoformat(),\n                "previous_ack_hash": previous_ack_hash,\n            }\n        )\n        conn.execute(\n            """\n            INSERT INTO health_recovery_acks_v3(\n                entity_kind,entity_id,ack_seq,recovery_id,request_fingerprint,\n                confirmed_by,applied_at,previous_ack_hash,ack_hash\n            ) VALUES(?,?,?,?,?,?,?,?,?)\n            """,\n            (\n                current.entity_kind.value,\n                current.entity_id,\n                ack_seq,\n                recovery_id,\n                request_fingerprint,\n                confirmed_by,\n                now.isoformat(),\n                previous_ack_hash,\n                ack_hash,\n            ),\n        )\n        return ack_seq, ack_hash\n\n    def _verify_recovery_ack_chain(\n        self,\n        conn: sqlite3.Connection,\n        state: HealthControlState,\n    ) -> None:\n        rows = conn.execute(\n            """\n            SELECT ack_seq,recovery_id,request_fingerprint,confirmed_by,\n                   applied_at,previous_ack_hash,ack_hash\n            FROM health_recovery_acks_v3\n            WHERE entity_kind=? AND entity_id=?\n            ORDER BY ack_seq ASC\n            """,\n            (state.entity_kind.value, state.entity_id),\n        ).fetchall()\n        running = "GENESIS"\n        expected_seq = 1\n        for row in rows:\n            try:\n                ack_seq = int(row["ack_seq"])\n                recovery_id = str(row["recovery_id"])\n                request_fingerprint = str(row["request_fingerprint"])\n                confirmed_by = str(row["confirmed_by"])\n                applied_at = str(row["applied_at"])\n                previous_ack_hash = str(row["previous_ack_hash"])\n                ack_hash = str(row["ack_hash"])\n            except (KeyError, TypeError, ValueError) as exc:\n                raise HealthStateConflict("recovery ACK chain row is malformed") from exc\n            if ack_seq != expected_seq:\n                raise HealthStateConflict("recovery ACK chain sequence gap/reorder detected")\n            _identity(recovery_id, "recovery_id")\n            _identity(confirmed_by, "confirmed_by")\n            _hash_value(request_fingerprint, "request_fingerprint")\n            if previous_ack_hash != running:\n                raise HealthStateConflict("recovery ACK chain previous hash mismatch")\n            expected_hash = _hash(\n                {\n                    "entity_kind": state.entity_kind.value,\n                    "entity_id": state.entity_id,\n                    "ack_seq": ack_seq,\n                    "recovery_id": recovery_id,\n                    "request_fingerprint": request_fingerprint,\n                    "confirmed_by": confirmed_by,\n                    "applied_at": applied_at,\n                    "previous_ack_hash": previous_ack_hash,\n                }\n            )\n            if ack_hash != expected_hash:\n                raise HealthStateConflict("recovery ACK chain hash mismatch")\n            running = ack_hash\n            expected_seq += 1\n        if running != state.recovery_ack_head:\n            raise HealthStateConflict("recovery ACK chain head does not match Health state")\n\n'''
if marker not in text:
    raise SystemExit("_assert_binding marker missing")
path.write_text(text.replace(marker, helpers + marker, 1))
