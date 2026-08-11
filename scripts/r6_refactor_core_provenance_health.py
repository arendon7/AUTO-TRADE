from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one anchor, found {count}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_between(path: str, start: str, end: str, replacement: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    start_index = text.find(start)
    end_index = text.find(end, start_index + len(start))
    if start_index < 0 or end_index < 0:
        raise RuntimeError(f"{path}: block markers not found")
    if text.find(start, start_index + 1) >= 0:
        raise RuntimeError(f"{path}: start marker is not unique")
    target.write_text(text[:start_index] + replacement + text[end_index:], encoding="utf-8")


replace_once(
    "src/autotrade/health_bridge.py",
    "from .research.health import HealthControlState, HealthEntityKind, HealthState\n",
    "from .research.health import (\n"
    "    HealthControlState,\n"
    "    HealthEntityKind,\n"
    "    HealthState,\n"
    "    HealthStateConflict,\n"
    "    _state_from_row as _authoritative_health_state_from_row,\n"
    ")\n",
)

replace_once(
    "src/autotrade/health_bridge.py",
    "class HealthBridgeRecoveryRejected(HealthBridgeError):\n    pass\n\n\nclass HealthRiskMode(StrEnum):\n",
    "class HealthBridgeRecoveryRejected(HealthBridgeError):\n"
    "    pass\n\n\n"
    "def verified_authoritative_health_state_from_row(row: sqlite3.Row) -> HealthControlState:\n"
    "    \"\"\"Verify one persisted authoritative Health row without changing state.\"\"\"\n"
    "    try:\n"
    "        return _authoritative_health_state_from_row(row)\n"
    "    except HealthStateConflict as exc:\n"
    "        raise HealthBridgeConflict(\"authoritative Health state integrity failed\") from exc\n\n\n"
    "class HealthRiskMode(StrEnum):\n",
)

replace_once(
    "src/autotrade/brokers/alpaca_paper_core_provenance.py",
    "from autotrade.health_bridge import (\n"
    "    HealthBridgeConflict,\n"
    "    HealthRiskMode,\n"
    "    _state_from_row as _health_bridge_state_from_row,\n"
    ")\n",
    "from autotrade.health_bridge import (\n"
    "    HealthBridgeConflict,\n"
    "    HealthRiskMode,\n"
    "    _state_from_row as _health_bridge_state_from_row,\n"
    "    verified_authoritative_health_state_from_row,\n"
    ")\n",
)

replace_once(
    "src/autotrade/brokers/alpaca_paper_core_provenance.py",
    "_HASH_RE = re.compile(r\"^[0-9a-f]{64}$\")\n"
    "_STRATEGY_KIND = \"STRATEGY\"\n"
    "_HEALTHY = \"HEALTHY\"\n\n\n"
    "class PaperCoreProvenanceError(RuntimeError):\n",
    "_HASH_RE = re.compile(r\"^[0-9a-f]{64}$\")\n"
    "_STRATEGY_KIND = \"STRATEGY\"\n"
    "_HEALTHY = \"HEALTHY\"\n\n\n"
    "class PaperCoreProvenanceError(RuntimeError):\n",
)

replace_between(
    "src/autotrade/brokers/alpaca_paper_core_provenance.py",
    "@dataclass(frozen=True, slots=True)\nclass _ObservedStrategyHealth:\n",
    "@dataclass(frozen=True, slots=True)\nclass PaperCoreProvenance:\n",
    "",
)

replace_between(
    "src/autotrade/brokers/alpaca_paper_core_provenance.py",
    "    @staticmethod\n    def _read_strategy_health(\n",
    "    @staticmethod\n    def _read_strategy_bridge(\n",
    "    @staticmethod\n"
    "    def _read_strategy_health(conn: sqlite3.Connection, *, strategy_id: str):\n"
    "        row = conn.execute(\n"
    "            \"SELECT * FROM health_state_v2 WHERE entity_kind=? AND entity_id=?\",\n"
    "            (_STRATEGY_KIND, strategy_id),\n"
    "        ).fetchone()\n"
    "        if row is None:\n"
    "            raise PaperCoreProvenanceMissing(\"strategy Health state is missing\")\n"
    "        try:\n"
    "            health = verified_authoritative_health_state_from_row(row)\n"
    "        except HealthBridgeConflict as exc:\n"
    "            raise PaperCoreProvenanceConflict(\"strategy Health state integrity failed\") from exc\n"
    "        if health.entity_kind.value != _STRATEGY_KIND or health.entity_id != strategy_id:\n"
    "            raise PaperCoreProvenanceConflict(\"strategy Health row identity mismatch\")\n"
    "        if health.state.value != _HEALTHY:\n"
    "            raise PaperCoreProvenanceConflict(\"strategy Health state is not HEALTHY\")\n"
    "        return health\n\n",
)

replace_once(
    "src/autotrade/brokers/alpaca_paper_core_provenance.py",
    "        health: _ObservedStrategyHealth,\n",
    "        health,\n",
)

replace_once(
    "scripts/check_r6_core_provenance_boundary.py",
    "    \"state != _HEALTHY\",\n",
    "    \"health.state.value != _HEALTHY\",\n"
    "    \"verified_authoritative_health_state_from_row(row)\",\n",
)

print("R6 core provenance Health refactor applied")
