from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

import pytest

from labs.oss3_qlib.environment_attestation import (
    EnvironmentAttestationGovernanceError,
    EnvironmentAttestationIntegrityError,
    InstalledDistribution,
    QlibEnvironmentAttestation,
    REQUIRED_RUNTIME_DISTRIBUTIONS,
    RUNTIME_FORBIDDEN_EVEN_IF_INSTALLED,
    collect_environment_attestation,
    normalize_distribution_name,
)
from labs.oss3_qlib.model_contract import QLIB_VERSION, model_config_hash, runner_code_hash


def test_effective_environment_is_stable_within_same_process() -> None:
    first = collect_environment_attestation()
    second = collect_environment_attestation()

    assert first == second
    assert first.same_effective_environment(second)
    assert first.attestation_hash == second.attestation_hash
    assert first.installed_distribution_count > 0


def test_effective_environment_binds_certified_d2b_runtime() -> None:
    attestation = collect_environment_attestation()
    lab_root = Path(__file__).resolve().parents[1]

    assert attestation.qlib_version == QLIB_VERSION == "0.9.7"
    assert attestation.package_version("pyqlib") == "0.9.7"
    assert attestation.runner_code_hash == runner_code_hash(lab_root=lab_root)
    assert attestation.model_config_hash == model_config_hash()
    assert attestation.requirements_sha256 == sha256(
        (lab_root / "requirements.txt").read_bytes()
    ).hexdigest()
    for name in REQUIRED_RUNTIME_DISTRIBUTIONS:
        assert attestation.package_version(name)


def test_transitive_runtime_forbidden_packages_are_evidence_not_authority() -> None:
    attestation = collect_environment_attestation()
    installed = {item.name for item in attestation.installed_distributions}
    expected = tuple(
        sorted(name for name in RUNTIME_FORBIDDEN_EVEN_IF_INSTALLED if name in installed)
    )

    assert attestation.runtime_forbidden_installed == expected
    assert attestation.execution_authorized is False
    assert attestation.paper_execution_authorized is False
    assert attestation.capital_authority == "NONE"
    assert attestation.live_trading == "BLOCKED"


def test_canonical_round_trip(tmp_path: Path) -> None:
    attestation = collect_environment_attestation()
    target = tmp_path / "environment.json"

    attestation.write(target)
    loaded = QlibEnvironmentAttestation.read(target)

    assert loaded == attestation
    assert target.read_text(encoding="utf-8").endswith("\n")


def test_tampered_attestation_hash_fails_closed(tmp_path: Path) -> None:
    attestation = collect_environment_attestation()
    target = tmp_path / "environment.json"
    attestation.write(target)
    document = json.loads(target.read_text(encoding="utf-8"))
    document["attestation_hash"] = "0" * 64
    target.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(EnvironmentAttestationIntegrityError, match="attestation hash mismatch"):
        QlibEnvironmentAttestation.read(target)


def test_duplicate_json_key_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "duplicate.json"
    target.write_text(
        '{"attestation_version":"A","attestation_version":"B","environment":{},"attestation_hash":"'
        + "0" * 64
        + '"}\n',
        encoding="utf-8",
    )

    with pytest.raises(EnvironmentAttestationIntegrityError, match="duplicate JSON object key"):
        QlibEnvironmentAttestation.read(target)


def test_duplicate_normalized_distribution_identity_fails_closed() -> None:
    current = collect_environment_attestation()
    manifest = list(current.installed_distributions)
    manifest.append(InstalledDistribution(name="pyqlib", version="0.9.7"))

    with pytest.raises(EnvironmentAttestationIntegrityError, match="duplicate canonical"):
        collect_environment_attestation(distributions=manifest)


def test_wrong_pyqlib_version_fails_closed() -> None:
    current = collect_environment_attestation()
    manifest = tuple(
        InstalledDistribution(item.name, "9.9.9") if item.name == "pyqlib" else item
        for item in current.installed_distributions
    )

    with pytest.raises(EnvironmentAttestationGovernanceError, match="pyqlib version"):
        collect_environment_attestation(distributions=manifest)


def test_authority_cannot_be_enabled_by_mutation() -> None:
    attestation = collect_environment_attestation()

    with pytest.raises(EnvironmentAttestationGovernanceError, match="cannot authorize execution"):
        replace(attestation, execution_authorized=True)
    with pytest.raises(EnvironmentAttestationGovernanceError, match="cannot authorize execution"):
        replace(attestation, paper_execution_authorized=True)
    with pytest.raises(EnvironmentAttestationGovernanceError, match="cannot grant capital or LIVE"):
        replace(attestation, capital_authority="SOME")
    with pytest.raises(EnvironmentAttestationGovernanceError, match="cannot grant capital or LIVE"):
        replace(attestation, live_trading="ENABLED")


def test_serialized_evidence_excludes_sensitive_machine_context() -> None:
    raw = json.dumps(collect_environment_attestation().to_dict(), sort_keys=True).lower()
    forbidden_keys = (
        '"hostname"',
        '"username"',
        '"home"',
        '"cwd"',
        '"environment_variables"',
        '"credentials"',
        '"token"',
        '"secret"',
        '"password"',
        '"api_key"',
    )
    assert all(key not in raw for key in forbidden_keys)


def test_distribution_name_normalization_is_pep503_like() -> None:
    assert normalize_distribution_name("Scikit_Learn") == "scikit-learn"
    assert normalize_distribution_name("A..B__C---D") == "a-b-c-d"
