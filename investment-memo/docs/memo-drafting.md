# Memo drafting: what's left to produce the investment memo

Status as of this writing: ingestion (9 sources), RAG (BM25 + structured + date filtering),
five analysis modules (MoA, PoS, regulatory, peak sales, price) each emitting typed cited
claims, and an eval suite (retrieval + extraction deterministic across 5 companies;
faithfulness + quality harnesses, uncalibrated) are built. This doc covers the layer that
does not exist yet: turning claims into an actual, structured, figure-bearing memo.

The gap in one line: we have modules that emit cited claims; we need the layer that turns
claims into a written, figure-bearing memo, plus the synthesis that forms the thesis.

## Structural pieces to build

1. **Assembly orchestrator.** The headless "produce the memo for company X" entry point the
   brief grades ("run it for the chosen company and submit the memorandum it produces").
   Runs all five modules, collects claims into the ledger (`claims` + `evidence` tables),
   then drives composition. No human in the loop between initiation and finished output.

2. **Memo structure / schema.** The document skeleton and the map of which claims feed each
   section. Proposed sections:
   - Executive summary / thesis
   - Company & pipeline overview
   - MoA (does it work)
   - PoS (will it be approved)
   - Regulatory path (how it gets approved)
   - Peak sales (commercial opportunity)
   - Valuation & price-vs-thesis
   - Risks
   - Catalysts
   - Sources / appendix
   Each section is a view over the relevant module's claims.

3. **Synthesis / thesis layer.** The piece no single module produces and the heart of the
   brief: what the market prices and where our view departs. Reasons across modules
   (PoS x peak sales -> rNPV -> compare to the value the price implies -> recommendation).
   New reasoning step; the hardest prompt in the system.

4. **Section drafting / rendering.** Turn structured claims into institutional prose, per
   section, with inline citations, adding no facts beyond the claims. We committed to "prose
   rendered from the ledger," so this is a real drafting layer, not free-writing. The memo is
   a view of the ledger; the ledger stays the source of truth.

5. **Figures: extract -> insert -> annotate.** The brief explicitly requires pulling figures
   from company decks, inserting them at the claim, and drawing the arrow/box/callout on the
   exact feature the argument rests on. Currently none of this exists: no deck ingestion, no
   figure-to-claim matching, no image annotation. Biggest single gap; its own workstream.

6. **Valuation math (deterministic).** rNPV assembly (peak sales x PoS, discounted) in code,
   feeding the thesis. Partly blocked on the price side (no free market-cap/consensus feed),
   so enterprise value stays conditional, as the price module already flags. Keep arithmetic
   in code, not the model (same discipline as the peak-sales model).

7. **Output artifact + refusal.** Render to a real document (HTML/PDF/DOCX) with annotated
   figures embedded, and a refusal path that declines with reasons when required sections
   lack evidence. The web app is a separate, later surface over the same ledger.

## Prompt engineering

This is where much of the quality lives.

- **Shared house-style system prompt (the system prompt).** One stable, cached prompt that
  governs all drafting: the analyst persona and standing rules — grounded-and-cited only,
  calibrated hedging, institutional register, no hype, state absence rather than guess.
  Applied everywhere.
- **New task prompts** (do not exist yet):
  - *Thesis / synthesis*: reason over all modules to a recommendation + where-we-depart. Hard.
  - *Section drafting*: claims -> grounded prose with citations; likely per-section variants
    for the different registers of MoA vs valuation.
  - *Executive summary*: distill the whole memo.
  - *Figure selection / annotation*: which figure supports a claim, what feature to mark.
  - *Refusal decision*: is the evidence sufficient to write this section honestly.
  - *In-loop verifier* (optional but the brief values it): one component adversarially checks
    another before it ships, distinct from the faithfulness eval.
- **Existing module prompts** (MoA/PoS/regulatory/peak-sales/price) are built but were tuned
  in isolation. They need a consistency pass against the house style, and the atomic-claim +
  evidence-only discipline that fixed MoA (0.41 -> ~1.0 faithfulness) should be uniform.
- **Prompt-cache structure**: stable house style + rubric as the cached prefix; volatile
  per-section content after the last breakpoint, so a full memo run stays affordable.

## Hard dependencies to decide

- **Figures need decks.** The insert-and-annotate requirement depends on ingesting IR/data-
  deck figures, rated Hard in the feasibility report (JS sites, PDFs). Options: a hand-seeded
  per-company figure manifest, or a scoped scraper for the chosen company. Most likely to
  constrain how fully we meet the brief.
- **Price / consensus is data-starved.** No free market-cap or consensus feed, so valuation
  and "where we depart from the street" stay partly reconstructed / assumption-based. Being
  explicit about that gap is itself a point in the memo's favor.

## Suggested order

1. Freeze the memo structure + house-style system prompt (small, high-leverage, unblocks
   everything).
2. Build the assembly orchestrator (modules -> ledger -> rendered sections) with the
   section-drafting prompt, producing a text-only memo first.
3. Add the thesis / synthesis layer + deterministic rNPV.
4. Then figures (decide seed-vs-scrape), then refusal, then the web app.

The analytical inputs are largely done; what remains is composition, the cross-module thesis,
and figures. The prompt work is concentrated in the house-style prompt plus the thesis and
drafting prompts.
