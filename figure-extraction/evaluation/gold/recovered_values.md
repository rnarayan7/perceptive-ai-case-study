# Recovered Ground-Truth Values — Six Oncology Figures

Public sources only (company IR, SEC/EDGAR, press releases, conference abstracts, PMC, FDA, peer-reviewed journals).

## Summary: real vs likely synthetic

| # | Figure | Trial / drug real? | Requested values recoverable? |
|---|--------|--------------------|-------------------------------|
| 1 | KM — Inhibrx ozekibart chondrosarcoma (ChonDRAgon) | **Real** | Median PFS: yes. 6-month PFS probability: not published. |
| 2 | Waterfall — Janux JANX007 mCRPC | **Real** | PSA50/PSA90/ORR: yes, all recovered. |
| 3 | PK log-scale — Janux TROP2-TRACTr | Program real, values **synthetic** | "20 pM" threshold and 0.03/0.3 mg/kg PK: not publicly recoverable. |
| 4 | Forest plot — ChonDRAgon subgroup HRs | Trial real, **subgroup HRs not published** | Specific subgroup HRs: not recoverable. |
| 5 | Raster table — CG Oncology NMIBC comparators | **All real** | All four requested values recovered. |
| 6 | Spider plot — Immatics IMA203CD8 gynecologic | **Real** | Response/PFS: yes, all recovered. |

Net: Figures 2, 5, 6 fully recoverable from public data. Figure 1 partially (median PFS yes, 6-month landmark no). Figures 3 and 4 name real programs but the specific requested numbers appear constructed for the exercise.

---

## Figure 1 — Kaplan-Meier: Inhibrx ozekibart in chondrosarcoma (ChonDRAgon)

**The trial, drug, and name are all REAL.** "Ozekibart" is the INN for INBRX-109 (a DR5 agonist), "ChonDRAgon" is the registrational trial name (NCT04950075), and the Inhibrx Biosciences chondrosarcoma program is genuine. The trial randomized 206 patients 2:1 (ozekibart vs placebo), grade 2/3 unresectable/metastatic conventional chondrosarcoma. Topline reported 23 Oct 2025; FDA accepted the BLA 15 Jun 2026 (PDUFA 14 Apr 2027).

Note: I could not locate the specific "BSG [British Sarcoma Group] oral presentation, Feb 2026, slide 8" or its curves in public sources. The values below come from the Oct 2025 topline release / trade coverage.

- **Median PFS, ozekibart: 5.52 months** — recovered.
- **Median PFS, placebo: 2.66 months** — recovered.
- Overall HR ≈ **0.48** (reported as "52% reduction in risk of progression or death"). (Context; not requested.)
- **6-month PFS probability, each arm: NOT publicly recoverable.** Landmark 6-month probabilities were not disclosed in the topline release or trade coverage; they would live only in the full conference KM curve, which I could not obtain publicly. Likely read off a synthetic curve for the case study.

Citations:
- Inhibrx topline PR, 23 Oct 2025: https://inhibrxbiosciences.investorroom.com/2025-10-23-Inhibrx-Biosciences-Reports-Positive-Topline-Results-from-its-Registrational-Trial-of-Ozekibart-INBRX-109-in-Chondrosarcoma-and-Provides-Updates-on-Colorectal-Cancer-and-Ewing-Sarcoma-Expansion-Cohorts
- Clinical Trials Arena coverage (median PFS 5.52 vs 2.66 mo): https://www.clinicaltrialsarena.com/news/inhibrx-biosciences-ozekibart-chondrosarcoma-phase-ii/
- Cancer Research UK trial page (confirms "ChonDRAgon" name): https://www.cancerresearchuk.org/about-cancer/find-a-clinical-trial/a-trial-looking-at-ozekibart-inbrx-109-for-people-with-chondrosarcoma-chondragon

---

## Figure 2 — Waterfall: Janux JANX007 (PSMAxCD3 TRACTr) in mCRPC

**REAL.** Janux Therapeutics and JANX007 (PSMA T-cell engager for mCRPC) are genuine. Data cutoff 15 Oct 2025; released 1 Dec 2025 (matches the "1 Dec 2025 deck" framing). The ≥2 mg subgroup n=85 matches the request exactly.

- **PSA50 (≥50% PSA reduction), ≥2 mg subgroup (n=85): 73%** — recovered.
- **PSA90 (≥90% PSA reduction), ≥2 mg subgroup (n=85): 26%** — recovered.
- **ORR (RECIST): 30% (8/27)** confirmed + unconfirmed partial responses among RECIST-evaluable patients treated at ≥2 mg — recovered.
- Context (not requested): rPFS 7.9–8.9 months across QW/Q2W expansions; 109 patients treated overall.

Citations:
- Janux PR, "Encouraging Efficacy and Safety Profile … JANX007 in mCRPC," 1 Dec 2025 (data cutoff 15 Oct 2025): https://investors.januxrx.com/investor-media/news/news-details/2025/Janux-Announces-Encouraging-Efficacy-and-Safety-Profile-from-Ongoing-Phase-1-Clinical-Trial-for-JANX007-in-mCRPC/default.aspx
- BusinessWire mirror: https://secure.businesswire.com/news/home/20251201540800/en/
- Targeted Oncology coverage: https://www.targetedonc.com/view/janx007-shows-promising-efficacy-safety-in-phase-1-trial-update

---

## Figure 3 — PK log-scale: Janux TROP2-TRACTr (R&D Day, July 2025)

**Program is REAL; the specific requested numbers appear SYNTHETIC / not publicly recoverable.** Janux held a Virtual R&D Day on 24 Jul 2025 and did present a preclinical TROP2-TRACTr program. But:

- **"Safety multiple / MTD threshold labeled 20 pM": NOT publicly recoverable.** Janux described the safety window only qualitatively — a "large safety multiple," with well-tolerated monkey exposures "well above the anticipated efficacious doses in humans," no CRS or healthy-tissue toxicity in NHP. No numeric "20 pM" threshold appears in the R&D Day release or materials I could access.
- **PK values for 0.03 and 0.3 mg/kg dose levels: NOT publicly recoverable.** TROP2-TRACTr was preclinical at the R&D Day (IND/Phase 1 planned for 2026), so there are no published human dose-level (0.03 / 0.3 mg/kg) PK values. These specific dose-level numbers were almost certainly constructed for the case study.

Citations:
- Janux R&D Day PR, 24 Jul 2025: https://investors.januxrx.com/investor-media/news/news-details/2025/Janux-Therapeutics-Highlights-Pipeline-Progress-and-Best-in-Class-Potential-of-Novel-Bispecific-Platform-for-Autoimmune-Diseases-at-Virtual-RD-Day/default.aspx
- TROP2-TRACTr program page (no PK/pM values disclosed): https://www.januxrx.com/trop2-tractr/

---

## Figure 4 — Forest plot: ChonDRAgon subgroup hazard ratios

**Trial is REAL (see Figure 1); the specific subgroup HRs are NOT publicly recoverable.** Public disclosures state only that benefit was "consistent across all chondrosarcoma subgroups, including IDH-wild-type and IDH-mutant" patients. No numeric per-subgroup hazard ratios (by IDH status, grade, disease site, prior lines, etc.) have been published. The overall HR ≈ 0.48 (52% risk reduction) is the only HR disclosed. Specific forest-plot subgroup HR values appear synthetic for the exercise.

Citations:
- Inhibrx topline PR, 23 Oct 2025 (same as Figure 1) — qualitative "consistent across subgroups" only.

---

## Figure 5 — Raster table: CG Oncology NMIBC comparator landscape

**All five trials are REAL and widely reported.** Requested values recovered below.

- **BOND-003 Cohort C (cretostimogene), 12-month CR: 46.4% (51/110).** Any-time CR 75.5% (83/110). n=110 (Lancet Oncology / AUA 2025 final results). Recovered.
  - https://ir.cgoncology.com/news-releases/news-release-details/cg-oncology-announces-publication-pivotal-phase-3-bond-003
  - AUA 2025 final results: https://www.auajournals.org/doi/10.1097/01.JU.0001111604.90306.91.02
- **SunRISe-1 (TAR-200 monotherapy, Cohort 2), CR: 82.4% (95% CI 72.6–89.8), n=85 treated.** Median DOR 25.8 months. (Note: this is any-time/best CR; some coverage cites 82.8%. Published JCO figure is 82.4%.) Recovered.
  - JCO (Phase IIb SunRISe-1): https://ascopubs.org/doi/10.1200/JCO-25-01651 · PubMed: https://pubmed.ncbi.nlm.nih.gov/40737582/
- **QUILT 3.032 (N-803 + BCG), 24-month DOR: 53.2%** of responders maintained CR beyond 24 months (Kaplan-Meier); median DOR 26.6 months (95% CI 0.9–NR). Recovered.
  - https://www.urologytimes.com/view/quilt-3-032-trial-anktiva-achieves-high-response-rate-in-bcg-unresponsive-nmibc
  - NCT03022825; NEJM Evidence publication (ImmunityBio IR): https://ir.immunitybio.com/news-releases/news-release-details/nejm-evidence-publishes-results-immunitybios-quilt-3032/
- **TAR-200 (SunRISe-1 Cohort 2) grade ≥3 treatment-related AEs: 12.9%** (grade ≥3 TRAE incidence; grade 3+ specific events ~9.4%, e.g. urinary tract pain 3.5%, UTI 1.2%). Recovered.
  - AJMC: https://www.ajmc.com/view/tar-200-monotherapy-has-82-8-complete-response-rate-in-bcg-unresponsive-hr-nmibc
  - JCO (same as above).

Additional comparators named in the table (nadofaragene firadenovec NCT02773849; pembrolizumab KEYNOTE-057) are also real trials; no specific values were requested for them.

---

## Figure 6 — Spider plot: Immatics IMA203CD8 in gynecologic cancers (ASCO 2026)

**REAL and matches the request.** IMA203CD8 (PRAME-directed TCR-T) gynecologic cohort, presented at 2026 ASCO Annual Meeting (PR 30 May 2026; data cutoff 30 Mar 2026). Efficacy-evaluable at ≥DL4c: n=19 (17 ovarian, 2 uterine) — matches "n=19."

- **ORR: 63% (12/19).** Recovered.
- **Confirmed ORR (cORR): 50% (9/18).** Recovered.
- **Complete responses: 4 total** (2 confirmed + 2 unconfirmed). Recovered.
- **Disease control rate (week 6): 68% (13/19).** Recovered.
- **Median PFS: not reached** (median follow-up 5.3 months). Recovered.
- **Median DOR: not reached;** 89% (8/9) of confirmed responses ongoing at cutoff, longest ongoing response 12 months post-infusion. Recovered.

Citations:
- Immatics PR, 30 May 2026: https://investors.immatics.com/news-releases/news-release-details/immatics-presents-clinical-activity-ima203cd8-prame-cell-therapy
- GlobeNewswire: https://www.globenewswire.com/news-release/2026/05/30/3303876/0/en/Immatics-Presents-Clinical-Activity-of-IMA203CD8-PRAME-Cell-Therapy-in-Hard-to-Treat-Gynecologic-Cancers-at-2026-ASCO-Annual-Meeting.html
