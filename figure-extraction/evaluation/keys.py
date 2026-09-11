"""The quantity-key contract.

The controlled vocabulary of canonical value keys, one list per figure. Both the
extractor (its output) and the gold (its answer key) tag values with these keys,
so the matcher aligns them by lookup instead of fuzzy name comparison.

Keys are instantiated concretely for our six figures (params like the 6-month
landmark or the 0.03 mg/kg dose are baked into the key), which keeps matching an
exact string compare. The parametric template is given in each key's description.
Adding a figure or a value is a change here and nowhere else.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from evaluation.types import Family, QuantitySpec

# Figure ids line up with assets/figures/*.png.
FIG_KM = "fig01_km"
FIG_WATERFALL = "fig02_waterfall"
FIG_PK = "fig03_pk"
FIG_FOREST = "fig04_forest"
FIG_TABLE = "fig05_table"
FIG_SPIDER = "fig06_spider"

FIGURE_TYPE: Dict[str, str] = {
    FIG_KM: "kaplan_meier",
    FIG_WATERFALL: "waterfall",
    FIG_PK: "pk_logscale",
    FIG_FOREST: "forest",
    FIG_TABLE: "table",
    FIG_SPIDER: "spider",
}

# Tolerances are per-family, in the family's own units:
#   continuous -> absolute in `unit`; log_scale -> fold; fold_multiple -> relative.
FIGURE_REQUESTS: Dict[str, List[QuantitySpec]] = {
    FIG_KM: [
        QuantitySpec("median_pfs.ozekibart", Family.CONTINUOUS, "months", 1.0,
                     "median PFS, treatment arm"),
        QuantitySpec("median_pfs.placebo", Family.CONTINUOUS, "months", 1.0,
                     "median PFS, control arm"),
        QuantitySpec("pfs_6mo.ozekibart", Family.CONTINUOUS, "probability", 0.05,
                     "PFS probability at 6 months, treatment arm"),
        QuantitySpec("pfs_6mo.placebo", Family.CONTINUOUS, "probability", 0.05,
                     "PFS probability at 6 months, control arm"),
    ],
    FIG_WATERFALL: [
        QuantitySpec("psa50_proportion.ge2mg", Family.PROPORTION, None, 2,
                     "proportion of >=2mg subgroup with >=50% PSA reduction"),
        QuantitySpec("psa90_proportion.ge2mg", Family.PROPORTION, None, 2,
                     "proportion of >=2mg subgroup with >=90% PSA reduction"),
        QuantitySpec("deepest_bar_depth", Family.CONTINUOUS, "percent", 5.0,
                     "depth of the deepest PSA bar"),
        QuantitySpec("leftmost_bar_height", Family.CONTINUOUS, "percent", 5.0,
                     "height of the leftmost PSA bar"),
        QuantitySpec("recist_n_plotted", Family.CONTINUOUS, "count", 2.0,
                     "number of patients plotted in the RECIST panel"),
        QuantitySpec("recist_beyond_30_proportion", Family.PROPORTION, None, 2,
                     "proportion of RECIST bars beyond the -30% line"),
    ],
    FIG_PK: [
        QuantitySpec("concentration_day14.dose_003", Family.LOG_SCALE, "nM", 1.6,
                     "plasma concentration at day 14, 0.03 mg/kg"),
        QuantitySpec("threshold_concentration", Family.LOG_SCALE, "nM", 1.3,
                     "marked MTD threshold concentration (labeled 20 pM)"),
        QuantitySpec("fold_multiple.dose_03_peak", Family.FOLD_MULTIPLE, "x", 0.5,
                     "fold-multiple of 0.3 mg/kg peak over the threshold"),
        QuantitySpec("crossing_1nM_interval.dose_003", Family.CATEGORICAL_SET, None, None,
                     "the two sampled timepoints bracketing the 1 nM crossing"),
    ],
    FIG_FOREST: [
        QuantitySpec("hazard_ratio.idh_wildtype", Family.RATIO_CI, None, 0.1,
                     "HR and 95% CI for IDH wild type"),
        QuantitySpec("hazard_ratio.ecog_ps_0", Family.RATIO_CI, None, 0.1,
                     "HR and 95% CI for ECOG PS 0"),
        QuantitySpec("crosses_one.set", Family.CATEGORICAL_SET, None, None,
                     "every subgroup whose CI crosses 1"),
    ],
    FIG_TABLE: [
        QuantitySpec("cr_12mo.bond003_cohortC", Family.PROPORTION, None, None,
                     "CR at 12 months, BOND-003 Cohort C, with denominator"),
        QuantitySpec("cr_12mo.sunrise1", Family.PROPORTION, None, None,
                     "CR at 12 months, SunRISe-1, with denominator"),
        QuantitySpec("dor_24mo.quilt3032", Family.CONTINUOUS, "percent", 2.0,
                     "24-month duration of response, QUILT 3.032"),
        QuantitySpec("grade3_trae.tar200", Family.CONTINUOUS, "percent", 2.0,
                     "grade 3+ treatment-related AE rate, TAR-200"),
        QuantitySpec("nct02773849_denominator_reason", Family.INTERPRETIVE, None, None,
                     "why the patient number differs between CR any-time and CR 12mo"),
        QuantitySpec("multi_estimator.sunrise1_dor12mo", Family.INTERPRETIVE, None, None,
                     "both estimators for SunRISe-1 12M DOR; which is comparable"),
    ],
    FIG_SPIDER: [
        QuantitySpec("implied_pfs_6mo", Family.CONTINUOUS, "probability", 0.1,
                     "implied PFS at 6 months from the spider trajectories"),
        QuantitySpec("implied_median_pfs", Family.INTERPRETIVE, None, None,
                     "implied median PFS (may be 'not reached')"),
        QuantitySpec("conversion_rule", Family.INTERPRETIVE, None, None,
                     "the event/censoring rule applied"),
        QuantitySpec("median_range", Family.INTERPRETIVE, None, None,
                     "range of medians consistent with the figure"),
    ],
}


def spec_for(figure_id: str, quantity_key: str) -> Optional[QuantitySpec]:
    """Look up the spec for a (figure, key) pair, or ``None`` if unknown."""
    for spec in FIGURE_REQUESTS.get(figure_id, []):
        if spec.key == quantity_key:
            return spec
    return None


def all_specs() -> List[QuantitySpec]:
    return [spec for specs in FIGURE_REQUESTS.values() for spec in specs]
