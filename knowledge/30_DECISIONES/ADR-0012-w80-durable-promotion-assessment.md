# ADR-0012 — Durable Promotion Assessment Is Append-Only Evidence, Not Trading Authority

- Status: **ACCEPTED / W80 TECHNICALLY CERTIFIED**
- Date: 2026-08-23
- Scope: Strategy Promotion Governance / Strategy Lab
- PAPER candidate authority: **FALSE**
- External capital authority: **NONE**
- Broker write authority: **NONE**
- LIVE: **BLOCKED**

## Context

W79 froze promotion thresholds before DEVELOPMENT and froze one exact candidate after Tournament but before FINAL_HOLDOUT. It could evaluate the canonical gates, but deliberately did not persist the resulting assessment. Strategy Lab therefore exposed `gate_evidence_state=NOT_PERSISTED_BY_W79` and could not claim that a gate had durably passed merely because partial evidence happened to exist at read time.

The missing layer was a durable scientific receipt that answered a narrower question: **what did the canonical W79 evaluator conclude, against exactly which policy and evidence, at a specific point in the evidence history?**

Persisting that conclusion must not create a shortcut from research evidence to broker authority.

## Decision

W80 adds an append-only, hash-bound Promotion Assessment journal on the same authoritative SQLite runtime as the W79 promotion policies and Trial Ledger.

The writer does not accept an arbitrary prebuilt promotion view. It executes the canonical W79 evaluator internally and persists the resulting gate state as a receipt.

Each receipt binds:
- assessment id;
- ordinal within the policy chain;
- exact `policy_id` and `policy_hash`;
- exact threshold-policy hash;
- selected strategy id and version;
- source W79 evidence-view hash;
- predecessor assessment hash;
- timezone-aware assessment timestamp;
- the exact canonical W79 gate set;
- each gate status, reason codes and evidence hashes;
- aggregate assessment state;
- permanent promotion blockers;
- authority flags fixed to false/NONE/BLOCKED;
- its own SHA-256 assessment hash.

The journal is application-level append-only. Registration uses `BEGIN IMMEDIATE`, so competing assessment writers cannot race the ordinal/predecessor decision.

## Evidence-history monotonicity

For one frozen promotion policy, a later assessment may add evidence or resolve previously missing evidence, but it may not rewrite history.

W80 therefore rejects:
- duplicate/conflicting assessment identity;
- an unchanged W79 view appended under a new assessment id;
- predecessor-hash discontinuity;
- ordinal discontinuity;
- non-increasing timestamps;
- frozen strategy/policy identity drift;
- disappearance of evidence hashes already observed for a gate;
- regression of a gate from a non-`MISSING` state back to `MISSING`.

Materially different evidence that cannot preserve the previous evidence chain is not silently substituted. It requires a separately governed evidence/policy transition.

## Independent read model

The Strategy Lab consumer does not trust the writer module to validate its own output.

`strategy_promotion_assessment_read_model.py`:
- does not import the W80 writer;
- opens `core.sqlite3` with SQLite URI `mode=ro`;
- enables `PRAGMA query_only=ON`;
- rejects symlinked/missing authoritative DBs;
- independently recomputes receipt hashes;
- verifies receipt/SQLite side-column equality;
- reconstructs and validates the complete predecessor chain;
- rechecks evidence-history monotonicity;
- independently reconstructs the frozen W79 candidate policy;
- verifies assessment `policy_hash`, threshold hash and selected strategy id/version against that W79 policy;
- verifies the W79 threshold-policy identity still exists and matches.

A self-consistent assessment journal that has become detached from the frozen W79 policy is therefore invalid.

## Strategy Lab projection

W80 extends the existing GET-only Strategy Lab projection; it does not add an action route.

Two provenance domains remain deliberately separate:

1. **W79 governance provenance** — preregistered thresholds and frozen candidate. Its field remains exactly `gate_evidence_state=NOT_PERSISTED_BY_W79` because W79 itself never persisted an assessment.
2. **W80 assessment provenance** — independently verified durable assessment history, or explicit `NO_DURABLE_W80_ASSESSMENT` when none exists.

The UI can show latest durable assessments, gate states, reasons and evidence hashes, but must continue displaying:
- `PAPER CANDIDATE · FALSE`;
- `CAPITAL · NONE`;
- `LIVE · BLOCKED`;
- `Broker POST: NO`.

`EVIDENCE_QUALIFIED` remains a scientific evidence state, not a permission state.

## Authority boundary

W80 may write only its local scientific assessment journal. It has no authority to:
- submit/cancel/replace broker orders;
- read or persist broker credentials;
- create external execution permits;
- stage OMS external handoff;
- invoke Capital Safety as an execution authority;
- construct an Auto-Paper `OrderIntent` path;
- enable LIVE;
- turn an assessment result into `paper_candidate_authorized=true`.

Permanent Core boundaries protect the writer, independent reader and Strategy Lab projection separately.

## Cryptographic scope

The SHA-256 chain is a deterministic tamper-evidence mechanism for the local application contract. It is **not** an externally signed transparency log and does not claim resistance to a fully privileged malicious administrator who can rewrite the entire SQLite database and all dependent policy rows coherently.

If stronger non-repudiation is required later, a signed or externally anchored checkpoint should be added as a separate security design rather than overstating the guarantees of W80.

## Technical certification

Behavioral implementation head:

`492ca4a621b263324b2cb5322490d74beda66a9c`

Dedicated W80 workflow `32671751555`:
- **46/46 W80 tests PASS**;
- W80 assessment writer boundary PASS;
- W80 independent reader boundary PASS;
- W80 Strategy Lab durable projection boundary PASS;
- W79 promotion boundary PASS;
- W79 Strategy Lab boundary PASS;
- Mac Control Center boundary PASS;
- W78 boundary PASS;
- Research authority PASS.

Core Safety workflow `32671751544`:
- **2890/2890 PASS**;
- exact branch coverage `85.1061367161277%` >= 85%;
- `strategy_promotion_assessment.py`: 82%;
- `strategy_promotion_assessment_read_model.py`: 84%;
- `strategy_lab_read_model.py`: 84%;
- all inherited R5/R6/R7/W78/W79 boundaries PASS;
- all three W80 boundaries PASS;
- Debt Register PASS;
- Canonical Knowledge PASS.

## What W80 does not close

W80 does not prove that a strategy is profitable and does not make it eligible for automated external PAPER.

The following blockers remain open:
- `TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN` / `TD-R7D-001`;
- `FEE_ACCOUNTING_INCOMPLETE` / `TD-R7D-002`;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- `EXECUTION_STRATEGY_VERSION_UNBOUND`;
- `TD-R7D-003` safe remaining-quantity reservation after partial fills.

## Next decision

The next economic-integrity wave should address `TD-R7D-001` by proving that Research cost assumptions are not silently weakened by observed spread plus execution slippage across Research -> W78 qualification -> external PAPER evidence.

Fee-complete accounting remains a separate P1 and must not be implied closed by market-impact continuity.

**LIVE TRADING: BLOQUEADO.**