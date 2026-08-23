from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
import re

from autotrade.paper_execution_scenarios import PaperExecutionScenarioMatrix
from autotrade.research.costs import ExecutionCostModel


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
FEE_ACCOUNTING_MODE = "RESEARCH_COST_MODEL_REQUIRED"


class PaperExecutionQualificationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PaperExecutionQualificationContract:
    """Hash-bound continuity between R1 research costs and W78 execution stress.

    The contract deliberately grants no execution authority. It prevents Strategy
    Lab from qualifying a candidate with W78 slippage assumptions that are less
    conservative than the research model used to produce the candidate.

    Fees and half-spread remain explicit research accounting inputs. W78 market
    snapshots observe bid/ask directly and the core Fill type does not carry a
    fee field, so this contract records rather than fabricates fee accounting.
    """

    research_cost_model_hash: str
    scenario_matrix_hash: str
    research_fee_bps: Decimal
    research_half_spread_bps: Decimal
    research_slippage_bps: Decimal
    minimum_scenario_slippage_bps: Decimal
    maximum_scenario_slippage_bps: Decimal
    minimum_fill_fraction: Decimal
    scenario_count: int
    has_full_liquidity_case: bool
    has_execution_stress_case: bool
    fee_accounting_mode: str
    external_execution_authorized: bool
    live_trading: str
    contract_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("research_cost_model_hash", self.research_cost_model_hash),
            ("scenario_matrix_hash", self.scenario_matrix_hash),
            ("contract_hash", self.contract_hash),
        ):
            if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
                raise PaperExecutionQualificationError(f"{label} must be lowercase sha256")
        for label, value in (
            ("research_fee_bps", self.research_fee_bps),
            ("research_half_spread_bps", self.research_half_spread_bps),
            ("research_slippage_bps", self.research_slippage_bps),
            ("minimum_scenario_slippage_bps", self.minimum_scenario_slippage_bps),
            ("maximum_scenario_slippage_bps", self.maximum_scenario_slippage_bps),
            ("minimum_fill_fraction", self.minimum_fill_fraction),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise PaperExecutionQualificationError(f"{label} must be finite non-negative Decimal")
        if self.minimum_scenario_slippage_bps < self.research_slippage_bps:
            raise PaperExecutionQualificationError(
                "PAPER execution scenarios may not weaken research slippage assumptions"
            )
        if self.maximum_scenario_slippage_bps < self.minimum_scenario_slippage_bps:
            raise PaperExecutionQualificationError("scenario slippage range is invalid")
        if not Decimal("0") < self.minimum_fill_fraction <= Decimal("1"):
            raise PaperExecutionQualificationError("minimum_fill_fraction must be within (0,1]")
        if isinstance(self.scenario_count, bool) or not isinstance(self.scenario_count, int) or self.scenario_count < 2:
            raise PaperExecutionQualificationError("scenario_count must be integer >=2")
        if self.has_full_liquidity_case is not True:
            raise PaperExecutionQualificationError("qualification matrix requires a full-liquidity case")
        if self.has_execution_stress_case is not True:
            raise PaperExecutionQualificationError("qualification matrix requires a stressed execution case")
        if self.fee_accounting_mode != FEE_ACCOUNTING_MODE:
            raise PaperExecutionQualificationError("fee accounting mode is invalid")
        if self.external_execution_authorized is not False or self.live_trading != "BLOCKED":
            raise PaperExecutionQualificationError("qualification contract may not grant external/LIVE authority")
        if self.contract_hash != _hash(_payload(self, include_hash=False)):
            raise PaperExecutionQualificationError("qualification contract hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _payload(self, include_hash=True)


def bind_research_costs_to_paper_execution(
    *,
    cost_model: ExecutionCostModel,
    matrix: PaperExecutionScenarioMatrix,
) -> PaperExecutionQualificationContract:
    if not isinstance(cost_model, ExecutionCostModel):
        raise TypeError("cost_model must be ExecutionCostModel")
    if not isinstance(matrix, PaperExecutionScenarioMatrix):
        raise TypeError("matrix must be PaperExecutionScenarioMatrix")

    slippages = tuple(item.config.slippage_bps for item in matrix.scenarios)
    fill_fractions = tuple(item.config.max_fill_fraction for item in matrix.scenarios)
    minimum_slippage = min(slippages)
    maximum_slippage = max(slippages)
    minimum_fill = min(fill_fractions)

    if minimum_slippage < cost_model.slippage_bps:
        raise PaperExecutionQualificationError(
            "every W78 scenario must be at least as adverse as research slippage"
        )
    has_full_liquidity = any(value == Decimal("1") for value in fill_fractions)
    has_stress = any(
        scenario.config.slippage_bps > cost_model.slippage_bps
        or scenario.config.max_fill_fraction < Decimal("1")
        for scenario in matrix.scenarios
    )
    if not has_full_liquidity:
        raise PaperExecutionQualificationError("matrix requires at least one full-liquidity case")
    if not has_stress:
        raise PaperExecutionQualificationError(
            "matrix requires at least one scenario stricter than research execution assumptions"
        )

    cost_hash = _hash(cost_model.fingerprint_payload())
    values = {
        "research_cost_model_hash": cost_hash,
        "scenario_matrix_hash": matrix.matrix_hash,
        "research_fee_bps": cost_model.fee_bps,
        "research_half_spread_bps": cost_model.half_spread_bps,
        "research_slippage_bps": cost_model.slippage_bps,
        "minimum_scenario_slippage_bps": minimum_slippage,
        "maximum_scenario_slippage_bps": maximum_slippage,
        "minimum_fill_fraction": minimum_fill,
        "scenario_count": len(matrix.scenarios),
        "has_full_liquidity_case": has_full_liquidity,
        "has_execution_stress_case": has_stress,
        "fee_accounting_mode": FEE_ACCOUNTING_MODE,
        "external_execution_authorized": False,
        "live_trading": "BLOCKED",
    }
    return PaperExecutionQualificationContract(
        **values,
        contract_hash=_hash(_payload_from_values(values)),
    )


def _payload(value: PaperExecutionQualificationContract, *, include_hash: bool) -> dict[str, object]:
    payload = _payload_from_values(
        {
            "research_cost_model_hash": value.research_cost_model_hash,
            "scenario_matrix_hash": value.scenario_matrix_hash,
            "research_fee_bps": value.research_fee_bps,
            "research_half_spread_bps": value.research_half_spread_bps,
            "research_slippage_bps": value.research_slippage_bps,
            "minimum_scenario_slippage_bps": value.minimum_scenario_slippage_bps,
            "maximum_scenario_slippage_bps": value.maximum_scenario_slippage_bps,
            "minimum_fill_fraction": value.minimum_fill_fraction,
            "scenario_count": value.scenario_count,
            "has_full_liquidity_case": value.has_full_liquidity_case,
            "has_execution_stress_case": value.has_execution_stress_case,
            "fee_accounting_mode": value.fee_accounting_mode,
            "external_execution_authorized": value.external_execution_authorized,
            "live_trading": value.live_trading,
        }
    )
    if include_hash:
        payload["contract_hash"] = value.contract_hash
    return payload


def _payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    for key in (
        "research_fee_bps",
        "research_half_spread_bps",
        "research_slippage_bps",
        "minimum_scenario_slippage_bps",
        "maximum_scenario_slippage_bps",
        "minimum_fill_fraction",
    ):
        payload[key] = _decimal(payload[key])  # type: ignore[arg-type]
    return payload


def _decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return sha256(raw).hexdigest()


__all__ = [
    "FEE_ACCOUNTING_MODE",
    "PaperExecutionQualificationContract",
    "PaperExecutionQualificationError",
    "bind_research_costs_to_paper_execution",
]
