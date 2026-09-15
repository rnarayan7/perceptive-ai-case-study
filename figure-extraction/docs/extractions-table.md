# Stage 1 Extractions

Each requested quantity with the value read, the method that produced it, and a confidence.

Confidence is mechanical: high means a label/table read or a public number confirms it; medium means a measured or corrected read; low means an eyeball or log-axis interpolation the figure cannot pin down. Figures 1, 2, 5, 6 have public ground truth. Figures 3 and 4 name real programs but their specific requested numbers are not publicly disclosed and appear constructed for the exercise, so those reads can only be scored against a manual read, not a public source.

Values not present in the source files are marked to-confirm rather than guessed.

## Figure 1 — Kaplan-Meier (Inhibrx ozekibart, chondrosarcoma / ChonDRAgon)

| Quantity | Value | Method | Confidence |
|---|---|---|---|
| Median PFS, treatment arm (ozekibart) | 5.52 months | Read off residual median guide line; confirmed by Inhibrx topline PR 23 Oct 2025 (NCT04950075) | high |
| Median PFS, control arm (placebo) | 2.66 months | Read off residual median guide line; confirmed by same public source | high |
| PFS probability at 6 months, treatment arm | 0.47 | Eyeball read of curve height at x=6; 6-month landmark not publicly disclosed | low |
| PFS probability at 6 months, control arm | 0.19 | Eyeball read of curve height at x=6; not publicly disclosed | low |

The two medians match recovered public numbers. Both 6-month landmarks are eyeballed with no public confirmation and likely read off a synthetic curve.

## Figure 2 — Waterfall (Janux JANX007, mCRPC)

| Quantity | Value | Method | Confidence |
|---|---|---|---|
| PSA50 proportion, >=2mg subgroup | 73% (62/85) | CV pixel-measurement of bars past the -50% line; confirmed by Janux PR 1 Dec 2025 (numerator inferred from 73% of n=85) | high |
| PSA90 proportion, >=2mg subgroup | 26% (22/85) | CV pixel-measurement past the -90% line; confirmed by Janux PR (numerator inferred from 26% of n=85). CV corrected the eyeball read of 10% | high |
| Depth of deepest PSA bar | -97% | Eyeball; individual bar depth not published | low |
| Height of leftmost PSA bar | 62% | Eyeball; individual bar not published | low |
| Patients plotted in RECIST panel | 27 | CV count; confirmed by Janux PR (RECIST-evaluable n=27 at >=2mg) | high |
| Proportion of RECIST bars beyond -30% | 30% (8/27) | CV count against the -30% line; confirmed by Janux PR (ORR 30%, 8/27 RECIST-evaluable) | high |

PSA50, PSA90, RECIST n, and the beyond-30% proportion are all confirmed against the public release. The two individual-bar reads (deepest, leftmost) are eyeballs with no published value.

## Figure 3 — PK log-scale (Janux TROP2-TRACTr) — values likely synthetic

| Quantity | Value | Method | Confidence |
|---|---|---|---|
| Plasma concentration at day 14, 0.03 mg/kg | 0.25 nM | Log-axis interpolation between decades; dose-level PK not publicly recoverable | low |
| MTD threshold concentration (labeled 20 pM) | 0.02 nM | Read from the figure's own label "20 pM MTD" | high |
| Fold-multiple of 0.3 mg/kg peak over threshold | 6000x | Derived from eyeballed peak on the log axis; not publicly recoverable | low |
| Two sampled timepoints bracketing the 1 nM crossing | day 4, day 7 | Bracketed between sampled points shown on the figure | high |

The program is real but the dose-level PK values appear constructed. Only the labeled threshold and the bracketing timepoints are firm reads. The two interpolated/derived numbers carry real log-axis uncertainty (no minor gridlines).

## Figure 4 — Forest plot (ChonDRAgon subgroup HRs) — values likely synthetic

| Quantity | Value | Method | Confidence |
|---|---|---|---|
| HR and 95% CI, IDH wild-type | 0.50 (95% CI 0.33-0.72) | Eyeball of marker and whisker positions; per-subgroup HRs not published | low |
| HR and 95% CI, ECOG PS 0 | 0.45 (95% CI 0.28-0.70) | Eyeball of marker and whisker positions; not published | low |
| Subgroups whose CI crosses 1 | Nonmetastatic unresectable, Age over 65, BMI over 30 | Eyeball read of whisker ends | low |

Public disclosure gives only the overall HR (~0.48, "consistent across subgroups"). No numeric per-subgroup HRs are published, so all three reads are eyeballs against likely-synthetic values.

## Figure 5 — Raster table (CG Oncology NMIBC comparators)

| Quantity | Value | Method | Confidence |
|---|---|---|---|
| CR at 12 months, BOND-003 Cohort C | 46.4% (51/110) | Table transcription; confirmed by Lancet Oncology / AUA 2025 | high |
| CR at 12 months, SunRISe-1 | 45.9% (39/83) | Table transcription (K-M estimate shown in figure) | high |
| 24-month DOR, QUILT 3.032 | 40% | Table transcription | high (as shown) |
| Grade 3+ TRAE rate, TAR-200 | 13% | Table transcription; published 12.9% rounds to the figure's 13% | high |
| Why patient number differs (CR any-time vs CR 12mo, NCT02773849) | CR any-time is over the evaluable population (98); CR at 12 months uses a larger landmark/K-M at-risk set (103). Verify against the source footnote. | Interpretive read of denominators; footnote not yet confirmed | to-confirm |
| Both estimators, SunRISe-1 12M DOR, and which is comparable | Observed 52.9% and K-M 56.2%; the K-M estimate is the one comparable across columns | Table transcription | high |

Gold for this figure is what the table shows. Note a conflicting-disclosure case: the table shows QUILT 3.032 24-month DOR of 40%, while the later published figure is 53.2%. We keep 40% because the task is reading the figure, and record the conflict. The denominator-reason row is an interpretation pending the source footnote, so it is to-confirm.

## Figure 6 — Spider plot (Immatics IMA203CD8, gynecologic) — interpretive case

No published PFS answer exists for the spider trajectories; the deliverable is the rule and a defensible range, not a point.

| Quantity | Value | Method | Confidence |
|---|---|---|---|
| Implied event/censoring rule | Event = a trajectory crossing the +20% PD line or a documented new-lesion / clinical progression; censor at last assessment for ongoing markers; a marginal above-baseline-but-below-+20% trajectory is censored, not an event. | Analyst construction (stated rule applied to trajectories) | rule, not a measured value |
| Implied median PFS | not reached | Derived from the trajectories under the rule; confirmed by Immatics ASCO 2026 (median PFS not reached) | high |
| Implied PFS at 6 months | 0.80 | Derived from the trajectories; median follow-up only 5.3 months, so a 6-month PFS is not directly published | low to medium |
| Range of medians the figure cannot resolve | 9 months to not reached | Range consistent with the figure given the follow-up and censoring | not resolvable to a point |

The "not reached" median matches the public read. The 6-month value is derived and the median cannot be pinned to a point given only 5.3 months of median follow-up, so it is stated as a range.
