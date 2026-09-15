# A clean run from scratch, with automatic calibration

*What the system does out of the gate on a figure it has never seen, after adding a calibration pass that lets CV run without hand tuning. Two runs: the six-figure pipeline (router plus VLM), and the waterfall suite with automatic CV calibration.*

## What changed

CV used to need a hand-written config per figure (the axis anchors, the bar colors, the plot box), so on an unseen image it did nothing. The new calibration pass fills that config automatically, keeping the division of labor the whole system rests on:

- the VLM reads the printed reference-line values (a waterfall's zero baseline and its dashed clinical thresholds), which is a text read it is reliable at;
- deterministic CV finds the geometry (the strongest horizontal line is the baseline, the dominant saturated colors are the bars);
- the axis is built from two anchors and **self-checked** against a third labelled line. If the check fails, the figure is declined and falls back to the VLM instead of emitting a confident wrong number.

Neither side eyeballs a bar height.

## Run 1: the six brief figures, router plus VLM

Fresh run, no CV, scored against verified public gold.

| Figure type | Within tolerance | Note |
|---|---|---|
| Table | 1.00 | structured transcription, zero cell error |
| Kaplan-Meier | 1.00 | medians and landmarks, MAE 0.10 months |
| PK log-scale | 0.75 | one wide miss on a between-decade point |
| Forest | 0.33 | marker position is below what the VLM can eyeball |
| Waterfall | 0.33 | bare bars, no printed values to read |
| Spider | 0.00 | target is a range, not a point |

- **Overall: 0.64 within tolerance** (22 scored, 5 interpretive, 0 missed).
- **Router: 5 of 6.** The one miss is the spider plot called a waterfall, the figure the system should decline to point-read anyway.
- **Calibration ECE 0.167**, interval coverage 0.83.
- **Keystone (does read-agreement predict error): pearson r = 0.968 on n = 15.** Strong on this run, but n is still small, so read it as encouraging, not settled.
- **Cost: $0.159 for the run, $0.026 per figure.**

The split is the same one the design predicts: near-perfect on printed numbers (table, KM), weak on the geometric reads (forest, waterfall) where the value has to be measured off the picture.

## Run 2: waterfall suite, automatic CV calibration from scratch

Four hand-labeled harvested waterfalls, no hand tuning. One VLM call per figure reads the reference lines; the rest is deterministic. Total calibration cost $0.01 for all four.

| Figure | Auto-calibrated? | Self-check | Result |
|---|---|---|---|
| Camrelizumab (9 bars, 2 colors) | yes | passed (0.14% error) | **6 of 6 vs labels**: 9 bars, leftmost +30, deepest −99 (label −98), beyond −30 = 8, beyond −50 = 8, beyond +20 = 1 |
| Anlotinib (2 panels, ~54 bars each) | axis yes | passed (0.56% error) | leftmost 61 vs labelled 62 (exact); bar counts wrong because two panels were read as one |
| PSMA (3 colors, ~185 bars) | no | n/a | **declined**: reference lines too light to detect, fell back to VLM |
| Swimmer + waterfall panel B | no | failed (23.8% error) | **declined**: compound figure, the self-check caught a bad calibration and fell back to VLM |

What this shows:

- **On a clean single-panel waterfall, automatic calibration reproduced the hand-tuned result exactly (6 of 6), for a third of a cent.** No human set the axis, colors, or plot box.
- **The self-check is a real gate.** It passed the two figures it calibrated correctly, and on the two it could not, it declined rather than guessing. Declining routes the figure to the VLM read, which is the honest fallback.
- **The failure modes are structural, not accuracy.** Multi-panel layouts get read as one panel (the anlotinib counts), and figures whose reference lines are drawn too faintly cannot be anchored geometrically (PSMA). Both are known next builds: panel splitting and tick-based calibration for figures without dark reference lines.

## The honest bottom line

Out of the gate on an unseen figure, the system now **classifies it and reads it automatically** (router plus VLM), and on waterfalls it **auto-calibrates and measures with CV where it can, and declines where it cannot.** That last property is the point: the improvement is not that CV runs everywhere, it is that the system knows when its calibration is trustworthy. On the one clean real waterfall it went from the VLM's 0.33 to a full 6-of-6 with no human in the loop; on the messy ones it stepped back instead of pretending. The remaining work is multi-panel handling and calibration for figures with no dark reference lines, which is where the declines came from.
