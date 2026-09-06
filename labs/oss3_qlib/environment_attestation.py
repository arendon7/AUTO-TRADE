"""OSS-3D2C canonical environment attestation for the isolated Qlib lab.

The artifact records only reproducibility metadata. It deliberately excludes
paths, environment variables, hostnames, usernames, timestamps, credentials,
network state and hardware identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib import metadata
import json
from pathlib import Path
import platform
import re
import sys
from typing import Iterable, Mapping

from labs.oss3_qlib.model_contract import (
    MODEL_FAMILY,
    QLIB_VERSION,
    model_config_hash,
    runner_code_hash,
)


ARTIFACT_VERSION = "OSS3D2C_ENVIRONMENT_ATTESTATION_V1"
POLICY_ID = "SANITIZED_INSTALLED_DISTRIBUTIONS_V1"
MAX_ARTIFACT_BYTES = 2_000_000
MAX_DISTRIBUTIONS = 4096

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_CANON_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.!+_-]{0,127}$")
_TOP_LEVEL_KEYS = frozenset({"artifact_version", "manifest", "distributions", "artifact_hash"})
_MANIFEST_KEYS = frozenset(
    {
        "policy_id",
        "python_implementation",
        "python_version",
        "platform_system",
        "platform_machine",
        "libc_name",
        "libc_version",
        "qlib_distribution",
        "qlib_version",
        "model_family",
        "model_config_hash",
        "runner_code_hash",
        "distribution_count",
        "distribution_set_hash",
        "research_only",
        "execution_authorized",
        "paper_execution_authorized",
        "capital_authority",
        "live_trading",
    }
)
_DISTRIBUTION_KEYS = frozenset({"name", "version"})


class EnvironmentAttestationError(RuntimeError):
    """Base OSS-3D2C failure."""


class EnvironmentAttestationIntegrityError(EnvironmentAttestationError):
    """Serialized or in-memory artifact identity is inconsistent."""


class EnvironmentAttestationGovernanceError(EnvironmentAttestationError):
    """Artifact violates the sanitized research-only environment policy."""


def canonical_distribution_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("distribution name must be non-empty")
    canonical = re.sub(r"[-_.]+", "-", value.strip()).lower()
    if not _CANON_NAME_RE.fullmatch(canonical):
        raise ValueError("invalid canonical distribution name")
    return canonical


@dataclass(frozen=True, slots=True, order=True)
class InstalledDistribution:
    name: str
    version: str

    def __post_init__(self) -> None:
        if self.name != canonical_distribution_name(self.name):
            raise ValueError("distribution name must already be canonical")
        if not isinstance(self.version, str) or not _VERSION_RE.fullmatch(self.version):
            raise ValueError("invalid distribution version")
        if any(token in self.version for token in ("/", "\\", " ")):
            raise EnvironmentAttestationGovernanceError(
                "distribution version must not contain paths or whitespace"
            )

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "version": self.version}


@dataclass(frozen=True, slots=True)
class EnvironmentManifest:
    policy_id: str
    python_implementation: str
    python_version: str
    platform_system: str
    platform_machine: str
    libc_name: str
    libc_version: str
    qlib_distribution: str
    qlib_version: str
    model_family: str
    model_config_hash: str
    runner_code_hash: str
    distribution_count: int
    distribution_set_hash: str
    research_only: bool = True
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if self.policy_id != POLICY_ID:
            raise EnvironmentAttestationGovernanceError("noncanonical OSS-3D2C policy")
        if self.python_implementation != self.python_implementation.lower():
            raise ValueError("python implementation must be lowercase")
        if not _VERSION_RE.fullmatch(self.python_version):
            raise ValueError("invalid Python version")
        if not self.platform_system or not self.platform_machine:
            raise ValueError("platform identity must be non-empty")
        if self.qlib_distribution != "pyqlib" or self.qlib_version != QLIB_VERSION:
            raise EnvironmentAttestationGovernanceError("Qlib runtime identity drifted")
        if self.model_family != MODEL_FAMILY:
            raise EnvironmentAttestationGovernanceError("model family drifted")
        _require_hash(self.model_config_hash, "model_config_hash")
        _require_hash(self.runner_code_hash, "runner_code_hash")
        _require_hash(self.distribution_set_hash, "distribution_set_hash")
        if not isinstance(self.distribution_count, int) or isinstance(self.distribution_count, bool):
            raise TypeError("distribution_count must be an integer")
        if not 1 <= self.distribution_count <= MAX_DISTRIBUTIONS:
            raise EnvironmentAttestationGovernanceError("distribution_count outside bound")
        if not self.research_only:
            raise EnvironmentAttestationGovernanceError("OSS-3D2C must remain research-only")
        if self.execution_authorized or self.paper_execution_authorized:
            raise EnvironmentAttestationGovernanceError("OSS-3D2C cannot authorize execution")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise EnvironmentAttestationGovernanceError("OSS-3D2C cannot grant capital or LIVE")

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_id": self.policy_id,
            "python_implementation": self.python_implementation,
            "python_version": self.python_version,
            "platform_system": self.platform_system,
            "platform_machine": self.platform_machine,
            "libc_name": self.libc_name,
            "libc_version": self.libc_version,
            "qlib_distribution": self.qlib_distribution,
            "qlib_version": self.qlib_version,
            "model_family": self.model_family,
            "model_config_hash": self.model_config_hash,
            "runner_code_hash": self.runner_code_hash,
            "distribution_count": self.distribution_count,
            "distribution_set_hash": self.distribution_set_hash,
            "research_only": self.research_only,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


@dataclass(frozen=True, slots=True)
class EnvironmentAttestation:
    artifact_version: str
    manifest: EnvironmentManifest
    distributions: tuple[InstalledDistribution, ...]
    artifact_hash: str

    def __post_init__(self) -> None:
        if self.artifact_version != ARTIFACT_VERSION:
            raise EnvironmentAttestationIntegrityError("unsupported OSS-3D2C artifact version")
        _require_hash(self.artifact_hash, "artifact_hash")
        _validate_distributions(self.distributions)
        if len(self.distributions) != self.manifest.distribution_count:
            raise EnvironmentAttestationIntegrityError("distribution_count mismatch")
        if _distribution_set_hash(self.distributions) != self.manifest.distribution_set_hash:
            raise EnvironmentAttestationIntegrityError("distribution_set_hash mismatch")
        qlib_versions = [item.version for item in self.distributions if item.name == "pyqlib"]
        if qlib_versions != [QLIB_VERSION]:
            raise EnvironmentAttestationGovernanceError("exact pyqlib distribution is required")
        expected = _artifact_hash(
            artifact_version=self.artifact_version,
            manifest=self.manifest,
            distributions=self.distributions,
        )
        if self.artifact_hash != expected:
            raise EnvironmentAttestationIntegrityError("artifact hash mismatch")

    @classmethod
    def build(
        cls,
        *,
        distributions: Iterable[InstalledDistribution],
        python_implementation: str,
        python_version: str,
        platform_system: str,
        platform_machine: str,
        libc_name: str,
        libc_version: str,
    ) -> "EnvironmentAttestation":
        canonical = tuple(sorted(distributions))
        _validate_distributions(canonical)
        manifest = EnvironmentManifest(
            policy_id=POLICY_ID,
            python_implementation=python_implementation,
            python_version=python_version,
            platform_system=platform_system,
            platform_machine=platform_machine,
            libc_name=libc_name,
            libc_version=libc_version,
            qlib_distribution="pyqlib",
            qlib_version=QLIB_VERSION,
            model_family=MODEL_FAMILY,
            model_config_hash=model_config_hash(),
            runner_code_hash=runner_code_hash(),
            distribution_count=len(canonical),
            distribution_set_hash=_distribution_set_hash(canonical),
        )
        artifact_hash = _artifact_hash(
            artifact_version=ARTIFACT_VERSION,
            manifest=manifest,
            distributions=canonical,
        )
        return cls(
            artifact_version=ARTIFACT_VERSION,
            manifest=manifest,
            distributions=canonical,
            artifact_hash=artifact_hash,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_version": self.artifact_version,
            "manifest": self.manifest.to_dict(),
            "distributions": [item.to_dict() for item in self.distributions],
            "artifact_hash": self.artifact_hash,
        }

    def write(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = _canonical_json(self.to_dict()) + "\n"
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(raw, encoding="utf-8")
        temporary.replace(target)

    @classmethod
    def read(cls, path: str | Path) -> "EnvironmentAttestation":
        target = Path(path)
        if not target.is_file():
            raise EnvironmentAttestationIntegrityError("attestation artifact does not exist")
        if target.stat().st_size > MAX_ARTIFACT_BYTES:
            raise EnvironmentAttestationGovernanceError("attestation artifact exceeds size bound")
        try:
            raw = target.read_text(encoding="utf-8")
            document = json.loads(raw, object_pairs_hook=_reject_duplicate_json_pairs)
        except EnvironmentAttestationError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise EnvironmentAttestationIntegrityError("artifact is not valid UTF-8 JSON") from exc
        if not isinstance(document, dict) or frozenset(document) != _TOP_LEVEL_KEYS:
            raise EnvironmentAttestationIntegrityError("top-level schema mismatch")
        manifest_raw = document["manifest"]
        distributions_raw = document["distributions"]
        if not isinstance(manifest_raw, dict) or frozenset(manifest_raw) != _MANIFEST_KEYS:
            raise EnvironmentAttestationIntegrityError("manifest schema mismatch")
        if not isinstance(distributions_raw, list):
            raise EnvironmentAttestationIntegrityError("distributions must be an array")
        try:
            artifact = cls(
                artifact_version=_string(document, "artifact_version"),
                manifest=_manifest_from_mapping(manifest_raw),
                distributions=tuple(_distribution_from_value(value) for value in distributions_raw),
                artifact_hash=_string(document, "artifact_hash"),
            )
        except EnvironmentAttestationError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise EnvironmentAttestationIntegrityError("invalid attestation fields") from exc
        if raw != _canonical_json(artifact.to_dict()) + "\n":
            raise EnvironmentAttestationIntegrityError("artifact serialization is not canonical")
        return artifact


def collect_environment_attestation() -> EnvironmentAttestation:
    distributions: dict[str, str] = {}
    for distribution in metadata.distributions():
        raw_name = distribution.metadata.get("Name")
        if not raw_name:
            raise EnvironmentAttestationIntegrityError("installed distribution has no Name metadata")
        name = canonical_distribution_name(raw_name)
        version = distribution.version
        existing = distributions.get(name)
        if existing is not None and existing != version:
            raise EnvironmentAttestationIntegrityError(
                f"conflicting installed versions for canonical distribution {name}"
            )
        distributions[name] = version
    libc_name, libc_version = platform.libc_ver()
    return EnvironmentAttestation.build(
        distributions=(
            InstalledDistribution(name=name, version=version)
            for name, version in distributions.items()
        ),
        python_implementation=sys.implementation.name.lower(),
        python_version=platform.python_version(),
        platform_system=platform.system().lower(),
        platform_machine=platform.machine().lower(),
        libc_name=(libc_name or "unknown").lower(),
        libc_version=libc_version or "unknown",
    )


def _validate_distributions(distributions: tuple[InstalledDistribution, ...]) -> None:
    if not distributions:
        raise EnvironmentAttestationGovernanceError("at least one installed distribution is required")
    if len(distributions) > MAX_DISTRIBUTIONS:
        raise EnvironmentAttestationGovernanceError("too many installed distributions")
    if distributions != tuple(sorted(distributions)):
        raise EnvironmentAttestationGovernanceError("distributions must be canonically sorted")
    names = [item.name for item in distributions]
    if len(names) != len(set(names)):
        raise EnvironmentAttestationGovernanceError("duplicate canonical distribution name")


def _distribution_set_hash(distributions: tuple[InstalledDistribution, ...]) -> str:
    return _hash([item.to_dict() for item in distributions])


def _artifact_hash(
    *,
    artifact_version: str,
    manifest: EnvironmentManifest,
    distributions: tuple[InstalledDistribution, ...],
) -> str:
    return _hash(
        {
            "artifact_version": artifact_version,
            "manifest": manifest.to_dict(),
            "distributions": [item.to_dict() for item in distributions],
        }
    )


def _manifest_from_mapping(data: Mapping[str, object]) -> EnvironmentManifest:
    return EnvironmentManifest(
        policy_id=_string(data, "policy_id"),
        python_implementation=_string(data, "python_implementation"),
        python_version=_string(data, "python_version"),
        platform_system=_string(data, "platform_system"),
        platform_machine=_string(data, "platform_machine"),
        libc_name=_string(data, "libc_name"),
        libc_version=_string(data, "libc_version"),
        qlib_distribution=_string(data, "qlib_distribution"),
        qlib_version=_string(data, "qlib_version"),
        model_family=_string(data, "model_family"),
        model_config_hash=_string(data, "model_config_hash"),
        runner_code_hash=_string(data, "runner_code_hash"),
        distribution_count=_integer(data, "distribution_count"),
        distribution_set_hash=_string(data, "distribution_set_hash"),
        research_only=_boolean(data, "research_only"),
        execution_authorized=_boolean(data, "execution_authorized"),
        paper_execution_authorized=_boolean(data, "paper_execution_authorized"),
        capital_authority=_string(data, "capital_authority"),
        live_trading=_string(data, "live_trading"),
    )


def _distribution_from_value(value: object) -> InstalledDistribution:
    if not isinstance(value, dict) or frozenset(value) != _DISTRIBUTION_KEYS:
        raise EnvironmentAttestationIntegrityError("distribution schema mismatch")
    return InstalledDistribution(name=_string(value, "name"), version=_string(value, "version"))


def _reject_duplicate_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EnvironmentAttestationIntegrityError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _string(data: Mapping[str, object], key: str) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _integer(data: Mapping[str, object], key: str) -> int:
    value = data[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{key} must be an integer")
    return value


def _boolean(data: Mapping[str, object], key: str) -> bool:
    value = data[key]
    if not isinstance(value, bool):
        raise TypeError(f"{key} must be a boolean")
    return value


def _require_hash(value: str, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase sha256")


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(payload: object) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
