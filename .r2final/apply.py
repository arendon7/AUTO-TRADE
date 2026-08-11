from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]

# Reviewed full replacements.
shutil.copy2(ROOT / ".r2final" / "reconciliation.py", ROOT / "src" / "autotrade" / "reconciliation.py")
shutil.copy2(ROOT / ".r2final" / "test_r2_risk_matrix.py", ROOT / "tests" / "test_r2_risk_matrix.py")
shutil.copy2(ROOT / ".r2final" / "test_r2_replace_evidence.py", ROOT / "tests" / "test_r2_replace_evidence.py")
shutil.copy2(ROOT / ".r2final" / "test_r2_partial_cancel.py", ROOT / "tests" / "test_r2_partial_cancel.py")

# Safety: exact aggregate + strategy consistency.
safety_path = ROOT / "src" / "autotrade" / "safety.py"
safety = safety_path.read_text(encoding="utf-8")
if "gross_exposure does not match position map" not in safety:
    marker = "def _validate_portfolio(portfolio: PortfolioSnapshot) -> str | None:\n"
    if marker not in safety:
        raise SystemExit("safety portfolio validation marker not found")
    prefix = safety.split(marker, 1)[0]
    safety = prefix + '''def _validate_portfolio(portfolio: PortfolioSnapshot) -> str | None:
    numeric = {
        "equity": portfolio.equity,
        "gross_exposure": portfolio.gross_exposure,
        "net_exposure": portfolio.net_exposure,
        "daily_pnl": portfolio.daily_pnl,
        "drawdown": portfolio.drawdown,
    }
    for name, value in numeric.items():
        if not _finite(value):
            return f"{name} is not finite"
    if portfolio.equity <= 0:
        return "equity must be > 0"
    if portfolio.gross_exposure < 0:
        return "gross_exposure cannot be negative"
    if portfolio.drawdown < 0:
        return "drawdown cannot be negative"
    if portfolio.open_orders < 0:
        return "open_orders cannot be negative"

    zero = Decimal("0")
    aggregate_positions = dict(portfolio.signed_position_notional_by_symbol)
    for symbol, value in aggregate_positions.items():
        if not symbol.strip():
            return "position symbol is empty"
        if not _finite(value):
            return f"position {symbol} is not finite"
    calculated_gross = sum((abs(value) for value in aggregate_positions.values()), start=zero)
    calculated_net = sum(aggregate_positions.values(), start=zero)
    if calculated_gross != portfolio.gross_exposure:
        return (
            "gross_exposure does not match position map: "
            f"declared={portfolio.gross_exposure},calculated={calculated_gross}"
        )
    if calculated_net != portfolio.net_exposure:
        return (
            "net_exposure does not match position map: "
            f"declared={portfolio.net_exposure},calculated={calculated_net}"
        )

    strategy_positions = portfolio.strategy_signed_position_notional_by_symbol
    for strategy, values in strategy_positions.items():
        if not strategy.strip():
            return "strategy id is empty"
        calculated = zero
        for symbol, value in values.items():
            if not symbol.strip():
                return f"strategy {strategy} contains empty symbol"
            if not _finite(value):
                return f"strategy {strategy}/{symbol} position is not finite"
            calculated += abs(value)
        declared = portfolio.strategy_gross_exposure.get(strategy)
        if declared is None:
            return f"strategy {strategy} is missing gross exposure"
        if not _finite(declared) or declared < 0:
            return f"strategy {strategy} gross exposure is invalid"
        if declared != calculated:
            return (
                f"strategy {strategy} gross exposure mismatch: "
                f"declared={declared},calculated={calculated}"
            )

    for strategy, declared in portfolio.strategy_gross_exposure.items():
        if not _finite(declared) or declared < 0:
            return f"strategy {strategy} gross exposure is invalid"
        if strategy not in strategy_positions and declared != 0:
            return f"strategy {strategy} gross exposure has no position map"
    return None
'''
    safety_path.write_text(safety, encoding="utf-8")

# SQLite portfolio projection: immutable fill fingerprint.
execution_path = ROOT / "src" / "autotrade" / "execution_state.py"
execution = execution_path.read_text(encoding="utf-8")
if "INSERT INTO portfolio_applied_fills(fill_id, order_id, fill_hash, applied_at)" not in execution:
    execution = execution.replace(
        '''CREATE TABLE IF NOT EXISTS portfolio_applied_fills (
                    fill_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );''',
        '''CREATE TABLE IF NOT EXISTS portfolio_applied_fills (
                    fill_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    fill_hash TEXT,
                    applied_at TEXT NOT NULL
                );''',
        1,
    )
    init_tail = '''                """
            )
        finally:
            conn.close()

    def apply_fills(
'''
    if 'PRAGMA table_info(portfolio_applied_fills)' not in execution:
        replacement_tail = '''                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(portfolio_applied_fills)").fetchall()
            }
            if "fill_hash" not in columns:
                conn.execute("ALTER TABLE portfolio_applied_fills ADD COLUMN fill_hash TEXT")
        finally:
            conn.close()

    def apply_fills(
'''
        class_at = execution.index("class SQLiteFillAwarePortfolioStore")
        before, after = execution[:class_at], execution[class_at:]
        if init_tail not in after:
            raise SystemExit("fill-aware store init marker not found")
        execution = before + after.replace(init_tail, replacement_tail, 1)
    old = '''                already = conn.execute(
                    "SELECT 1 FROM portfolio_applied_fills WHERE fill_id = ?",
                    (fill.fill_id,),
                ).fetchone()
                if already is not None:
                    continue
                snapshot = apply_single_fill_to_portfolio(snapshot=snapshot, order=order, fill=fill)
                conn.execute(
                    """
                    INSERT INTO portfolio_applied_fills(fill_id, order_id, applied_at)
                    VALUES (?, ?, ?)
                    """,
                    (fill.fill_id, order.order_id, now.isoformat()),
                )
'''
    new = '''                fingerprint = fill_fingerprint(fill)
                already = conn.execute(
                    "SELECT fill_hash FROM portfolio_applied_fills WHERE fill_id = ?",
                    (fill.fill_id,),
                ).fetchone()
                if already is not None:
                    if already["fill_hash"] != fingerprint:
                        raise FillIntegrityConflict(fill.fill_id)
                    continue
                snapshot = apply_single_fill_to_portfolio(snapshot=snapshot, order=order, fill=fill)
                conn.execute(
                    """
                    INSERT INTO portfolio_applied_fills(fill_id, order_id, fill_hash, applied_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (fill.fill_id, order.order_id, fingerprint, now.isoformat()),
                )
'''
    if old not in execution:
        raise SystemExit("portfolio projection apply marker not found")
    execution = execution.replace(old, new, 1)
    execution_path.write_text(execution, encoding="utf-8")

# In-memory projection: same fill_id must preserve immutable content.
state_path = ROOT / "src" / "autotrade" / "state.py"
state = state_path.read_text(encoding="utf-8")
if "_applied_fill_identities" not in state:
    if "self._applied_fill_ids: set[str] = set()" not in state:
        raise SystemExit("in-memory applied-fill marker not found")
    state = state.replace(
        "self._applied_fill_ids: set[str] = set()",
        "self._applied_fill_identities: dict[str, tuple[str, str, str, str, str, str]] = {}",
        1,
    )
    old = '''                if fill.fill_id in batch_seen or fill.fill_id in self._applied_fill_ids:
                    continue
                batch_seen.add(fill.fill_id)
                incremental = replace(
                    order,
                    filled_quantity=fill.quantity,
                    average_fill_price=fill.price,
                )
                snapshot = apply_fill_to_portfolio(snapshot, incremental)
                self._applied_fill_ids.add(fill.fill_id)
                self._orders_with_fill_events.add(order.order_id)
'''
    new = '''                identity = _fill_identity(fill)
                existing_identity = self._applied_fill_identities.get(fill.fill_id)
                if existing_identity is not None:
                    if existing_identity != identity:
                        raise ValueError("conflicting applied fill identity")
                    continue
                if fill.fill_id in batch_seen:
                    continue
                batch_seen.add(fill.fill_id)
                incremental = replace(
                    order,
                    filled_quantity=fill.quantity,
                    average_fill_price=fill.price,
                )
                snapshot = apply_fill_to_portfolio(snapshot, incremental)
                self._applied_fill_identities[fill.fill_id] = identity
                self._orders_with_fill_events.add(order.order_id)
'''
    if old not in state:
        raise SystemExit("in-memory fill apply marker not found")
    state = state.replace(old, new, 1)
    marker = "\ndef _validate_fill_for_order(*, fill: Fill, order: OrderRecord) -> None:\n"
    if marker not in state:
        raise SystemExit("fill validation marker not found")
    state = state.replace(
        marker,
        '''
def _fill_identity(fill: Fill) -> tuple[str, str, str, str, str, str]:
    return (
        fill.order_id,
        fill.symbol,
        fill.side.value,
        str(fill.quantity),
        str(fill.price),
        fill.occurred_at.isoformat(),
    )


def _validate_fill_for_order(*, fill: Fill, order: OrderRecord) -> None:
''',
        1,
    )
    state_path.write_text(state, encoding="utf-8")

# OMS: terminal replay repairs evidence; replace request is durable/idempotent.
oms_path = ROOT / "src" / "autotrade" / "oms.py"
oms = oms_path.read_text(encoding="utf-8")
old_terminal = '''        if order.status.terminal:
            self._validate_terminal_replay(order=order, execution=execution)
            return order
'''
if old_terminal in oms:
    oms = oms.replace(
        old_terminal,
        '''        if order.status.terminal:
            self._validate_terminal_replay(order=order, execution=execution)
            self._record_execution(
                final=order,
                all_fills=execution.fills,
                now=now,
                recovered=recovered,
                changed=False,
            )
            return order
''',
        1,
    )
if "        if not changed:\n            return\n        snapshot_key = json.dumps(" in oms:
    oms = oms.replace(
        "        if not changed:\n            return\n        snapshot_key = json.dumps(",
        "        snapshot_key = json.dumps(",
        1,
    )
if "def mark_replace_pending(" not in oms:
    marker = "    def fills_for_order(self, order_id: str) -> tuple[Fill, ...]:\n"
    if marker not in oms:
        raise SystemExit("OMS fills_for_order marker not found")
    methods = '''    def mark_replace_pending(
        self,
        *,
        order_id: str,
        replacement_intent_id: str,
        now: datetime,
    ) -> OrderRecord:
        if not replacement_intent_id.strip():
            raise ValueError("replacement_intent_id is required")
        order = self._orders.get_by_order_id(order_id)
        if order is None:
            raise KeyError(order_id)
        if order.status not in {
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.REPLACE_PENDING,
        }:
            raise BrokerStateConflict(
                f"order {order_id} cannot enter replace from {order.status.value}"
            )
        self._append_idempotent(
            LedgerEvent(
                event_id=f"replace-requested:{order.order_id}",
                event_type="ORDER_REPLACE_REQUESTED",
                occurred_at=now,
                payload={
                    "order_id": order.order_id,
                    "replacement_intent_id": replacement_intent_id,
                },
            )
        )
        if order.status is OrderStatus.REPLACE_PENDING:
            return order
        pending = replace(order, status=OrderStatus.REPLACE_PENDING)
        self._orders.update(pending)
        return pending

    def replacement_request_matches(
        self, *, order_id: str, replacement_intent_id: str
    ) -> bool:
        event_id = f"replace-requested:{order_id}"
        for event in self._ledger.all_events():
            if event.event_id != event_id:
                continue
            if event.event_type != "ORDER_REPLACE_REQUESTED":
                raise BrokerStateConflict(
                    f"replace event identity has wrong type: {event.event_type}"
                )
            existing = event.payload.get("replacement_intent_id", "")
            if existing != replacement_intent_id:
                raise BrokerStateConflict(
                    f"replacement intent conflict for order {order_id}"
                )
            return True
        return False

'''
    oms = oms.replace(marker, methods + marker, 1)
oms_path.write_text(oms, encoding="utf-8")

# Engine: retry-safe cancel-first replacement.
engine_path = ROOT / "src" / "autotrade" / "engine.py"
engine = engine_path.read_text(encoding="utf-8")
start = engine.index("    def replace_order(\n")
end = engine.index("    def _with_risk_telemetry", start)
replacement = '''    def replace_order(
        self,
        *,
        order_id: str,
        replacement_intent: OrderIntent,
        market: MarketSnapshot,
        now: datetime,
    ) -> ReplacePipelineResult:
        """Authoritative cancel followed by a completely fresh guarded submit.

        The replace request is durably evidenced before cancellation. Retries
        after a crash are safe: REPLACE_PENDING resumes cancellation; CANCELLED
        resumes the fresh replacement submit only when the durable marker binds
        the same replacement intent.
        """
        original = self._oms.get_by_order_id(order_id)
        if original is None:
            raise KeyError(order_id)
        if replacement_intent.idempotency_key == original.intent.idempotency_key:
            raise ReplacementAborted("replacement requires a new idempotency key")
        if replacement_intent.intent_id == original.intent.intent_id:
            raise ReplacementAborted("replacement requires a new intent_id")
        if (
            replacement_intent.symbol != original.intent.symbol
            or replacement_intent.side is not original.intent.side
            or replacement_intent.strategy_id != original.intent.strategy_id
        ):
            raise ReplacementAborted(
                "replacement must preserve symbol, side and strategy identity"
            )

        if original.status.terminal:
            if (
                original.status is OrderStatus.CANCELLED
                and self._oms.replacement_request_matches(
                    order_id=order_id,
                    replacement_intent_id=replacement_intent.intent_id,
                )
            ):
                new_result = self.process_intent(
                    intent=replacement_intent,
                    market=market,
                    now=now,
                )
                return ReplacePipelineResult(
                    original_order=original,
                    replacement=new_result,
                    aborted_reason=(
                        new_result.decision.reason_code
                        if new_result.order is None and new_result.decision is not None
                        else ""
                    ),
                )
            raise ReplacementAborted(
                f"original order already terminal: {original.status.value}"
            )

        self._oms.mark_replace_pending(
            order_id=order_id,
            replacement_intent_id=replacement_intent.intent_id,
            now=now,
        )
        cancelled = self.cancel_order(order_id=order_id, now=now)
        if cancelled.status is not OrderStatus.CANCELLED:
            raise ReplacementAborted(
                f"replacement aborted because original resolved as {cancelled.status.value}"
            )

        new_result = self.process_intent(
            intent=replacement_intent,
            market=market,
            now=now,
        )
        return ReplacePipelineResult(
            original_order=cancelled,
            replacement=new_result,
            aborted_reason=(
                new_result.decision.reason_code
                if new_result.order is None and new_result.decision is not None
                else ""
            ),
        )

'''
engine = engine[:start] + replacement + engine[end:]
engine_path.write_text(engine, encoding="utf-8")

# Remove all temporary patch machinery from the final tree.
shutil.rmtree(ROOT / ".r2patch", ignore_errors=True)
shutil.rmtree(ROOT / ".r2final", ignore_errors=True)
for transient in (
    ROOT / ".github" / "workflows" / "r2-one-shot-patch.yml",
    ROOT / ".github" / "workflows" / "r2-finalize-patch.yml",
):
    if transient.exists():
        transient.unlink()

print("R2 final staged patch applied")
