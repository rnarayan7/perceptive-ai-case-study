"""Tests for the per-company query profiles.

The point of this module is that ``ingest --source all`` stops silently returning
nothing from the sources that need drug names, so the tests assert the shape of what
each source is handed rather than the specific drugs (which are analyst input and will
change as the comparator landscape does).
"""

import pytest

from memo.companies import PROFILES, CompanyProfile, get_profile, source_options
from memo.ingestion import REGISTRY

BRIEF_COMPANIES = {"ABVX", "KYMR", "PRAX", "IMVT", "COGT"}

# The sources that cannot be driven from a ticker alone.
TERM_DEPENDENT = {"openfda", "cms", "nadac", "pubchem", "pubmed"}


def test_every_brief_company_has_a_profile():
    assert set(PROFILES) == BRIEF_COMPANIES


@pytest.mark.parametrize("ticker", sorted(BRIEF_COMPANIES))
def test_profile_is_populated(ticker):
    profile = get_profile(ticker)
    assert isinstance(profile, CompanyProfile)
    assert profile.ticker == ticker
    assert profile.lead_asset
    assert profile.asset_codes, "asset codes drive PubChem lookups and retrieval keywords"
    assert profile.indications
    assert profile.literature_terms


@pytest.mark.parametrize("ticker", sorted(BRIEF_COMPANIES))
def test_every_company_has_comparators(ticker):
    """Every company needs a comparator landscape; whether it is infused or oral varies.

    PRAX and COGT are oral-only, so their Part B (ASP) list is empty by design. An empty
    *combined* list would mean the pricing sources have nothing to query, which is the
    bug this module exists to prevent.
    """
    profile = get_profile(ticker)
    assert profile.all_comparators


@pytest.mark.parametrize("ticker", sorted(BRIEF_COMPANIES))
@pytest.mark.parametrize("source", sorted(TERM_DEPENDENT))
def test_term_dependent_sources_receive_terms(source, ticker):
    options = source_options(source, ticker)
    assert options, f"{source} would query nothing for {ticker}"
    # Each option value must be non-empty, or the source treats it as "not provided".
    for key, value in options.items():
        if key == "indication":
            continue  # optional refinement alongside terms
        assert value, f"{source}[{key}] is empty for {ticker}"


def test_source_options_keys_match_what_the_ingesters_read():
    """Guards against a rename drifting the profile away from the fetch signature."""
    expected = {
        "openfda": {"terms", "indication"},
        "cms": {"drugs"},
        "nadac": {"drugs"},
        "asp": {"drugs"},
        "pubchem": {"compounds"},
        "pubmed": {"term"},
    }
    for source, keys in expected.items():
        assert set(source_options(source, "KYMR")) == keys


def test_ticker_is_case_and_whitespace_insensitive():
    assert get_profile(" kymr ").ticker == "KYMR"
    assert source_options("cms", "kymr") == source_options("cms", "KYMR")


def test_unknown_ticker_returns_empty_options_not_an_error():
    """A caller merges these unconditionally, so an unknown ticker must not raise."""
    assert source_options("cms", "ZZZZ") == {}
    with pytest.raises(KeyError):
        get_profile("ZZZZ")


def test_sources_needing_nothing_extra_return_empty():
    for source in ("edgar", "xbrl", "clinicaltrials", "orphanet", "cdc", "preprints"):
        assert source_options(source, "KYMR") == {}


def test_every_profiled_source_is_a_real_registry_key():
    for source in TERM_DEPENDENT | {"asp"}:
        assert source in REGISTRY


def test_part_b_comparators_track_the_asp_table():
    """The ASP table stays the single source of truth for infused comparators."""
    from memo.ingestion.asp import COMPARATORS

    for ticker, profile in PROFILES.items():
        assert profile.part_b_comparators == [str(d) for d in COMPARATORS.get(ticker, ())]


def test_comparators_are_deduplicated_in_stable_order():
    profile = get_profile("ABVX")
    combined = profile.all_comparators
    assert len(combined) == len(set(combined))
    assert combined[: len(profile.part_b_comparators)] == profile.part_b_comparators
