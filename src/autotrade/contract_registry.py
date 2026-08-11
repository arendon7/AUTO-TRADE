from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping


class ContractRegistryError(ValueError):
    pass


class ContractValidationError(ValueError):
    pass


class ContractCompatibilityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FieldSpec:
    type_name: str
    required: bool = True
    non_empty: bool = False
    enum: tuple[str, ...] = ()
    items_contract: str = ""
    object_contract: str = ""


@dataclass(frozen=True, slots=True)
class ContractSpec:
    name: str
    version: int
    compatibility: str
    fields: Mapping[str, FieldSpec]
    allow_extra_fields: bool = False

    @property
    def contract_id(self) -> str:
        return f"{self.name}@{self.version}"

    @property
    def fingerprint(self) -> str:
        raw = json.dumps(
            _contract_document(self),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return sha256(raw).hexdigest()


class ContractRegistry:
    def __init__(self, document: Mapping[str, object]) -> None:
        self.registry_version = _positive_int(document.get("registry_version"), "registry_version")
        raw_contracts = document.get("contracts")
        if not isinstance(raw_contracts, list) or not raw_contracts:
            raise ContractRegistryError("contracts must be a non-empty array")
        self._contracts: dict[str, ContractSpec] = {}
        for raw in raw_contracts:
            spec = _parse_contract(raw)
            if spec.contract_id in self._contracts:
                raise ContractRegistryError(f"duplicate contract id: {spec.contract_id}")
            self._contracts[spec.contract_id] = spec
        for spec in self._contracts.values():
            for field in spec.fields.values():
                for referenced in (field.items_contract, field.object_contract):
                    if referenced and referenced not in self._contracts:
                        raise ContractRegistryError(
                            f"{spec.contract_id} references missing contract {referenced}"
                        )

    @classmethod
    def load_default(cls) -> "ContractRegistry":
        path = Path(__file__).resolve().parent / "contracts" / "registry.json"
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def get(self, contract_id: str) -> ContractSpec:
        try:
            return self._contracts[contract_id]
        except KeyError as exc:
            raise ContractRegistryError(f"unknown contract: {contract_id}") from exc

    def all_contracts(self) -> tuple[ContractSpec, ...]:
        return tuple(
            sorted(self._contracts.values(), key=lambda spec: (spec.name, spec.version))
        )

    def validate(self, contract_id: str, payload: Mapping[str, object]) -> None:
        spec = self.get(contract_id)
        if not isinstance(payload, Mapping):
            raise ContractValidationError(f"{contract_id} payload must be an object")
        unknown = set(payload) - set(spec.fields)
        if unknown and not spec.allow_extra_fields:
            raise ContractValidationError(
                f"{contract_id} unknown fields: {sorted(unknown)}"
            )
        missing = [
            name for name, field in spec.fields.items() if field.required and name not in payload
        ]
        if missing:
            raise ContractValidationError(
                f"{contract_id} missing required fields: {sorted(missing)}"
            )
        for name, value in payload.items():
            field = spec.fields.get(name)
            if field is None:
                continue
            _validate_field(
                registry=self,
                contract_id=contract_id,
                field_name=name,
                spec=field,
                value=value,
            )

    def assert_additive_compatible(self, old_id: str, new_id: str) -> None:
        old = self.get(old_id)
        new = self.get(new_id)
        if old.name != new.name:
            raise ContractCompatibilityError("contract names must match")
        if new.version <= old.version:
            raise ContractCompatibilityError("new contract version must increase")
        if old.compatibility != "additive" or new.compatibility != "additive":
            raise ContractCompatibilityError("both contracts must declare additive compatibility")
        for name, old_field in old.fields.items():
            new_field = new.fields.get(name)
            if new_field is None:
                raise ContractCompatibilityError(f"field removed: {name}")
            if new_field != old_field:
                raise ContractCompatibilityError(f"existing field changed: {name}")
        for name, new_field in new.fields.items():
            if name not in old.fields and new_field.required:
                raise ContractCompatibilityError(
                    f"new additive field must be optional: {name}"
                )
        if old.allow_extra_fields != new.allow_extra_fields:
            raise ContractCompatibilityError("allow_extra_fields policy changed")

    def registry_fingerprint(self) -> str:
        document = {
            "registry_version": self.registry_version,
            "contracts": [
                {
                    **_contract_document(spec),
                    "fingerprint": spec.fingerprint,
                }
                for spec in self.all_contracts()
            ],
        }
        raw = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return sha256(raw).hexdigest()


def _parse_contract(raw: object) -> ContractSpec:
    if not isinstance(raw, dict):
        raise ContractRegistryError("contract entries must be objects")
    allowed = {"name", "version", "compatibility", "allow_extra_fields", "fields"}
    unknown = set(raw) - allowed
    if unknown:
        raise ContractRegistryError(f"unknown contract keys: {sorted(unknown)}")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ContractRegistryError("contract name is required")
    version = _positive_int(raw.get("version"), f"{name}.version")
    compatibility = raw.get("compatibility")
    if compatibility not in {"additive", "strict"}:
        raise ContractRegistryError(f"{name} compatibility must be additive or strict")
    allow_extra = raw.get("allow_extra_fields", False)
    if not isinstance(allow_extra, bool):
        raise ContractRegistryError(f"{name}.allow_extra_fields must be boolean")
    raw_fields = raw.get("fields")
    if not isinstance(raw_fields, dict) or not raw_fields:
        raise ContractRegistryError(f"{name}.fields must be a non-empty object")
    fields: dict[str, FieldSpec] = {}
    for field_name, field_raw in raw_fields.items():
        if not isinstance(field_name, str) or not field_name.strip():
            raise ContractRegistryError(f"{name} contains invalid field name")
        if not isinstance(field_raw, dict):
            raise ContractRegistryError(f"{name}.{field_name} must be an object")
        field_unknown = set(field_raw) - {
            "type",
            "required",
            "non_empty",
            "enum",
            "items_contract",
            "object_contract",
        }
        if field_unknown:
            raise ContractRegistryError(
                f"{name}.{field_name} unknown keys: {sorted(field_unknown)}"
            )
        type_name = field_raw.get("type")
        if type_name not in {
            "string",
            "decimal",
            "nullable_decimal",
            "integer",
            "boolean",
            "timestamp",
            "nullable_timestamp",
            "object",
            "array",
        }:
            raise ContractRegistryError(f"{name}.{field_name} has unsupported type")
        required = field_raw.get("required", True)
        non_empty = field_raw.get("non_empty", False)
        if not isinstance(required, bool) or not isinstance(non_empty, bool):
            raise ContractRegistryError(f"{name}.{field_name} flags must be boolean")
        raw_enum = field_raw.get("enum", [])
        if not isinstance(raw_enum, list) or any(not isinstance(v, str) for v in raw_enum):
            raise ContractRegistryError(f"{name}.{field_name}.enum must be string array")
        items_contract = field_raw.get("items_contract", "")
        object_contract = field_raw.get("object_contract", "")
        if not isinstance(items_contract, str) or not isinstance(object_contract, str):
            raise ContractRegistryError(f"{name}.{field_name} contract references must be strings")
        if items_contract and type_name != "array":
            raise ContractRegistryError(
                f"{name}.{field_name}.items_contract requires array type"
            )
        if object_contract and type_name != "object":
            raise ContractRegistryError(
                f"{name}.{field_name}.object_contract requires object type"
            )
        fields[field_name] = FieldSpec(
            type_name=type_name,
            required=required,
            non_empty=non_empty,
            enum=tuple(raw_enum),
            items_contract=items_contract,
            object_contract=object_contract,
        )
    return ContractSpec(
        name=name,
        version=version,
        compatibility=compatibility,
        fields=fields,
        allow_extra_fields=allow_extra,
    )


def _validate_field(
    *,
    registry: ContractRegistry,
    contract_id: str,
    field_name: str,
    spec: FieldSpec,
    value: object,
) -> None:
    prefix = f"{contract_id}.{field_name}"
    if spec.type_name == "string":
        if not isinstance(value, str):
            raise ContractValidationError(f"{prefix} must be string")
        if spec.non_empty and not value.strip():
            raise ContractValidationError(f"{prefix} must be non-empty")
    elif spec.type_name in {"decimal", "nullable_decimal"}:
        if value is None and spec.type_name == "nullable_decimal":
            return
        if not isinstance(value, str):
            raise ContractValidationError(f"{prefix} decimal must be encoded as string")
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise ContractValidationError(f"{prefix} invalid decimal") from exc
        if not parsed.is_finite():
            raise ContractValidationError(f"{prefix} decimal must be finite")
    elif spec.type_name == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ContractValidationError(f"{prefix} must be integer")
    elif spec.type_name == "boolean":
        if not isinstance(value, bool):
            raise ContractValidationError(f"{prefix} must be boolean")
    elif spec.type_name in {"timestamp", "nullable_timestamp"}:
        if value is None and spec.type_name == "nullable_timestamp":
            return
        if not isinstance(value, str):
            raise ContractValidationError(f"{prefix} must be ISO timestamp string")
        try:
            parsed_time = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ContractValidationError(f"{prefix} invalid timestamp") from exc
        if parsed_time.tzinfo is None or parsed_time.utcoffset() is None:
            raise ContractValidationError(f"{prefix} timestamp must be timezone-aware")
    elif spec.type_name == "object":
        if not isinstance(value, dict):
            raise ContractValidationError(f"{prefix} must be object")
        if spec.object_contract:
            registry.validate(spec.object_contract, value)
    elif spec.type_name == "array":
        if not isinstance(value, list):
            raise ContractValidationError(f"{prefix} must be array")
        if spec.items_contract:
            for index, item in enumerate(value):
                if not isinstance(item, dict):
                    raise ContractValidationError(
                        f"{prefix}[{index}] must be object for {spec.items_contract}"
                    )
                registry.validate(spec.items_contract, item)
    else:
        raise AssertionError(spec.type_name)

    if spec.enum and value not in spec.enum:
        raise ContractValidationError(
            f"{prefix} must be one of {list(spec.enum)}"
        )


def _contract_document(spec: ContractSpec) -> dict[str, object]:
    return {
        "name": spec.name,
        "version": spec.version,
        "compatibility": spec.compatibility,
        "allow_extra_fields": spec.allow_extra_fields,
        "fields": {
            name: {
                "type": field.type_name,
                "required": field.required,
                "non_empty": field.non_empty,
                "enum": list(field.enum),
                "items_contract": field.items_contract,
                "object_contract": field.object_contract,
            }
            for name, field in sorted(spec.fields.items())
        },
    }


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ContractRegistryError(f"{name} must be integer > 0")
    return value
