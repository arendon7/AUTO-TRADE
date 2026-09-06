"""OSS-3D2C effective environment attestation for the isolated Qlib lab.

This module records the concrete Python/platform/distribution environment in
which the already-certified OSS-3D2B runner is executed.  It does not install,
import, configure or execute Qlib.  It deliberately records no environment
variable values, credentials, usernames, hostnames, paths, working directories
or other machine/user identifiers.

The artifact is evidence of runtime reproducibility only.  It grants no broker,
OMS, Safety, PAPER, capital or LIVE authority.
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

from .model_contract import QLIB_VERSION, model_config_hash, runner_code_hash


OSS3D2C_ATTESTATION_VERSION = "OSS3D2C_QLIB_ENVIRONMENT_ATTESTATION_V1"
MAX_ATTESTATION_BYTES = 5_000_000
REQUIRED_RUNTIME_DISTRIBUTIONS = (
    "pyqlib",
    "numpy",
    "pandas",
    "scikit-learn",
    "scipy",
    "joblib",
)
RUNTIME_FORBIDDEN_EVEN_IF_INSTALLED = (
    "mlflow",
    "redis",
)

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_DIST_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
_VERSION_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")
_NORMALIZE_RE = re.compile(r"[-_.]+")
_TOP_LEVEL_KEYS = frozenset({"attestation_version", "environment", "attestation_hash"})
_ENVIRONMENT_KEYS = frozenset(
    {
        "python_implementation",
        "python_version",
        "os_system",
        "os_release",
        "machine",
        "runner_code_hash",
        "model_config_hash",
        "requirements_sha256",
        "qlib_version",
        "required_runtime_distributions",
        "runtime_forbidden_installed",
        "installed_distribution_count",
        "installed_distributions",
        "installed_manifest_hash",
        "execution_authorized",
        "paper_execution_authorized",
        "capital_authority",
        "live_trading",
    }
)
_DIST_KEYS = frozenset({"name", "version"})


class EnvironmentAttestationError(RuntimeError):
    """Base OSS-3D2C attestation failure."""


class EnvironmentAttestationIntegrityError(EnvironmentAttestationError):
    """Serialized or derived environment identity is inconsistent."""


class EnvironmentAttestationGovernanceError(EnvironmentAttestationError):
    """Environment evidence violates the research-only contract."""


@dataclass(frozen=True, slots=True, order=True)
class InstalledDistribution:
    name: str
    version: str

    def __post_init__(self) -> None:
        normalized = normalize_distribution_name(self.name)
        if normalized != self.name or not _DIST_NAME_RE.fullmatch(self.name):
            raise EnvironmentAttestationIntegrityError(
                "installed distribution name is not canonical"
            )
        if not isinstance(self.version, str) or not _VERSION_RE.fullmatch(self.version):
            raise EnvironmentAttestationIntegrityError(
                f"invalid installed distribution version for {self.name}"
            )

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "version": self.version}


@dataclass(frozen=True, slots=True)
class QlibEnvironmentAttestation:
    attestation_version: str
    python_implementation: str
    python_version: str
    os_system: str
    os_release: str
    machine: str
    runner_code_hash: str
    model_config_hash: str
    requirements_sha256: str
    qlib_version: str
    required_runtime_distributions: tuple[str, ...]
    runtime_forbidden_installed: tuple[str, ...]
    installed_distributions: tuple[InstalledDistribution, ...]
    installed_manifest_hash: str
    attestation_hash: str
    execution_authorized: bool = False
    paper_execution_authorized: bool = False
    capital_authority: str = "NONE"
    live_trading: str = "BLOCKED"

    def __post_init__(self) -> None:
        if self.attestation_version != OSS3D2C_ATTESTATION_VERSION:
            raise EnvironmentAttestationIntegrityError(
                "unsupported OSS-3D2C attestation version"
            )
        for field_name, value in (
            ("runner_code_hash", self.runner_code_hash),
            ("model_config_hash", self.model_config_hash),
            ("requirements_sha256", self.requirements_sha256),
            ("installed_manifest_hash", self.installed_manifest_hash),
            ("attestation_hash", self.attestation_hash),
        ):
            _require_hash(value, field_name)
        for field_name, value in (
            ("python_implementation", self.python_implementation),
            ("python_version", self.python_version),
            ("os_system", self.os_system),
            ("os_release", self.os_release),
            ("machine", self.machine),
        ):
            if not isinstance(value, str) or not value or len(value) > 128:
                raise EnvironmentAttestationIntegrityError(
                    f"invalid environment identity field: {field_name}"
                )
        if self.qlib_version != QLIB_VERSION:
            raise EnvironmentAttestationGovernanceError(
                "effective pyqlib version does not match certified OSS-3D2B"
            )
        if self.required_runtime_distributions != REQUIRED_RUNTIME_DISTRIBUTIONS:
            raise EnvironmentAttestationIntegrityError(
                "required runtime distribution contract drifted"
            )
        if tuple(sorted(self.runtime_forbidden_installed)) != self.runtime_forbidden_installed:
            raise EnvironmentAttestationIntegrityError(
                "runtime-forbidden installed package list is not canonical"
            )
        if any(
            item not in RUNTIME_FORBIDDEN_EVEN_IF_INSTALLED
            for item in self.runtime_forbidden_installed
        ):
            raise EnvironmentAttestationIntegrityError(
                "unknown runtime-forbidden installed package"
            )
        if not self.installed_distributions:
            raise EnvironmentAttestationGovernanceError(
                "effective environment contains no distributions"
            )
        if tuple(sorted(self.installed_distributions)) != self.installed_distributions:
            raise EnvironmentAttestationIntegrityError(
                "installed distribution manifest is not canonically sorted"
            )
        names = tuple(item.name for item in self.installed_distributions)
        if len(names) != len(set(names)):
            raise EnvironmentAttestationIntegrityError(
                "duplicate canonical distribution identity"
            )
        versions = {item.name: item.version for item in self.installed_distributions}
        missing = [name for name in REQUIRED_RUNTIME_DISTRIBUTIONS if name not in versions]
        if missing:
            raise EnvironmentAttestationGovernanceError(
                f"required Qlib runtime distributions are missing: {missing!r}"
            )
        if versions["pyqlib"] != QLIB_VERSION:
            raise EnvironmentAttestationGovernanceError(
                "installed pyqlib distribution version mismatch"
            )
        expected_forbidden = tuple(
            sorted(name for name in RUNTIME_FORBIDDEN_EVEN_IF_INSTALLED if name in versions)
        )
        if self.runtime_forbidden_installed != expected_forbidden:
            raise EnvironmentAttestationIntegrityError(
                "runtime-forbidden installed package evidence mismatch"
            )
        expected_manifest_hash = _manifest_hash(self.installed_distributions)
        if self.installed_manifest_hash != expected_manifest_hash:
            raise EnvironmentAttestationIntegrityError(
                "installed distribution manifest hash mismatch"
            )
        if self.execution_authorized or self.paper_execution_authorized:
            raise EnvironmentAttestationGovernanceError(
                "environment evidence cannot authorize execution"
            )
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise EnvironmentAttestationGovernanceError(
                "environment evidence cannot grant capital or LIVE authority"
            )
        expected_hash = _attestation_hash(self._payload_without_hash())
        if self.attestation_hash != expected_hash:
            raise EnvironmentAttestationIntegrityError("attestation hash mismatch")

    @property
    def installed_distribution_count(self) -> int:
        return len(self.installed_distributions)

    @property
    def fingerprint(self) -> str:
        return self.attestation_hash

    def package_version(self, name: str) -> str:
        canonical = normalize_distribution_name(name)
        for distribution in self.installed_distributions:
            if distribution.name == canonical:
                return distribution.version
        raise KeyError(canonical)

    def same_effective_environment(self, other: "QlibEnvironmentAttestation") -> bool:
        return isinstance(other, QlibEnvironmentAttestation) and (
            self.attestation_hash == other.attestation_hash
        )

    def _payload_without_hash(self) -> dict[str, object]:
        return {
            "attestation_version": self.attestation_version,
            "environment": {
                "python_implementation": self.python_implementation,
                "python_version": self.python_version,
                "os_system": self.os_system,
                "os_release": self.os_release,
                "machine": self.machine,
                "runner_code_hash": self.runner_code_hash,
                "model_config_hash": self.model_config_hash,
                "requirements_sha256": self.requirements_sha256,
                "qlib_version": self.qlib_version,
                "required_runtime_distributions": list(self.required_runtime_distributions),
                "runtime_forbidden_installed": list(self.runtime_forbidden_installed),
                "installed_distribution_count": self.installed_distribution_count,
                "installed_distributions": [
                    item.to_dict() for item in self.installed_distributions
                ],
                "installed_manifest_hash": self.installed_manifest_hash,
                "execution_authorized": self.execution_authorized,
                "paper_execution_authorized": self.paper_execution_authorized,
                "capital_authority": self.capital_authority,
                "live_trading": self.live_trading,
            },
        }

    def to_dict(self) -> dict[str, object]:
        payload = self._payload_without_hash()
        payload["attestation_hash"] = self.attestation_hash
        return payload

    def write(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = _canonical_json(self.to_dict()) + "\n"
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(raw, encoding="utf-8")
        temporary.replace(target)

    @classmethod
    def read(cls, path: str | Path) -> "QlibEnvironmentAttestation":
        target = Path(path)
        if not target.is_file():
            raise EnvironmentAttestationIntegrityError(
                "OSS-3D2C attestation does not exist"
            )
        if target.stat().st_size > MAX_ATTESTATION_BYTES:
            raise EnvironmentAttestationGovernanceError(
                "OSS-3D2C attestation exceeds size limit"
            )
        try:
            raw = target.read_text(encoding="utf-8")
            document = json.loads(raw, object_pairs_hook=_reject_duplicate_json_pairs)
        except EnvironmentAttestationError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise EnvironmentAttestationIntegrityError(
                "OSS-3D2C attestation is not valid UTF-8 JSON"
            ) from exc
        attestation = _from_document(document)
        expected_raw = _canonical_json(attestation.to_dict()) + "\n"
        if raw != expected_raw:
            raise EnvironmentAttestationIntegrityError(
                "OSS-3D2C attestation serialization is not canonical"
            )
        return attestation


def collect_environment_attestation(
    *, lab_root: Path | None = None,
    distributions: Iterable[InstalledDistribution] | None = None,
) -> QlibEnvironmentAttestation:
    root = Path(__file__).resolve().parent if lab_root is None else Path(lab_root)
    requirements = root / "requirements.txt"
    if not requirements.is_file():
        raise EnvironmentAttestationGovernanceError(
            "certified OSS-3D2B requirements.txt is missing"
        )
    manifest = (
        _collect_installed_distributions()
        if distributions is None
        else _canonicalize_distributions(distributions)
    )
    versions = {item.name: item.version for item in manifest}
    missing = [name for name in REQUIRED_RUNTIME_DISTRIBUTIONS if name not in versions]
    if missing:
        raise EnvironmentAttestationGovernanceError(
            f"required Qlib runtime distributions are missing: {missing!r}"
        )
    if versions["pyqlib"] != QLIB_VERSION:
        raise EnvironmentAttestationGovernanceError(
            "effective pyqlib version does not match certified OSS-3D2B"
        )
    forbidden_installed = tuple(
        sorted(name for name in RUNTIME_FORBIDDEN_EVEN_IF_INSTALLED if name in versions)
    )
    payload = {
        "attestation_version": OSS3D2C_ATTESTATION_VERSION,
        "environment": {
            "python_implementation": platform.python_implementation(),
            "python_version": ".".join(str(value) for value in sys.version_info[:3]),
            "os_system": platform.system(),
            "os_release": platform.release(),
            "machine": platform.machine(),
            "runner_code_hash": runner_code_hash(lab_root=root),
            "model_config_hash": model_config_hash(),
            "requirements_sha256": sha256(requirements.read_bytes()).hexdigest(),
            "qlib_version": versions["pyqlib"],
            "required_runtime_distributions": list(REQUIRED_RUNTIME_DISTRIBUTIONS),
            "runtime_forbidden_installed": list(forbidden_installed),
            "installed_distribution_count": len(manifest),
            "installed_distributions": [item.to_dict() for item in manifest],
            "installed_manifest_hash": _manifest_hash(manifest),
            "execution_authorized": False,
            "paper_execution_authorized": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        },
    }
    return QlibEnvironmentAttestation(
        attestation_version=OSS3D2C_ATTESTATION_VERSION,
        python_implementation=str(payload["environment"]["python_implementation"]),
        python_version=str(payload["environment"]["python_version"]),
        os_system=str(payload["environment"]["os_system"]),
        os_release=str(payload["environment"]["os_release"]),
        machine=str(payload["environment"]["machine"]),
        runner_code_hash=str(payload["environment"]["runner_code_hash"]),
        model_config_hash=str(payload["environment"]["model_config_hash"]),
        requirements_sha256=str(payload["environment"]["requirements_sha256"]),
        qlib_version=str(payload["environment"]["qlib_version"]),
        required_runtime_distributions=REQUIRED_RUNTIME_DISTRIBUTIONS,
        runtime_forbidden_installed=forbidden_installed,
        installed_distributions=manifest,
        installed_manifest_hash=str(payload["environment"]["installed_manifest_hash"]),
        attestation_hash=_attestation_hash(payload),
    )


def normalize_distribution_name(name: str) -> str:
    if not isinstance(name, str) or not name:
        raise EnvironmentAttestationIntegrityError("distribution name must be non-empty")
    return _NORMALIZE_RE.sub("-", name).lower()


def _collect_installed_distributions() -> tuple[InstalledDistribution, ...]:
    items: list[InstalledDistribution] = []
    for distribution in metadata.distributions():
        raw_name = distribution.metadata.get("Name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise EnvironmentAttestationIntegrityError(
                "installed distribution is missing canonical Name metadata"
            )
        version = distribution.version
        items.append(
            InstalledDistribution(
                name=normalize_distribution_name(raw_name.strip()),
                version=version,
            )
        )
    return _canonicalize_distributions(items)


def _canonicalize_distributions(
    distributions: Iterable[InstalledDistribution],
) -> tuple[InstalledDistribution, ...]:
    versions_by_name: dict[str, set[str]] = {}
    for item in distributions:
        if not isinstance(item, InstalledDistribution):
            raise EnvironmentAttestationIntegrityError(
                "distribution manifest contains a noncanonical entry"
            )
        versions_by_name.setdefault(item.name, set()).add(item.version)

    manifest: list[InstalledDistribution] = []
    for name in sorted(versions_by_name):
        versions = sorted(versions_by_name[name])
        if len(versions) != 1:
            raise EnvironmentAttestationIntegrityError(
                f"conflicting installed versions for canonical distribution {name}"
            )
        manifest.append(InstalledDistribution(name=name, version=versions[0]))
    return tuple(manifest)


def _from_document(document: object) -> QlibEnvironmentAttestation:
    if not isinstance(document, dict) or frozenset(document) != _TOP_LEVEL_KEYS:
        raise EnvironmentAttestationIntegrityError("attestation top-level schema mismatch")
    environment = document["environment"]
    if not isinstance(environment, dict) or frozenset(environment) != _ENVIRONMENT_KEYS:
        raise EnvironmentAttestationIntegrityError("attestation environment schema mismatch")
    raw_distributions = environment["installed_distributions"]
    if not isinstance(raw_distributions, list):
        raise EnvironmentAttestationIntegrityError(
            "installed_distributions must be an array"
        )
    distributions: list[InstalledDistribution] = []
    for value in raw_distributions:
        if not isinstance(value, dict) or frozenset(value) != _DIST_KEYS:
            raise EnvironmentAttestationIntegrityError(
                "installed distribution schema mismatch"
            )
        name = value["name"]
        version = value["version"]
        if not isinstance(name, str) or not isinstance(version, str):
            raise EnvironmentAttestationIntegrityError(
                "installed distribution fields must be strings"
            )
        distributions.append(InstalledDistribution(name=name, version=version))
    required = _string_tuple(environment, "required_runtime_distributions")
    forbidden = _string_tuple(environment, "runtime_forbidden_installed")
    count = environment["installed_distribution_count"]
    if not isinstance(count, int) or isinstance(count, bool) or count != len(distributions):
        raise EnvironmentAttestationIntegrityError(
            "installed_distribution_count mismatch"
        )
    attestation_hash = document["attestation_hash"]
    if not isinstance(attestation_hash, str):
        raise EnvironmentAttestationIntegrityError("attestation_hash must be a string")
    return QlibEnvironmentAttestation(
        attestation_version=_string(document, "attestation_version"),
        python_implementation=_string(environment, "python_implementation"),
        python_version=_string(environment, "python_version"),
        os_system=_string(environment, "os_system"),
        os_release=_string(environment, "os_release"),
        machine=_string(environment, "machine"),
        runner_code_hash=_string(environment, "runner_code_hash"),
        model_config_hash=_string(environment, "model_config_hash"),
        requirements_sha256=_string(environment, "requirements_sha256"),
        qlib_version=_string(environment, "qlib_version"),
        required_runtime_distributions=required,
        runtime_forbidden_installed=forbidden,
        installed_distributions=tuple(distributions),
        installed_manifest_hash=_string(environment, "installed_manifest_hash"),
        attestation_hash=attestation_hash,
        execution_authorized=_bool(environment, "execution_authorized"),
        paper_execution_authorized=_bool(environment, "paper_execution_authorized"),
        capital_authority=_string(environment, "capital_authority"),
        live_trading=_string(environment, "live_trading"),
    )


def _manifest_hash(distributions: tuple[InstalledDistribution, ...]) -> str:
    return sha256(
        _canonical_json([item.to_dict() for item in distributions]).encode("utf-8")
    ).hexdigest()


def _attestation_hash(payload: Mapping[str, object]) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _reject_duplicate_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise EnvironmentAttestationIntegrityError(
                f"duplicate JSON object key: {key}"
            )
        document[key] = value
    return document


def _string(data: Mapping[str, object], key: str) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise EnvironmentAttestationIntegrityError(f"{key} must be a string")
    return value


def _string_tuple(data: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = data[key]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise EnvironmentAttestationIntegrityError(f"{key} must be a string array")
    return tuple(value)


def _bool(data: Mapping[str, object], key: str) -> bool:
    value = data[key]
    if not isinstance(value, bool):
        raise EnvironmentAttestationIntegrityError(f"{key} must be boolean")
    return value


def _require_hash(value: str, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise EnvironmentAttestationIntegrityError(
            f"{name} must be a lowercase sha256"
        )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Capture canonical OSS-3D2C Qlib environment evidence"
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    attestation = collect_environment_attestation()
    attestation.write(args.output)
    print(attestation.attestation_hash)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
