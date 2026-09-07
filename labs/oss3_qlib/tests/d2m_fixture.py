from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from labs.oss3_qlib.predictive_economic_protocol import (
    ECONOMIC_HOLDOUT_PURPOSE,
    OSS3D2M_HOLDOUT_COMMITMENT_VERSION,
    EconomicHoldoutCommitment,
)
from labs.oss3_qlib.predictive_strategy_contract import (
    OSS3PredictiveStrategyBinding,
    OSS3PredictiveStrategyPreregistrationReceipt,
    SQLiteOSS3PredictiveStrategyRegistry,
    build_predictive_strategy_binding,
)
from labs.oss3_qlib.tests.d2l_fixture import D2LSource, build_d2l_source
from labs.oss3_qlib.tests import d2i_fixture


UTC = timezone.utc
D2L_REGISTERED_AT = datetime(2026, 6, 15, tzinfo=UTC)
D2M_REGISTERED_AT = datetime(2026, 6, 16, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class D2MSource:
    d2l_source: D2LSource
    binding: OSS3PredictiveStrategyBinding
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt
    shared_sqlite_path: Path
    economic_holdout_commitment: EconomicHoldoutCommitment


def build_d2m_source(tmp_path) -> D2MSource:
    source = build_d2l_source(tmp_path)
    binding = build_predictive_strategy_binding(
        protocol=source.protocol,
        winner_output=source.winner_output,
    )
    shared = tmp_path / "d2l-d2m-d2k-shared.sqlite3"
    d2l_registry = SQLiteOSS3PredictiveStrategyRegistry(shared)
    d2l_receipt = d2l_registry.preregister(
        protocol=source.protocol,
        binding=binding,
        now=D2L_REGISTERED_AT,
    )

    predictive_holdout_end = datetime.fromisoformat(
        source.protocol.holdout_commitment.partition_end
    )
    economic_start = predictive_holdout_end + timedelta(days=1)
    economic_end = economic_start + timedelta(days=90)
    commitment = EconomicHoldoutCommitment(
        commitment_version=OSS3D2M_HOLDOUT_COMMITMENT_VERSION,
        commitment_id="oss3d2m-economic-holdout-001",
        purpose=ECONOMIC_HOLDOUT_PURPOSE,
        universe_hash="1" * 64,
        universe_name="oss3d2m-economic-holdout-universe-v1",
        source_dataset_set_hash="2" * 64,
        symbols=tuple(sorted(d2i_fixture.SYMBOLS)),
        quote_currency="USDT",
        timeframe_seconds=86400,
        partition_start=economic_start.isoformat(),
        partition_end=economic_end.isoformat(),
        bar_count=90,
        symbol_count=len(d2i_fixture.SYMBOLS),
        market_values_exposed=False,
        economic_outcomes_observed=False,
    )
    return D2MSource(
        d2l_source=source,
        binding=binding,
        d2l_receipt=d2l_receipt,
        shared_sqlite_path=shared,
        economic_holdout_commitment=commitment,
    )
