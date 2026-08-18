from __future__ import annotations

import ast
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src/autotrade/brokers"
WRITER = SRC / "alpaca_paper_crypto_writer.py"
PRE_IO = SRC / "alpaca_paper_crypto_pre_io.py"
COLD_START_PRE_IO = SRC / "alpaca_paper_crypto_cold_start_pre_io.py"
LIFECYCLE = SRC / "alpaca_paper_crypto_lifecycle.py"
RECOVERY = SRC / "alpaca_paper_crypto_unknown_recovery.py"
RECONCILIATION = SRC / "alpaca_paper_crypto_reconciliation.py"
PROTECTION_ATTEMPT = SRC / "alpaca_paper_crypto_protection_execution_attempt.py"
WORKFLOW = ROOT / ".github/workflows/r6-crypto-execution.yml"
COLD_START_WORKFLOW = ROOT / ".github/workflows/r6-crypto-cold-start-execution-authority.yml"
TEST = ROOT / "tests/test_r6_paper_crypto_writer.py"
PROTECTION_TEST = ROOT / "tests/test_r6_paper_crypto_protection_pre_io.py"
UNKNOWN_RECOVERY_TEST = ROOT / "tests/test_r6_paper_crypto_unknown_recovery.py"
UNKNOWN_RECOVERY_ADVERSARIAL_TEST = ROOT / "tests/test_r6_paper_crypto_unknown_recovery_adversarial.py"
COLD_START_TRANSPORT_TEST = ROOT / "tests/test_r6_paper_crypto_cold_start_pre_io_transport.py"
WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
ALLOWED_WRITER = "alpaca_paper_crypto_writer.py"
EXPECTED_GUARDED_SUBCLASSES = {
    "FinalGuardedCryptoEntryTransport",
    "FinalGuardedCryptoProtectionTransport",
    "ColdStartFinalGuardedCryptoEntryTransport",
}
NETWORK_IMPORT_ROOTS = {
    "aiohttp",
    "http.client",
    "httpx",
    "requests",
    "socket",
    "ssl",
    "urllib.request",
    "urllib3",
}


class BoundaryError(RuntimeError):
    pass


def _text(path: Path) -> str:
    if not path.is_file():
        raise BoundaryError(f"required file missing: {path}")
    return path.read_text(encoding="utf-8")


def _imports(source: str) -> set[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _network_imports(source: str) -> set[str]:
    imports = _imports(source)
    bad: set[str] = set()
    for name in imports:
        for root in NETWORK_IMPORT_ROOTS:
            if name == root or name.startswith(f"{root}."):
                bad.add(name)
    return bad


def _base_name(base: ast.expr) -> str:
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return ""


def _guarded_subclasses(source: str) -> set[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if any(_base_name(base) == "GuardedAlpacaPaperCryptoWriteTransport" for base in node.bases):
            found.add(node.name)
    return found


def _writer_has_nominal_gate(source: str) -> bool:
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "AlpacaPaperCryptoWriter":
            continue
        for item in node.body:
            if not isinstance(item, ast.FunctionDef) or item.name != "submit_once":
                continue
            first_mark_unknown: int | None = None
            first_transport_post: int | None = None
            first_guarded_gate: int | None = None
            first_role_gate: int | None = None
            for child in ast.walk(item):
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "mark_entry_submission_unknown"
                ):
                    first_mark_unknown = child.lineno if first_mark_unknown is None else min(first_mark_unknown, child.lineno)
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "post"
                    and isinstance(child.func.value, ast.Attribute)
                    and child.func.value.attr == "_transport"
                ):
                    first_transport_post = child.lineno if first_transport_post is None else min(first_transport_post, child.lineno)
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == "isinstance":
                    if len(child.args) == 2 and isinstance(child.args[0], ast.Attribute) and child.args[0].attr == "_transport":
                        second = child.args[1]
                        if isinstance(second, ast.Name) and second.id == "GuardedAlpacaPaperCryptoWriteTransport":
                            first_guarded_gate = child.lineno if first_guarded_gate is None else min(first_guarded_gate, child.lineno)
                if (
                    isinstance(child, ast.Compare)
                    and isinstance(child.left, ast.Attribute)
                    and child.left.attr == "role"
                    and any(isinstance(comp, ast.Attribute) and comp.attr == "role" for comp in child.comparators)
                ):
                    first_role_gate = child.lineno if first_role_gate is None else min(first_role_gate, child.lineno)
            return bool(
                first_guarded_gate is not None
                and first_role_gate is not None
                and first_mark_unknown is not None
                and first_transport_post is not None
                and first_guarded_gate < first_mark_unknown
                and first_role_gate < first_mark_unknown
                and first_mark_unknown < first_transport_post
            )
    return False


def _cold_start_transport_contract(source: str) -> list[str]:
    errors: list[str] = []
    required = (
        "class ColdStartFinalGuardedCryptoEntryTransport(GuardedAlpacaPaperCryptoWriteTransport):",
        "role = CryptoOrderRole.ENTRY",
        "CryptoColdStartPreIoAuthority",
        "CryptoColdStartPreIoExecutionContext",
        "request_payload != expected_payload",
        "credentials = _ephemeral_credentials(headers)",
        "CryptoColdStartFinalWritePhase.PRE_IO",
        "CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN",
        "attestation.entry_attempt_count != 1",
        "attestation.client_order_id != self._context.broker_order.client_order_id",
        "attestation.package_hash != self._context.package.package_hash",
        "attestation.credential_reference != credentials.credential_reference",
        "self._last_attestation = attestation",
        "response = self._delegate.post(",
        "return datetime.now(timezone.utc)",
    )
    for token in required:
        if token not in source:
            errors.append(f"cold-start guarded transport contract missing: {token}")
    class_start = source.find("class ColdStartFinalGuardedCryptoEntryTransport")
    property_start = source.find("    @property\n    def last_attestation", class_start)
    constructor_surface = source[class_start:property_start] if class_start >= 0 and property_start >= 0 else ""
    if "clock" in constructor_surface:
        errors.append("cold-start transport constructor may not accept injectable clock")
    latch = source.find("self._last_attestation = attestation")
    delegate = source.find("response = self._delegate.post(")
    if latch < 0 or delegate < 0 or latch > delegate:
        errors.append("cold-start PRE_IO evidence must latch before delegate I/O")
    return errors


def main() -> int:
    errors: list[str] = []
    required_files = (
        (WRITER, "crypto PAPER writer missing"),
        (PRE_IO, "crypto PRE_IO interlock missing"),
        (COLD_START_PRE_IO, "crypto cold-start PRE_IO interlock missing"),
        (LIFECYCLE, "crypto lifecycle missing"),
        (RECOVERY, "crypto UNKNOWN recovery missing"),
        (RECONCILIATION, "crypto reconciliation missing"),
        (PROTECTION_ATTEMPT, "crypto protection attempt checkpoint missing"),
        (TEST, "crypto writer tests missing"),
        (PROTECTION_TEST, "crypto protection PRE_IO tests missing"),
        (UNKNOWN_RECOVERY_TEST, "crypto UNKNOWN recovery tests missing"),
        (UNKNOWN_RECOVERY_ADVERSARIAL_TEST, "crypto UNKNOWN recovery adversarial tests missing"),
        (COLD_START_TRANSPORT_TEST, "crypto cold-start PRE_IO transport tests missing"),
    )
    for path, message in required_files:
        if not path.is_file():
            errors.append(message)

    writer = _text(WRITER) if WRITER.is_file() else ""
    pre_io = _text(PRE_IO) if PRE_IO.is_file() else ""
    cold_start_pre_io = _text(COLD_START_PRE_IO) if COLD_START_PRE_IO.is_file() else ""
    lifecycle = _text(LIFECYCLE) if LIFECYCLE.is_file() else ""
    recovery = _text(RECOVERY) if RECOVERY.is_file() else ""
    reconciliation = _text(RECONCILIATION) if RECONCILIATION.is_file() else ""
    protection_attempt = _text(PROTECTION_ATTEMPT) if PROTECTION_ATTEMPT.is_file() else ""
    test = _text(TEST) if TEST.is_file() else ""
    protection_test = _text(PROTECTION_TEST) if PROTECTION_TEST.is_file() else ""
    unknown_test = _text(UNKNOWN_RECOVERY_TEST) if UNKNOWN_RECOVERY_TEST.is_file() else ""
    unknown_adv = _text(UNKNOWN_RECOVERY_ADVERSARIAL_TEST) if UNKNOWN_RECOVERY_ADVERSARIAL_TEST.is_file() else ""
    cold_start_test = _text(COLD_START_TRANSPORT_TEST) if COLD_START_TRANSPORT_TEST.is_file() else ""

    for token in (
        "class GuardedAlpacaPaperCryptoWriteTransport(AlpacaPaperCryptoWriteTransport, Protocol):",
        "class AlpacaPaperCryptoWriteTransport(Protocol):",
        "class AlpacaPaperCryptoWriterConfig:",
        "enabled: bool = False",
        "base_url: str = f\"https://{ALPACA_PAPER_TRADING_HOST}\"",
        "def submit_once(",
        "mark_entry_submission_unknown",
        "mark_protection_submission_unknown",
        "attempt_count != 1",
        "client_order_id",
        "CryptoPaperWriterAmbiguous",
        "CryptoPaperWriterProtocolError",
        "AlpacaPaperCryptoHttpsTransport",
    ):
        if token not in writer:
            errors.append(f"writer contract missing: {token}")
    if writer and not _writer_has_nominal_gate(writer):
        errors.append("writer nominal guarded transport/role gate must execute before lifecycle UNKNOWN and broker I/O")

    for token in (
        "class FinalGuardedCryptoEntryTransport(GuardedAlpacaPaperCryptoWriteTransport):",
        "class FinalGuardedCryptoProtectionTransport(GuardedAlpacaPaperCryptoWriteTransport):",
        "role = CryptoOrderRole.ENTRY",
        "role = CryptoOrderRole.PROTECTION",
        "FinalCryptoExecutionPreIoAuthorizer",
        "FinalCryptoProtectionPreIoAuthorizer",
        "attestation.phase is not CryptoFinalWritePhase.PRE_IO",
        "attestation.phase is not CryptoProtectionFinalWritePhase.PRE_IO",
        "ENTRY_SUBMISSION_UNKNOWN",
        "PROTECTION_SUBMISSION_UNKNOWN",
        "attempt_count != 1",
    ):
        if token not in pre_io:
            errors.append(f"PRE_IO interlock contract missing: {token}")
    if cold_start_pre_io:
        errors.extend(_cold_start_transport_contract(cold_start_pre_io))

    production_guarded: set[str] = set()
    production_files: dict[str, set[str]] = {}
    for path in sorted(SRC.glob("alpaca_paper_crypto*.py")):
        source = path.read_text(encoding="utf-8")
        subclasses = _guarded_subclasses(source)
        if subclasses:
            production_files[path.name] = subclasses
            production_guarded.update(subclasses)
    if production_guarded != EXPECTED_GUARDED_SUBCLASSES:
        errors.append(
            "production guarded crypto transport subclasses must be exactly "
            f"{sorted(EXPECTED_GUARDED_SUBCLASSES)}; got {sorted(production_guarded)} from {production_files}"
        )

    for token in (
        "class CryptoLifecycleStatus",
        "ENTRY_SUBMISSION_UNKNOWN",
        "PROTECTION_SUBMISSION_UNKNOWN",
        "HALTED_RECONCILIATION_REQUIRED",
        "attempt_count",
        "reconciliation_only",
        "validate_entry_write_attempt",
        "validate_protection_write_attempt",
    ):
        if token not in lifecycle:
            errors.append(f"lifecycle contract missing: {token}")

    for token in (
        "class CryptoBrokerOrderAbsenceEvidence",
        "class CryptoBrokerUnknownReconciliation",
        "if status_code == 404:",
        "position_status_code, position_body, position_headers =",
        "retry_authorized=False",
    ):
        if token not in reconciliation:
            errors.append(f"reconciliation UNKNOWN/404 contract missing: {token}")
    if reconciliation:
        absence_pos = reconciliation.find("if status_code == 404:")
        position_pos = reconciliation.find("position_status_code, position_body, position_headers =")
        unknown_pos = reconciliation.find("return CryptoBrokerUnknownReconciliation(")
        if min(absence_pos, position_pos, unknown_pos) < 0 or not (absence_pos < position_pos < unknown_pos):
            errors.append("exact-order 404 must still fetch position before returning UNKNOWN reconciliation")

    for token in (
        "def recover_unknown_entry(",
        "def recover_unknown_protection(",
        "CryptoExecutionAttemptCheckpoint",
        "CryptoProtectionExecutionAttemptCheckpoint",
        "PaperFlatAccountAttestation",
        "HALTED_RECONCILIATION_REQUIRED",
        "FLAT_RECONCILED",
        "retry_authorized=False",
        "RECONCILE_ONLY",
        "fresh_account.account_reference != pre.account_reference",
        "fresh_account.credential_reference != pre.credential_reference",
        "flat_account.account_attestation_fingerprint != fresh_account.fingerprint",
    ):
        if token not in recovery:
            errors.append(f"UNKNOWN recovery contract missing: {token}")
    for forbidden in ("AlpacaPaperCryptoHttpsTransport", "urllib", "requests", "httpx", "socket", "R6_EXTERNAL_PAPER_WRITE"):
        if forbidden in recovery:
            errors.append(f"UNKNOWN recovery must remain offline/no-network: {forbidden}")

    if "_mutate(" not in lifecycle:
        errors.append("lifecycle mutation transaction primitive missing")
    mutate_users: list[str] = []
    for path in sorted(SRC.glob("alpaca_paper_crypto*.py")):
        if path in {LIFECYCLE, RECOVERY}:
            continue
        source = path.read_text(encoding="utf-8")
        if re.search(r"\.\s*_mutate\s*\(", source):
            mutate_users.append(path.name)
    if mutate_users:
        errors.append(f"package-internal lifecycle _mutate may only be used by dedicated UNKNOWN recovery; found {mutate_users}")

    for token in ("account_reference", "credential_reference", "fresh_account_fingerprint", "position_credential_reference"):
        if token not in protection_attempt:
            errors.append(f"protection checkpoint lost same-account binding: {token}")

    for token in (
        "test_enabled_writer_rejects_raw_transport_before_lifecycle_mutation_or_io",
        "test_enabled_writer_rejects_default_https_transport_before_lifecycle_mutation_or_io",
        "test_enabled_writer_rejects_role_mismatch_before_lifecycle_mutation_or_io",
    ):
        if token not in test:
            errors.append(f"writer fail-closed transport test missing: {token}")
    if "FinalGuardedCryptoEntryTransport" not in test:
        errors.append("ENTRY nominal guarded capability is not exercised by writer tests")
    if "FinalGuardedCryptoProtectionTransport" not in protection_test:
        errors.append("PROTECTION nominal guarded capability is not exercised by tests")
    if "ColdStartFinalGuardedCryptoEntryTransport" not in cold_start_test:
        errors.append("cold-start ENTRY nominal guarded capability is not exercised by tests")

    for token in (
        "test_unknown_entry_long_halts_and_never_authorizes_retry",
        "test_unknown_entry_zero_requires_full_flat_account_attestation",
        "test_unknown_entry_zero_clean_flat_account_reconciles_flat",
        "test_unknown_protection_long_halts",
        "test_unknown_protection_zero_clean_flat_account_reconciles_flat",
    ):
        if token not in unknown_test:
            errors.append(f"UNKNOWN recovery scenario test missing: {token}")
    for token in (
        "test_unknown_recovery_rejects_wrong_input_types",
        "test_unknown_entry_rejects_checkpoint_from_other_lifecycle",
        "test_unknown_entry_rejects_long_plus_flat_override",
        "test_unknown_entry_rejects_account_and_credential_rebinding",
        "test_unknown_entry_rejects_stale_future_and_nonatomic_evidence",
        "test_unknown_entry_flat_path_rejects_nonzero_counts_and_binding_drift",
        "test_unknown_recovery_receipt_rejects_tampering",
    ):
        if token not in unknown_adv:
            errors.append(f"UNKNOWN recovery adversarial test missing: {token}")

    if WORKFLOW.is_file():
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for token in (
            "python scripts/check_r6_crypto_execution_boundary.py",
            "tests/test_r6_paper_crypto_writer.py",
            "tests/test_r6_paper_crypto_reconciliation.py",
            "tests/test_r6_paper_crypto_writer_ambiguity.py",
            "tests/test_r6_paper_crypto_pre_io.py",
            "tests/test_r6_paper_crypto_protection_pre_io.py",
            "tests/test_r6_paper_crypto_unknown_recovery.py",
            "tests/test_r6_paper_crypto_unknown_recovery_adversarial.py",
        ):
            if token not in workflow:
                errors.append(f"crypto execution workflow missing: {token}")
    else:
        errors.append("R6 crypto execution workflow missing")
    if COLD_START_WORKFLOW.is_file():
        cold_workflow = COLD_START_WORKFLOW.read_text(encoding="utf-8")
        for token in (
            "python scripts/check_r6_crypto_execution_boundary.py",
            "tests/test_r6_paper_crypto_cold_start_pre_io_transport.py",
        ):
            if token not in cold_workflow:
                errors.append(f"cold-start execution workflow missing: {token}")
    else:
        errors.append("R6 cold-start execution authority workflow missing")

    for path in sorted(SRC.glob("alpaca_paper_crypto*.py")):
        source = path.read_text(encoding="utf-8")
        if path.name != ALLOWED_WRITER:
            if WRITE_ENV in source:
                errors.append(f"write env leaked outside single writer: {path.name}")
            bad_imports = _network_imports(source)
            if bad_imports:
                errors.append(f"direct network stack imported outside single writer in {path.name}: {sorted(bad_imports)}")
            for token in ("urlopen(", "requests.", "httpx.", "HTTPSConnection("):
                if token in source:
                    errors.append(f"direct crypto broker network call outside single writer in {path.name}: {token}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "R6 crypto execution boundary: PASS "
        "(single writer, exactly three role-bound nominal PRE_IO capabilities: normal ENTRY, "
        "normal PROTECTION, isolated cold-start ENTRY; UNKNOWN before I/O; exact-order 404 recovery; no blind retry)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
