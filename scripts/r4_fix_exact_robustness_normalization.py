from pathlib import Path

path = Path("src/autotrade/research/allocation_robustness.py")
text = path.read_text()

old = '''    total = sum((value for _, value in positive), _ZERO)\n    normalized = tuple((key, value / total) for key, value in positive)\n'''
new = '''    normalized = _normalize_exact(positive)\n'''
if old not in text:
    raise SystemExit("baseline normalization block not found")
text = text.replace(old, new, 1)

old = '''    for removed in keys:\n        remaining_total = _ONE - baseline_map[removed]\n        if remaining_total <= _ZERO:\n            raise AllocationRobustnessError("leave-one-out requires positive remaining allocation")\n        weights = tuple(\n            (key, (_ZERO if key == removed else baseline_map[key] / remaining_total))\n            for key in keys\n        )\n        scenarios.append(\n'''
new = '''    for removed in keys:\n        remaining = tuple(\n            (key, baseline_map[key])\n            for key in keys\n            if key != removed and baseline_map[key] > _ZERO\n        )\n        if not remaining:\n            raise AllocationRobustnessError("leave-one-out requires positive remaining allocation")\n        remaining_normalized = dict(_normalize_exact(remaining))\n        weights = tuple(\n            (key, (_ZERO if key == removed else remaining_normalized[key]))\n            for key in keys\n        )\n        scenarios.append(\n'''
if old not in text:
    raise SystemExit("leave-one-out normalization block not found")
text = text.replace(old, new, 1)

old = '''            changed = dict(baseline_map)\n            changed[donor] -= delta\n            changed[receiver] += delta\n            weights = tuple((key, changed[key]) for key in keys)\n            scenarios.append(\n'''
new = '''            changed = dict(baseline_map)\n            changed[donor] -= delta\n            changed[receiver] += delta\n            weights = _normalize_exact(tuple((key, changed[key]) for key in keys))\n            scenarios.append(\n'''
if old not in text:
    raise SystemExit("perturbation normalization block not found")
text = text.replace(old, new, 1)

marker = '''def _portfolio_returns(\n    weights: tuple[tuple[str, Decimal], ...],\n    aligned: Mapping[str, tuple[Decimal, ...]],\n) -> tuple[Decimal, ...]:\n'''
helper = '''def _normalize_exact(\n    weights: tuple[tuple[str, Decimal], ...],\n) -> tuple[tuple[str, Decimal], ...]:\n    """Normalize canonical weights while preserving an exact Decimal sum of 1.\n\n    Decimal division of repeating ratios cannot represent every fraction exactly.\n    Dividing each component independently can therefore create a vector whose\n    arithmetic sum is one representational ulp away from 1. We preserve the\n    exact-sum contract by computing all but the final canonical component and\n    assigning that final component the exact remainder. This is deterministic,\n    does not change the universe/order, and never relaxes the invariant checked\n    by AllocationScenario.\n    """\n\n    if not weights:\n        raise AllocationRobustnessError("weights cannot be empty")\n    keys = tuple(key for key, _ in weights)\n    if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):\n        raise AllocationRobustnessError("weights must be canonical unique sorted order")\n    if any(not _finite(value) or value < _ZERO for _, value in weights):\n        raise AllocationRobustnessError("weights must be finite Decimal >= 0")\n    total = sum((value for _, value in weights), _ZERO)\n    if total <= _ZERO:\n        raise AllocationRobustnessError("weights must contain positive allocation")\n\n    normalized: list[tuple[str, Decimal]] = []\n    running = _ZERO\n    for index, (key, value) in enumerate(weights):\n        if index == len(weights) - 1:\n            normalized_value = _ONE - running\n        else:\n            normalized_value = value / total\n            running += normalized_value\n        if normalized_value < _ZERO:\n            raise AllocationRobustnessError("normalization produced negative weight")\n        normalized.append((key, normalized_value))\n    result = tuple(normalized)\n    if sum((value for _, value in result), _ZERO) != _ONE:\n        raise AllocationRobustnessError("exact normalization failed to sum to 1")\n    return result\n\n\n'''
if marker not in text:
    raise SystemExit("portfolio returns marker not found")
text = text.replace(marker, helper + marker, 1)
path.write_text(text)
