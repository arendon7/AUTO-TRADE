from pathlib import Path

path = Path("src/autotrade/portfolio_manager.py")
text = path.read_text()

old = '''    base_budget_fingerprint: str\n    health_budget_fingerprint: str\n'''
new = '''    base_budget_fingerprint: str\n    base_robustness_fingerprint: str\n    health_budget_fingerprint: str\n'''
if old not in text:
    raise SystemExit("decision fingerprint fields block not found")
text = text.replace(old, new, 1)

old = '''            ("base_budget_fingerprint", self.base_budget_fingerprint),\n            ("health_budget_fingerprint", self.health_budget_fingerprint),\n'''
new = '''            ("base_budget_fingerprint", self.base_budget_fingerprint),\n            ("base_robustness_fingerprint", self.base_robustness_fingerprint),\n            ("health_budget_fingerprint", self.health_budget_fingerprint),\n'''
if old not in text:
    raise SystemExit("decision fingerprint validation block not found")
text = text.replace(old, new, 1)

old = '''            "base_budget_fingerprint": self.base_budget_fingerprint,\n            "health_budget_fingerprint": self.health_budget_fingerprint,\n'''
new = '''            "base_budget_fingerprint": self.base_budget_fingerprint,\n            "base_robustness_fingerprint": self.base_robustness_fingerprint,\n            "health_budget_fingerprint": self.health_budget_fingerprint,\n'''
if old not in text:
    raise SystemExit("decision payload block not found")
text = text.replace(old, new, 1)

old = '''        self._require_robust(\n            dependence=dependence,\n            diversification_policy=diversification_policy,\n            weights=dict(base_budget.strategy_weights),\n            robustness_spec=robustness_spec,\n            robustness_policy=robustness_policy,\n            reason_code="BASE_ALLOCATION_NOT_ROBUST",\n        )\n'''
new = '''        base_robustness = self._require_robust(\n            dependence=dependence,\n            diversification_policy=diversification_policy,\n            weights=dict(base_budget.strategy_weights),\n            robustness_spec=robustness_spec,\n            robustness_policy=robustness_policy,\n            reason_code="BASE_ALLOCATION_NOT_ROBUST",\n        )\n'''
if old not in text:
    raise SystemExit("base robustness call not found")
text = text.replace(old, new, 1)

old = '''            base_budget_fingerprint=base_budget.fingerprint,\n            health_budget_fingerprint=health_budget.fingerprint,\n'''
new = '''            base_budget_fingerprint=base_budget.fingerprint,\n            base_robustness_fingerprint=base_robustness.fingerprint,\n            health_budget_fingerprint=health_budget.fingerprint,\n'''
if old not in text:
    raise SystemExit("decision construction block not found")
text = text.replace(old, new, 1)

old = '''            if candidate.strategy_key in by_key:\n                raise PortfolioSizingBlocked(\n                    "DUPLICATE_CANDIDATE",\n                    candidate.strategy_key,\n                )\n            by_key[candidate.strategy_key] = candidate\n'''
new = '''            if candidate.strategy_key in by_key:\n                raise PortfolioSizingBlocked(\n                    "DUPLICATE_CANDIDATE",\n                    candidate.strategy_key,\n                )\n            strategy_id, separator, strategy_version = candidate.strategy_key.rpartition("@")\n            if not separator or not strategy_id or not strategy_version:\n                raise PortfolioSizingBlocked(\n                    "INVALID_STRATEGY_KEY",\n                    candidate.strategy_key,\n                )\n            if candidate.health_entity_id != strategy_id:\n                raise PortfolioSizingBlocked(\n                    "HEALTH_ENTITY_STRATEGY_MISMATCH",\n                    f"{candidate.health_entity_id}!={strategy_id}",\n                )\n            by_key[candidate.strategy_key] = candidate\n'''
if old not in text:
    raise SystemExit("candidate universe block not found")
text = text.replace(old, new, 1)

path.write_text(text)
