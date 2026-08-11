from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    if old not in text:
        raise SystemExit(f"{label} block not found in {path}")
    path.write_text(text.replace(old, new, 1))


bridge = Path("src/autotrade/health_bridge.py")
replace_once(
    bridge,
    '''        portfolio = (\n            self.get(portfolio_entity_id, HealthEntityKind.PORTFOLIO)\n            if portfolio_entity_id\n            else None\n        )\n\n        strategy_mode, strategy_multiplier, strategy_reason = self._effective_entity(\n''',
    '''        portfolio = (\n            self.get(portfolio_entity_id, HealthEntityKind.PORTFOLIO)\n            if portfolio_entity_id\n            else None\n        )\n\n        strategy_mode, strategy_multiplier, strategy_reason = self._effective_entity(\n''',
    "bridge portfolio lookup",
)
# Replace only the portfolio-control call so a required portfolio identity cannot
# disappear by passing an empty id.
replace_once(
    bridge,
    '''        portfolio_mode, portfolio_multiplier, portfolio_reason = self._effective_entity(\n            portfolio,\n            required=self._policy.require_portfolio_state and bool(portfolio_entity_id),\n            label="PORTFOLIO",\n            now=now,\n        )\n''',
    '''        if self._policy.require_portfolio_state and not portfolio_entity_id:\n            portfolio_mode = HealthRiskMode.NO_NEW_RISK\n            portfolio_multiplier = _ZERO\n            portfolio_reason = "MISSING_PORTFOLIO_HEALTH_ID"\n        else:\n            portfolio_mode, portfolio_multiplier, portfolio_reason = self._effective_entity(\n                portfolio,\n                required=self._policy.require_portfolio_state,\n                label="PORTFOLIO",\n                now=now,\n            )\n''',
    "bridge required portfolio identity",
)

safety = Path("src/autotrade/safety.py")
replace_once(
    safety,
    '''from .ledger import EventLedger, LedgerEvent\n''',
    '''from .health_bridge import HealthBridgeControlProvider, HealthBridgeError\nfrom .ledger import EventLedger, LedgerEvent\n''',
    "safety health bridge import",
)
replace_once(
    safety,
    '''        state_store: SafetyStateStore | None = None,\n    ) -> None:\n        self._limits = limits\n        self._ledger = ledger\n        self._state_store = state_store or InMemorySafetyStateStore()\n        self._lock = RLock()\n''',
    '''        state_store: SafetyStateStore | None = None,\n        health_bridge: HealthBridgeControlProvider | None = None,\n        portfolio_health_entity_id: str = "",\n    ) -> None:\n        if portfolio_health_entity_id and (\n            portfolio_health_entity_id != portfolio_health_entity_id.strip()\n            or not portfolio_health_entity_id\n        ):\n            raise ValueError("portfolio_health_entity_id must be canonical text")\n        if health_bridge is None and portfolio_health_entity_id:\n            raise ValueError("portfolio_health_entity_id requires health_bridge")\n        self._limits = limits\n        self._ledger = ledger\n        self._state_store = state_store or InMemorySafetyStateStore()\n        self._health_bridge = health_bridge\n        self._portfolio_health_entity_id = portfolio_health_entity_id\n        self._lock = RLock()\n''',
    "safety constructor",
)
replace_once(
    safety,
    '''        projected_strategy_gross = current_strategy_gross - abs(current_strategy_position) + abs(projected_strategy_position)\n        projected_gross = portfolio.gross_exposure - abs(current_position) + abs(projected_position)\n        projected_net = portfolio.net_exposure + signed_order_notional\n\n        if control_state.kill_switch_active and not risk_reducing:\n''',
    '''        projected_strategy_gross = current_strategy_gross - abs(current_strategy_position) + abs(projected_strategy_position)\n        projected_gross = portfolio.gross_exposure - abs(current_position) + abs(projected_position)\n        projected_net = portfolio.net_exposure + signed_order_notional\n\n        if self._health_bridge is not None:\n            try:\n                health_control = self._health_bridge.effective_control(\n                    strategy_id=intent.strategy_id,\n                    portfolio_entity_id=self._portfolio_health_entity_id,\n                    now=now,\n                )\n            except HealthBridgeError as exc:\n                return reject(\n                    "HEALTH_CONTROL_UNAVAILABLE",\n                    str(exc),\n                    risk_reducing=risk_reducing,\n                )\n            if health_control.blocks_new_risk and not risk_reducing:\n                return reject(\n                    "HEALTH_NO_NEW_RISK",\n                    health_control.reason,\n                    risk_reducing=risk_reducing,\n                )\n            if not risk_reducing:\n                health_order_limit = self._limits.max_order_notional * health_control.order_multiplier\n                if order_notional > health_order_limit:\n                    return reject(\n                        "HEALTH_MAX_ORDER_NOTIONAL",\n                        f"{order_notional}>{health_order_limit}:{health_control.reason}",\n                        risk_reducing=False,\n                    )\n                health_strategy_limit = (\n                    self._limits.max_strategy_gross_exposure * health_control.strategy_multiplier\n                )\n                if projected_strategy_gross > health_strategy_limit:\n                    return reject(\n                        "HEALTH_MAX_STRATEGY_GROSS",\n                        f"{projected_strategy_gross}>{health_strategy_limit}:{health_control.reason}",\n                        risk_reducing=False,\n                    )\n                health_portfolio_limit = (\n                    self._limits.max_portfolio_gross_exposure * health_control.portfolio_multiplier\n                )\n                if projected_gross > health_portfolio_limit:\n                    return reject(\n                        "HEALTH_MAX_PORTFOLIO_GROSS",\n                        f"{projected_gross}>{health_portfolio_limit}:{health_control.reason}",\n                        risk_reducing=False,\n                    )\n\n        if control_state.kill_switch_active and not risk_reducing:\n''',
    "safety health gate",
)

oms = Path("src/autotrade/oms.py")
replace_once(
    oms,
    '''from .execution_state import FillIntegrityConflict, FillStore, InMemoryFillStore, fill_fingerprint\n''',
    '''from .execution_state import FillIntegrityConflict, FillStore, InMemoryFillStore, fill_fingerprint\nfrom .health_bridge import HealthBridgeControlProvider, HealthBridgeError\n''',
    "oms health bridge import",
)
replace_once(
    oms,
    '''        safety_state_store: SafetyStateStore | None = None,\n        fill_store: FillStore | None = None,\n    ) -> None:\n        self._broker = broker\n        self._ledger = ledger\n        self._orders = order_store or InMemoryOrderStore()\n        self._safety_state_store = safety_state_store\n        self._fills = fill_store or InMemoryFillStore()\n''',
    '''        safety_state_store: SafetyStateStore | None = None,\n        fill_store: FillStore | None = None,\n        health_bridge: HealthBridgeControlProvider | None = None,\n        portfolio_health_entity_id: str = "",\n    ) -> None:\n        if portfolio_health_entity_id and (\n            portfolio_health_entity_id != portfolio_health_entity_id.strip()\n            or not portfolio_health_entity_id\n        ):\n            raise ValueError("portfolio_health_entity_id must be canonical text")\n        if health_bridge is None and portfolio_health_entity_id:\n            raise ValueError("portfolio_health_entity_id requires health_bridge")\n        self._broker = broker\n        self._ledger = ledger\n        self._orders = order_store or InMemoryOrderStore()\n        self._safety_state_store = safety_state_store\n        self._fills = fill_store or InMemoryFillStore()\n        self._health_bridge = health_bridge\n        self._portfolio_health_entity_id = portfolio_health_entity_id\n''',
    "oms constructor",
)
replace_once(
    oms,
    '''        if self._safety_state_store is not None:\n            current = self._safety_state_store.get()\n            if current.version != decision.safety_state_version:\n                raise OrderRejectedByControlPlane("safety state changed after risk approval")\n''',
    '''        if self._safety_state_store is not None:\n            current = self._safety_state_store.get()\n            if current.version != decision.safety_state_version:\n                raise OrderRejectedByControlPlane("safety state changed after risk approval")\n        if self._health_bridge is not None:\n            try:\n                health_control = self._health_bridge.effective_control(\n                    strategy_id=intent.strategy_id,\n                    portfolio_entity_id=self._portfolio_health_entity_id,\n                    now=now,\n                )\n            except HealthBridgeError as exc:\n                raise OrderRejectedByControlPlane(\n                    "health control unavailable after risk approval"\n                ) from exc\n            if health_control.blocks_new_risk and not decision.risk_reducing:\n                raise OrderRejectedByControlPlane(\n                    "health control blocks new risk after risk approval"\n                )\n''',
    "oms submit-time health gate",
)

bootstrap = Path("src/autotrade/bootstrap.py")
replace_once(
    bootstrap,
    '''from .execution_state import SQLiteFillAwarePortfolioStore, SQLiteFillStore\n''',
    '''from .execution_state import SQLiteFillAwarePortfolioStore, SQLiteFillStore\nfrom .health_bridge import HealthBridgePolicy, SQLiteHealthBridgeStore\n''',
    "bootstrap health bridge import",
)
replace_once(
    bootstrap,
    '''from .reconciliation import ReconciliationEngine, ReconciliationResult\n''',
    '''from .reconciliation import ReconciliationEngine, ReconciliationResult\nfrom .research.health import SQLiteHealthStateStore\n''',
    "bootstrap health store import",
)
replace_once(
    bootstrap,
    '''    safety: CapitalSafetyKernel\n    oms: OrderManagementSystem\n''',
    '''    safety: CapitalSafetyKernel\n    health_state_store: SQLiteHealthStateStore | None\n    health_bridge: SQLiteHealthBridgeStore | None\n    oms: OrderManagementSystem\n''',
    "bootstrap dataclass fields",
)
replace_once(
    bootstrap,
    '''    initial_portfolio: PortfolioSnapshot,\n    now: datetime,\n) -> DurablePaperCore:\n''',
    '''    initial_portfolio: PortfolioSnapshot,\n    now: datetime,\n    enable_health_bridge: bool = False,\n    health_bridge_policy: HealthBridgePolicy | None = None,\n    portfolio_health_entity_id: str = "",\n) -> DurablePaperCore:\n''',
    "bootstrap function signature",
)
replace_once(
    bootstrap,
    '''    safety_state_store = SQLiteR2SafetyStateStore(runtime)\n    risk_telemetry = SQLiteRiskTelemetryStore(\n''',
    '''    safety_state_store = SQLiteR2SafetyStateStore(runtime)\n    health_state_store: SQLiteHealthStateStore | None = None\n    health_bridge: SQLiteHealthBridgeStore | None = None\n    if enable_health_bridge:\n        health_state_store = SQLiteHealthStateStore(runtime.path)\n        health_bridge = SQLiteHealthBridgeStore(\n            runtime,\n            health_reader=health_state_store,\n            policy=health_bridge_policy,\n        )\n    elif portfolio_health_entity_id:\n        raise ValueError("portfolio_health_entity_id requires enable_health_bridge=True")\n    risk_telemetry = SQLiteRiskTelemetryStore(\n''',
    "bootstrap bridge construction",
)
replace_once(
    bootstrap,
    '''    safety = CapitalSafetyKernel(limits, ledger, state_store=safety_state_store)\n    oms = OrderManagementSystem(\n        broker=broker,\n        ledger=ledger,\n        order_store=order_store,\n        safety_state_store=safety_state_store,\n        fill_store=fill_store,\n    )\n''',
    '''    safety = CapitalSafetyKernel(\n        limits,\n        ledger,\n        state_store=safety_state_store,\n        health_bridge=health_bridge,\n        portfolio_health_entity_id=portfolio_health_entity_id,\n    )\n    oms = OrderManagementSystem(\n        broker=broker,\n        ledger=ledger,\n        order_store=order_store,\n        safety_state_store=safety_state_store,\n        fill_store=fill_store,\n        health_bridge=health_bridge,\n        portfolio_health_entity_id=portfolio_health_entity_id,\n    )\n''',
    "bootstrap safety/oms wiring",
)
replace_once(
    bootstrap,
    '''        safety=safety,\n        oms=oms,\n''',
    '''        safety=safety,\n        health_state_store=health_state_store,\n        health_bridge=health_bridge,\n        oms=oms,\n''',
    "bootstrap return fields",
)
