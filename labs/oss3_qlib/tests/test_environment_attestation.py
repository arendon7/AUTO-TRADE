from __future__ import annotations

from dataclasses import replace
import json

import pytest

from labs.oss3_qlib.environment_attestation import (
    ARTIFACT_VERSION,
    EnvironmentAttestation,
    EnvironmentAttestationGovernanceError,
    EnvironmentAttestationIntegrityError,
    InstalledDistribution,
    canonical_distribution_name,
    collect_environment_attestation,
)
from labs.oss3_qlib.model_contract import MODEL_FAMILY, QLIB_VERSION, model_config_hash, runner_code_hash


def _distributions(extra: tuple[InstalledDistribution, ...] = ()) -> tuple[InstalledDistribution, ...]:
    return tuple(
        sorted(
            (
                InstalledDistribution("numpy", "2.5.2"),
                InstalledDistribution("pandas", "3.0.5"),
                InstalledDistribution("pyqlib", QLIB_VERSION),
                InstalledDistribution("scikit-learn", "1.8.0"),
            )
            + extra
        )
    )


def _artifact(*, distributions: tuple[InstalledDistribution, ...] | None = None) -> EnvironmentAttestation:
    return EnvironmentAttestation.build(
        distributions=distributions or _distributions(),
        python_implementation="cpython",
        python_version="3.12.14",
        platform_system="linux",
        platform_machine="x86_64",
        libc_name="glibc",
        libc_version="2.39",
    )


def test_distribution_name_is_pep503_style_canonical() -> None:
    assert canonical_distribution_name("Scikit_Learn") == "scikit-learn"
    assert canonical_distribution_name("zope.interface") == "zope-interface"


def test_attestation_binds_model_runner_runtime_and_sorted_distribution_set() -> None:
    artifact = _artifact()
    assert artifact.artifact_version == ARTIFACT_VERSION
    assert artifact.manifest.qlib_version == QLIB_VERSION
    assert artifact.manifest.model_family == MODEL_FAMILY
    assert artifact.manifest.model_config_hash == model_config_hash()
    assert artifact.manifest.runner_code_hash == runner_code_hash()
    assert artifact.distributions == tuple(sorted(artifact.distributions))
    assert artifact.manifest.distribution_count == len(artifact.distributions)
    assert len(artifact.manifest.distribution_set_hash) == 64
    assert len(artifact.artifact_hash) == 64


def test_same_inputs_produce_same_artifact_without_timestamp_or_host_identity() -> None:
    left = _artifact()
    right = _artifact()
    assert left == right
    payload = left.to_dict()
    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in (
        "timestamp",
        "created_at",
        "hostname",
        "username",
        "home",
        "environment",
        "credential",
        "secret",
        "ip_address",
    ):
        assert forbidden not in serialized.lower()


def test_distribution_version_change_changes_environment_identity() -> None:
    left = _artifact()
    changed = tuple(
        InstalledDistribution(item.name, "2.5.3" if item.name == "numpy" else item.version)
        for item in _distributions()
    )
    right = _artifact(distributions=changed)
    assert left.manifest.distribution_set_hash != right.manifest.distribution_set_hash
    assert left.artifact_hash != right.artifact_hash


def test_duplicate_canonical_distribution_names_fail_closed() -> None:
    with pytest.raises(EnvironmentAttestationGovernanceError, match="duplicate canonical"):
        EnvironmentAttestation.build(
            distributions=(
                InstalledDistribution("pyqlib", QLIB_VERSION),
                InstalledDistribution("pyqlib", QLIB_VERSION),
            ),
            python_implementation="cpython",
            python_version="3.12.14",
            platform_system="linux",
            platform_machine="x86_64",
            libc_name="glibc",
            libc_version="2.39",
        )


def test_missing_or_wrong_pyqlib_fails_closed() -> None:
    with pytest.raises(EnvironmentAttestationGovernanceError, match="exact pyqlib"):
        _artifact(
            distributions=tuple(item for item in _distributions() if item.name != "pyqlib")
        )
    with pytest.raises(EnvironmentAttestationGovernanceError, match="exact pyqlib"):
        _artifact(
            distributions=tuple(
                InstalledDistribution(item.name, "0.9.6" if item.name == "pyqlib" else item.version)
                for item in _distributions()
            )
        )


def test_authority_fields_are_permanently_non_operational() -> None:
    artifact = _artifact()
    assert artifact.manifest.research_only is True
    assert artifact.manifest.execution_authorized is False
    assert artifact.manifest.paper_execution_authorized is False
    assert artifact.manifest.capital_authority == "NONE"
    assert artifact.manifest.live_trading == "BLOCKED"
    with pytest.raises(EnvironmentAttestationGovernanceError):
        replace(artifact.manifest, execution_authorized=True)


def test_write_read_is_canonical_and_tampering_fails(tmp_path) -> None:
    artifact = _artifact()
    path = tmp_path / "environment.json"
    artifact.write(path)
    assert EnvironmentAttestation.read(path) == artifact

    document = json.loads(path.read_text(encoding="utf-8"))
    document["distributions"][0]["version"] = "999"
    path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    with pytest.raises(EnvironmentAttestationIntegrityError):
        EnvironmentAttestation.read(path)


def test_noncanonical_serialization_and_duplicate_json_keys_fail(tmp_path) -> None:
    artifact = _artifact()
    path = tmp_path / "environment.json"
    path.write_text(json.dumps(artifact.to_dict(), indent=2) + "\n", encoding="utf-8")
    with pytest.raises(EnvironmentAttestationIntegrityError, match="serialization is not canonical"):
        EnvironmentAttestation.read(path)

    path.write_text('{"artifact_version":"x","artifact_version":"y"}\n', encoding="utf-8")
    with pytest.raises(EnvironmentAttestationIntegrityError, match="duplicate JSON key"):
        EnvironmentAttestation.read(path)


def test_actual_installed_environment_round_trip_after_qlib_install(tmp_path) -> None:
    pytest.importorskip("qlib")
    artifact = collect_environment_attestation()
    names = {item.name for item in artifact.distributions}
    assert "pyqlib" in names
    assert "numpy" in names
    assert "pandas" in names
    assert "scikit-learn" in names
    assert artifact.manifest.qlib_version == QLIB_VERSION

    path = tmp_path / "observed_environment.json"
    artifact.write(path)
    assert EnvironmentAttestation.read(path) == artifact


def test_actual_collection_is_deterministic_within_same_process() -> None:
    pytest.importorskip("qlib")
    left = collect_environment_attestation()
    right = collect_environment_attestation()
    assert left == right
