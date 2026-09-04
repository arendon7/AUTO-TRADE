from autotrade.research.strategy_catalog import CATALOG, StrategyFamily, get_strategy, research_only_catalog


def test_catalog_is_non_empty_unique_and_research_only():
    catalog = research_only_catalog()
    assert len(catalog) >= 8
    assert len({entry.strategy_id for entry in catalog}) == len(catalog)
    assert all(entry.execution_authority is False for entry in catalog)
    assert all(entry.live_authority is False for entry in catalog)


def test_catalog_covers_core_diversified_strategy_families():
    families = {entry.family for entry in CATALOG}
    expected = {
        StrategyFamily.TREND_FOLLOWING,
        StrategyFamily.TIME_SERIES_MOMENTUM,
        StrategyFamily.CROSS_SECTIONAL_MOMENTUM,
        StrategyFamily.MEAN_REVERSION,
        StrategyFamily.BREAKOUT,
        StrategyFamily.VOLATILITY_REGIME,
        StrategyFamily.STATISTICAL_ARBITRAGE,
        StrategyFamily.MARKET_MAKING,
        StrategyFamily.CARRY_BASIS,
        StrategyFamily.ML_RANKING,
    }
    assert expected <= families


def test_external_sources_have_explicit_license_provenance():
    for entry in CATALOG:
        assert entry.source_projects
        assert len(entry.source_projects) == len(entry.source_licenses)
        assert all(source.strip() for source in entry.source_projects)
        assert all(license_name.strip() for license_name in entry.source_licenses)


def test_gpl_and_lgpl_sources_are_reference_only():
    for entry in CATALOG:
        for license_name in entry.source_licenses:
            if license_name.startswith(("GPL", "LGPL")):
                assert "reference-only" in license_name


def test_lookup_is_exact_and_fail_closed():
    strategy = get_strategy("trend_ema_atr_v1")
    assert strategy.family is StrategyFamily.TREND_FOLLOWING

    try:
        get_strategy("does-not-exist")
    except KeyError as exc:
        assert exc.args == ("does-not-exist",)
    else:
        raise AssertionError("unknown strategy must fail closed")
