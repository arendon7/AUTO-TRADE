from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BROKERS = ROOT / "src/autotrade/brokers"
WRITER = BROKERS / "alpaca_paper_crypto_writer.py"
PRE_IO = BROKERS / "alpaca_paper_crypto_pre_io.py"
RECONCILIATION = BROKERS / "alpaca_paper_crypto_reconciliation.py"
UNKNOWN_RECOVERY = BROKERS / "alpaca_paper_crypto_unknown_recovery.py"
ORDER = BROKERS / "alpaca_paper_crypto_order.py"
LIFECYCLE = BROKERS / "alpaca_paper_crypto_lifecycle.py"
CRYPTO_MODULES = tuple(sorted(BROKERS.glob("alpaca_paper_crypto_*.py")))

FORBIDDEN_CROSS_PRODUCT = (
    "alpaca_paper_bracket",
    "alpaca_paper_writer",
    "alpaca_paper_final_guard",
    "connectivity_final_freshness",
    "connectivity_workspace_post",
)
NETWORK_IMPORT_ROOTS = {"http", "urllib", "socket", "requests", "httpx"}
GUARDED_CAPABILITY = "GuardedAlpacaPaperCryptoWriteTransport"
EXPECTED_GUARDED_SUBCLASSES = {
    (PRE_IO.name, "FinalGuardedCryptoEntryTransport"),
    (PRE_IO.name, "FinalGuardedCryptoProtectionTransport"),
}


def main() -> int:
    errors: list[str] = []
    for path in (WRITER, PRE_IO, RECONCILIATION, UNKNOWN_RECOVERY, ORDER, LIFECYCLE):
        if not path.is_file():
            errors.append(f"missing crypto execution contract file: {path.relative_to(ROOT)}")

    if WRITER.is_file():
        text = WRITER.read_text(encoding="utf-8")
        required = {
            'CRYPTO_ORDERS_PATH = "/v2/orders"': "exact orders path is missing",
            "enabled: bool = False": "crypto writer must be disabled by default",
            "host: str = ALPACA_PAPER_TRADING_HOST": "writer host is not bound to PAPER constant",
            'if self.host != ALPACA_PAPER_TRADING_HOST': "writer exact PAPER host self-check is missing",
            "http.client.HTTPSConnection(host": "writer TLS transport is missing",
            'connection.request("POST", path': "writer exact POST transport is missing",
            "class GuardedAlpacaPaperCryptoWriteTransport:": "nominal guarded transport capability is missing",
            "if not isinstance(self._transport, GuardedAlpacaPaperCryptoWriteTransport):": "enabled writer nominal Final-Guard gate is missing",
            'getattr(self._transport, "role", None) is not order.role': "enabled writer role-binding gate is missing",
            "lifecycle.mark_entry_submission_unknown": "entry UNKNOWN-before-I/O transition is missing",
            "lifecycle.mark_protection_submission_unknown": "protection UNKNOWN-before-I/O transition is missing",
            "self._transport.post(": "one-shot transport call is missing",
            'raise CryptoLifecycleBlocked("entry POST requires durable ENTRY_PREPARED")': "entry state gate is missing",
            'raise CryptoLifecycleBlocked("protection POST requires durable PROTECTION_PREPARED")': "protection state gate is missing",
            "reconcile by durable client_order_id": "ambiguous ACK reconciliation instruction is missing",
        }
        for needle, reason in required.items():
            if needle not in text:
                errors.append(f"writer: {reason}")
        if "api.alpaca.markets" in text:
            errors.append("writer contains LIVE Alpaca host literal")
        if "order_class" in text:
            errors.append("crypto writer may not expose equity order_class semantics")
        if text.find("lifecycle.mark_entry_submission_unknown") > text.find("self._transport.post("):
            errors.append("entry UNKNOWN transition does not precede broker I/O in source authority flow")
        if text.find("lifecycle.mark_protection_submission_unknown") > text.find("self._transport.post("):
            errors.append("protection UNKNOWN transition does not precede broker I/O in source authority flow")

    if PRE_IO.is_file():
        text = PRE_IO.read_text(encoding="utf-8")
        required = {
            "class FinalGuardedCryptoEntryTransport(GuardedAlpacaPaperCryptoWriteTransport):": "ENTRY transport is not nominally guarded",
            "role = CryptoOrderRole.ENTRY": "ENTRY guarded transport role is missing",
            "class FinalGuardedCryptoProtectionTransport(GuardedAlpacaPaperCryptoWriteTransport):": "PROTECTION transport is not nominally guarded",
            "role = CryptoOrderRole.PROTECTION": "PROTECTION guarded transport role is missing",
            "CryptoFinalWritePhase.PRE_IO": "ENTRY PRE_IO evidence gate is missing",
            "CryptoProtectionFinalWritePhase.PRE_IO": "PROTECTION PRE_IO evidence gate is missing",
            "CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN": "ENTRY durable UNKNOWN evidence gate is missing",
            "CryptoLifecycleStatus.PROTECTION_SUBMISSION_UNKNOWN": "PROTECTION durable UNKNOWN evidence gate is missing",
        }
        for needle, reason in required.items():
            if needle not in text:
                errors.append(f"pre_io: {reason}")

    if RECONCILIATION.is_file():
        text = RECONCILIATION.read_text(encoding="utf-8")
        required = {
            'ORDER_BY_CLIENT_PATH = "/v2/orders:by_client_order_id"': "client-order reconciliation endpoint is missing",
            'POSITION_PATH_PREFIX = "/v2/positions/"': "position reconciliation endpoint is missing",
            "class CryptoBrokerOrderAbsenceEvidence:": "tamper-evident order-404 absence type is missing",
            "class CryptoBrokerUnknownReconciliation:": "discriminated UNKNOWN reconciliation type is missing",
            "client_order_id=": "durable client_order_id lookup binding is missing",
            "UrllibAlpacaPaperReadTransport": "reconciliation must use certified read transport",
            'method="GET"': "reconciliation GET-only request is missing",
            "credentials.credential_reference": "reconciliation evidence is not credential-bound",
            "retry remains forbidden": "order-404 result does not explicitly preserve no-retry contract",
            "confirmed_net_long_quantity=reconciliation.position.quantity": "position truth is not applied to normal lifecycle reconciliation",
        }
        for needle, reason in required.items():
            if needle not in text:
                errors.append(f"reconciliation: {reason}")
        order_404 = text.find("if order_response.status_code == 404:")
        position_read = text.find("position_response = position_transport.read(")
        absence_return = text.find("return CryptoBrokerUnknownReconciliation(")
        if not 0 <= order_404 < position_read < absence_return:
            errors.append("reconciliation: exact order 404 must continue to position GET before returning UNKNOWN evidence")
        for forbidden in ("http.client", ".post(", 'method="POST"'):
            if forbidden in text:
                errors.append(f"reconciliation contains forbidden write marker: {forbidden}")

    if UNKNOWN_RECOVERY.is_file():
        text = UNKNOWN_RECOVERY.read_text(encoding="utf-8")
        required = {
            "class CryptoPaperUnknownRecoveryCoordinator:": "UNKNOWN recovery coordinator is missing",
            "CryptoExecutionAttemptCheckpoint": "ENTRY durable checkpoint binding is missing",
            "CryptoProtectionExecutionAttemptCheckpoint": "PROTECTION durable checkpoint binding is missing",
            "CryptoBrokerUnknownReconciliation": "order-404 evidence binding is missing",
            "fresh_account: AlpacaPaperAccountAttestation": "fresh same-account evidence is missing",
            "PaperFlatAccountAttestation": "all-account flatness evidence is missing",
            "if position.quantity > 0:": "remaining-long fail-closed branch is missing",
            "CryptoLifecycleStatus.HALTED_RECONCILIATION_REQUIRED": "remaining-long HALT state is missing",
            "clean_for_first_canary": "zero-position branch does not require complete account flatness",
            "CryptoLifecycleStatus.FLAT_RECONCILED": "strong-flat terminal state is missing",
            '"retry_authorized": False': "UNKNOWN recovery does not permanently deny retry",
            "attempt_count != 1": "UNKNOWN recovery does not preserve one-shot attempt count",
            "lifecycle._mutate(": "UNKNOWN recovery is not persisted through lifecycle event chain",
            '"order_absence_fingerprint"': "order-absence evidence is not persisted in event payload",
            '"position_fingerprint"': "position evidence is not persisted in event payload",
            '"fresh_account_fingerprint"': "account evidence is not persisted in event payload",
            '"flat_account_fingerprint"': "flat-account evidence is not persisted in event payload",
        }
        for needle, reason in required.items():
            if needle not in text:
                errors.append(f"unknown recovery: {reason}")
        for forbidden in (
            "http.client",
            "urllib",
            "socket",
            "requests",
            "httpx",
            ".post(",
            "submit_once(",
            "stage_external_submission(",
            "mark_entry_submission_unknown(",
            "mark_protection_submission_unknown(",
        ):
            if forbidden in text:
                errors.append(f"unknown recovery contains forbidden new-write/network marker: {forbidden}")

    guarded_subclasses: set[tuple[str, str]] = set()
    for path in CRYPTO_MODULES:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        imports = _imports(tree)
        for module in imports:
            if any(fragment in module for fragment in FORBIDDEN_CROSS_PRODUCT):
                errors.append(f"{path.name}: imports forbidden equity/write authority {module}")
        network_roots = {module.split(".", 1)[0] for module in imports if module}
        if path != WRITER and network_roots & NETWORK_IMPORT_ROOTS:
            errors.append(
                f"{path.name}: only dedicated crypto writer may import direct network stack; found {sorted(network_roots & NETWORK_IMPORT_ROOTS)}"
            )
        if path not in {LIFECYCLE, UNKNOWN_RECOVERY} and "._mutate(" in source:
            errors.append(f"{path.name}: direct lifecycle transaction primitive is reserved for lifecycle/UNKNOWN recovery")
        for class_name in _subclasses_of(tree, GUARDED_CAPABILITY):
            guarded_subclasses.add((path.name, class_name))

    if guarded_subclasses != EXPECTED_GUARDED_SUBCLASSES:
        missing = sorted(EXPECTED_GUARDED_SUBCLASSES - guarded_subclasses)
        extra = sorted(guarded_subclasses - EXPECTED_GUARDED_SUBCLASSES)
        if missing:
            errors.append(f"missing sanctioned guarded crypto transports: {missing}")
        if extra:
            errors.append(f"unauthorized production guarded crypto transport subclasses: {extra}")

    if ORDER.is_file():
        text = ORDER.read_text(encoding="utf-8")
        for forbidden in ("http.client", "urllib", "socket", "requests", "httpx", ".post("):
            if forbidden in text:
                errors.append(f"order contract contains forbidden network marker: {forbidden}")
    if LIFECYCLE.is_file():
        text = LIFECYCLE.read_text(encoding="utf-8")
        for forbidden in ("http.client", "urllib", "socket", "requests", "httpx", ".post("):
            if forbidden in text:
                errors.append(f"lifecycle contains forbidden network marker: {forbidden}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 crypto execution authority boundary: PASS "
        "(disabled-by-default PAPER writer; nominal role-bound ENTRY/PROTECTION Final Guards; "
        "UNKNOWN-before-I/O; exact /v2/orders POST; GET-only client-id + position reconciliation; "
        "order-404 always continues to position truth and can only HALT or strongly FLAT-reconcile; "
        "zero blind retry; no equity bracket/LIVE cross-authority)"
    )
    return 0


def _imports(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def _base_name(base: ast.expr) -> str:
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return ""


def _subclasses_of(tree: ast.AST, base_name: str) -> set[str]:
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and any(_base_name(base) == base_name for base in node.bases)
    }


if __name__ == "__main__":
    raise SystemExit(main())
