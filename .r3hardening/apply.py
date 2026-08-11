from __future__ import annotations

from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]

# ---- external_data.py hardening ----
path = ROOT / "src" / "autotrade" / "research" / "external_data.py"
text = path.read_text(encoding="utf-8")
text = text.replace(
    "from urllib.request import Request, urlopen\nfrom urllib.error import HTTPError, URLError\n",
    "from urllib.request import HTTPRedirectHandler, Request, build_opener\nfrom urllib.error import HTTPError, URLError\nimport socket\n",
)
if "class _PolicyRedirectHandler" not in text:
    marker = "\n\nclass UrllibReadOnlyTransport:\n"
    insert = '''\n\nclass _PolicyRedirectHandler(HTTPRedirectHandler):
    def __init__(self, policy: PublicDataPolicy) -> None:
        super().__init__()
        self._policy = policy

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Validate the redirect target BEFORE urllib performs the redirected
        # network request. Cross-host/path redirects therefore fail closed.
        self._policy.validate_final_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)
'''
    if marker not in text:
        raise SystemExit("UrllibReadOnlyTransport marker missing")
    text = text.replace(marker, insert + marker, 1)
old_init = '''        self._policy = policy
        self._max_response_bytes = max_response_bytes

    def send(self, request: ReadOnlyRequest) -> HttpResponse:
'''
new_init = '''        self._policy = policy
        self._max_response_bytes = max_response_bytes
        self._opener = build_opener(_PolicyRedirectHandler(policy))

    def send(self, request: ReadOnlyRequest) -> HttpResponse:
'''
if old_init in text:
    text = text.replace(old_init, new_init, 1)
old_open = '''            with urlopen(raw_request, timeout=request.timeout_seconds) as response:  # noqa: S310
'''
new_open = '''            with self._opener.open(raw_request, timeout=request.timeout_seconds) as response:  # noqa: S310
'''
if old_open in text:
    text = text.replace(old_open, new_open, 1)
text = text.replace(
    '''        except TimeoutError as exc:
            raise ExternalDataUnavailable("public-data request timed out") from exc
''',
    '''        except (TimeoutError, socket.timeout) as exc:
            raise ExternalDataUnavailable("public-data request timed out") from exc
''',
)
old_status = '''            if response.status_code != 200:
                raise ExternalDataUnavailable(
                    f"public-data response status is {response.status_code}"
                )
            rows = _decode_binance_rows(response.body)
'''
new_status = '''            if response.status_code != 200:
                raise ExternalDataUnavailable(
                    f"public-data response status is {response.status_code}"
                )
            content_type = response.headers.get("content-type", "").lower()
            if not content_type.startswith("application/json"):
                raise ExternalDataIntegrityError(
                    "public-data response content-type is not application/json"
                )
            rows = _decode_binance_rows(response.body)
'''
if old_status not in text:
    raise SystemExit("response status marker missing")
text = text.replace(old_status, new_status, 1)
old_epoch = '''def _epoch_ms(value: datetime) -> int:
    _require_aware(value, "timestamp")
    return int(value.astimezone(timezone.utc).timestamp() * 1000)
'''
new_epoch = '''def _epoch_ms(value: datetime) -> int:
    _require_aware(value, "timestamp")
    normalized = value.astimezone(timezone.utc)
    if normalized.microsecond % 1000:
        raise ValueError("timestamp must have exact millisecond precision")
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = normalized - epoch
    return (
        delta.days * 86_400_000
        + delta.seconds * 1_000
        + delta.microseconds // 1_000
    )
'''
if old_epoch not in text:
    raise SystemExit("epoch marker missing")
text = text.replace(old_epoch, new_epoch, 1)
path.write_text(text, encoding="utf-8")

# ---- Trial Ledger: real consumed HOLDOUT permit binding ----
path = ROOT / "src" / "autotrade" / "research" / "trials.py"
text = path.read_text(encoding="utf-8")
if "import sqlite3\n" not in text:
    text = text.replace("import json\n", "import json\nimport sqlite3\n", 1)
old_schema = '''                CREATE INDEX IF NOT EXISTS idx_research_trials_campaign
                    ON research_trials(campaign_id, trial_id);
                """
'''
new_schema = '''                CREATE INDEX IF NOT EXISTS idx_research_trials_campaign
                    ON research_trials(campaign_id, trial_id);

                CREATE TABLE IF NOT EXISTS research_holdout_trial_bindings (
                    permit_id TEXT PRIMARY KEY,
                    trial_id TEXT NOT NULL UNIQUE,
                    bound_at TEXT NOT NULL
                );
                """
'''
if old_schema not in text:
    raise SystemExit("trial schema marker missing")
text = text.replace(old_schema, new_schema, 1)
old_existing = '''            if existing is not None:
                record = _trial_record_from_row(existing)
                if record.spec.fingerprint != spec.fingerprint:
                    raise TrialConflict(f"trial identity conflict: {spec.trial_id}")
                conn.execute("COMMIT")
                return record
            conn.execute(
'''
new_existing = '''            if existing is not None:
                record = _trial_record_from_row(existing)
                if record.spec.fingerprint != spec.fingerprint:
                    raise TrialConflict(f"trial identity conflict: {spec.trial_id}")
                if spec.phase is TrialPhase.FINAL_HOLDOUT:
                    _bind_holdout_trial_tx(conn, spec=spec, now=now)
                conn.execute("COMMIT")
                return record
            if spec.phase is TrialPhase.FINAL_HOLDOUT:
                _bind_holdout_trial_tx(conn, spec=spec, now=now)
            conn.execute(
'''
if old_existing not in text:
    raise SystemExit("trial prereg existing marker missing")
text = text.replace(old_existing, new_existing, 1)
old_terminal = '''            existing = _trial_record_from_row(row)
            if existing.status.terminal:
'''
new_terminal = '''            existing = _trial_record_from_row(row)
            if existing.spec.phase is TrialPhase.FINAL_HOLDOUT:
                _require_consumed_holdout_permit_tx(conn, existing.spec)
            if existing.status.terminal:
'''
if old_terminal not in text:
    raise SystemExit("trial terminal marker missing")
text = text.replace(old_terminal, new_terminal, 1)
marker = "\ndef _campaign_payload(spec: CampaignSpec) -> dict[str, object]:\n"
helpers = '''
def _bind_holdout_trial_tx(
    conn: sqlite3.Connection, *, spec: TrialSpec, now: datetime
) -> None:
    permit_id = spec.holdout_authorization_id
    existing_permit = conn.execute(
        "SELECT trial_id FROM research_holdout_trial_bindings WHERE permit_id = ?",
        (permit_id,),
    ).fetchone()
    if existing_permit is not None and existing_permit["trial_id"] != spec.trial_id:
        raise TrialGovernanceError(
            f"HOLDOUT permit already bound to another trial: {permit_id}"
        )
    existing_trial = conn.execute(
        "SELECT permit_id FROM research_holdout_trial_bindings WHERE trial_id = ?",
        (spec.trial_id,),
    ).fetchone()
    if existing_trial is not None and existing_trial["permit_id"] != permit_id:
        raise TrialGovernanceError(
            f"HOLDOUT trial already bound to another permit: {spec.trial_id}"
        )
    if existing_permit is None and existing_trial is None:
        conn.execute(
            """
            INSERT INTO research_holdout_trial_bindings(permit_id, trial_id, bound_at)
            VALUES (?, ?, ?)
            """,
            (permit_id, spec.trial_id, now.isoformat()),
        )


def _require_consumed_holdout_permit_tx(
    conn: sqlite3.Connection, spec: TrialSpec
) -> None:
    binding = conn.execute(
        """
        SELECT permit_id FROM research_holdout_trial_bindings
        WHERE trial_id = ?
        """,
        (spec.trial_id,),
    ).fetchone()
    if binding is None or binding["permit_id"] != spec.holdout_authorization_id:
        raise TrialGovernanceError(
            "FINAL_HOLDOUT trial has no matching durable permit binding"
        )
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='holdout_permits'"
    ).fetchone()
    if table is None:
        raise TrialGovernanceError(
            "HOLDOUT permit registry is not initialized/consumed"
        )
    permit = conn.execute(
        """
        SELECT purpose, used_at FROM holdout_permits WHERE permit_id = ?
        """,
        (spec.holdout_authorization_id,),
    ).fetchone()
    if (
        permit is None
        or permit["purpose"] != "final_validation"
        or not str(permit["used_at"]).strip()
    ):
        raise TrialGovernanceError(
            "FINAL_HOLDOUT result requires a consumed final_validation permit"
        )

'''
if marker not in text:
    raise SystemExit("trial helper insertion marker missing")
text = text.replace(marker, helpers + marker, 1)
path.write_text(text, encoding="utf-8")

# ---- Core CI: permanent research authority gate ----
path = ROOT / ".github" / "workflows" / "core-tests.yml"
text = path.read_text(encoding="utf-8")
if "Research authority boundary" not in text:
    marker = "      - name: Debt register contract\n        run: python scripts/check_debt_register.py\n\n"
    addition = '''      - name: Research authority boundary
        run: python scripts/check_research_authority.py

'''
    if marker not in text:
        raise SystemExit("debt step marker missing in core workflow")
    text = text.replace(marker, addition + marker, 1)
path.write_text(text, encoding="utf-8")

# Self-clean.
shutil.rmtree(ROOT / ".r3hardening", ignore_errors=True)
workflow = ROOT / ".github" / "workflows" / "r3-hardening-one-shot.yml"
if workflow.exists():
    workflow.unlink()
print("R3 hardening patch applied")
