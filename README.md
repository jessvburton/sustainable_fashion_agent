# sustainable_fashion_agent

A [CrewAI](https://docs.crewai.com) project that checks a list of clothing product pages, records each item's brand, price and fabric composition, scores the fabric for sustainability, and reports the cheapest items that meet a fabric score threshold.

The aim is to find reasonably priced clothing made from more sustainable fabrics. Natural fibres (cotton, linen, hemp, wool, silk) rank best, regenerated cellulosics (viscose, modal, lyocell) sit in the middle, and synthetics (polyester, nylon, elastane) rank lowest.

> **Status:** early development. The first target is a small, hand-picked list of test URLs from a single retailer. It has not yet been run end to end.

## How it works

Three tasks run in order, managed by a Manager agent (hierarchical process):

| Step | Agent | What it does |
|---|---|---|
| 1. `collect_product_data` | ProductDataCollector | Fetches each supplied product URL and records the item name, brand, price, and the fabric composition text exactly as shown on the page. Marks `composition_found: false` rather than guessing. |
| 2. `analyse_fabric_composition` | FabricAnalyst | Turns the composition text into structured parts and percentages, then calls the `score_composition` tool. The agent does not do any scoring arithmetic itself. |
| 3. `present_results` | ReportPresenter | Writes a CSV of all items, a markdown report, and a ranked shortlist. |

The Manager delegates work, checks that every URL was processed, and sends back records that fail validation.

## Scoring model

Scores come from the `draft_score` column in `fabrics.csv`, which lists 38 fibres with aliases (for example, spandex and lycra both map to elastane), a tier and a note. Edit the CSV to change the scores; the tool picks up the changes.

| Tier | Examples | Draft score |
|---|---|---|
| Natural | cotton, linen, hemp, wool, silk | 100 |
| Regenerated cellulosic | lyocell, EcoVero viscose | 70 |
| Regenerated cellulosic | viscose, modal, cupro, bamboo | 60 |
| Semi-synthetic (cellulose derivative) | acetate, triacetate | 40 |
| Recycled synthetic | recycled polyester, recycled nylon | 25 |
| Synthetic | polyester, nylon, acrylic, elastane | 10 |

Rules the tool applies (`tools/score_composition.py`):

- **Outer fabric only.** An item's score is each fibre's score weighted by its percentage in the outer fabric. For example, 60% cotton / 40% polyester scores 64, and 100% viscose scores 60.
- **Lining and trim are mentioned, not scored.** Each item gets a short `lining_signal` and `trim_signal` (for example, "mostly synthetic (score 10): 100% polyester"). They appear in the results but never change the score or the ranking.
- **Flags instead of guesses.** Unknown fibres, fibres with no score, percentages that do not add up to 100 (within 1%), and an unclear outer part are flagged and left out of the shortlist.
- **Leather is handled separately.** It gets the status `leather_separate` and is not scored with the fibres.

Item statuses: `scored`, `flagged_unknown_fibre`, `flagged_unscored_fibre`, `flagged_percentages`, `flagged_unclear_outer`, `leather_separate`, `missing_composition`, `fetch_failed`.

## Setup

1. Install dependencies: `crewai install`
2. Create a `.env` file in the project root with your API key (the agents are set to Anthropic models; each agent's model is set in its file in `agents/`):

   ```
   ANTHROPIC_API_KEY=your-key-here
   ```

3. Make sure `fabrics.csv` is in the project root. The scoring tool looks there first, or at the path in the `FABRICS_CSV_PATH` environment variable.

## Running

```bash
crewai run
```

`crewai run` asks for any input that has no default. The inputs are:

| Input | Where it is set | Meaning |
|---|---|---|
| `product_urls` | prompted each run | The product page URLs to check |
| `item_type` | `crew.jsonc` | What you are looking for, for example `jumper` |
| `material_preference` | `crew.jsonc` | A note for the report, for example `wool` (does not change scores) |
| `size`, `fit` | `crew.jsonc` | Recorded if shown on the page |
| `min_fabric_score` | `crew.jsonc` | Minimum outer fabric score for the shortlist |

Each run is aimed at one item type, so the results are not dominated by cheap cotton socks.

## Outputs

- `results.csv`: every item and every field, including flagged items
- `report.md`: what was searched, how many items were scored and flagged, and the main findings
- `shortlist.md`: items meeting `min_fabric_score`, ranked by price (lowest first), with the fabric breakdown behind each score

## Project structure

- `agents/` - Agent definitions (JSONC): `ProductDataCollector`, `FabricAnalyst`, `ReportPresenter`, `Manager`
- `crew.jsonc` - Crew definition with tasks and configuration
- `tools/score_composition.py` - Deterministic fabric scoring tool
- `fabrics.csv` - Fibre classification list used by the scoring tool
- `knowledge/` - Knowledge files for agents (not used yet)

## Notes and limitations

- **Scraping.** The collector uses CrewAI's `ScrapeWebsiteTool`. As far as I know it reads static HTML, and many retail sites load much of their content with JavaScript, so test one URL from whichever site you use first and check that the composition text comes back. If it does not, alternatives are a browser-based tool or saving pages by hand.
- **Terms of use.** Check each site's terms before scraping. Keep runs small, use a supplied URL list, and do not crawl.
- **Draft scores.** The scores in `fabrics.csv` are a starting point for the scoring model, not a certified sustainability rating. Fabric is only one part of sustainability, and a brand-practices flag is a possible later addition.
- **Process.** Set `"process": "sequential"` in `crew.jsonc` to run the tasks in order without the Manager, which is simpler to debug.

## To do

- Run end to end on a handful of test URLs
- Decide how to handle leather
- Try smaller models for the worker agents to cut cost
- Optional: a simple brand sustainability flag

> **Note:** `custom:<name>` tool references execute `tools/<name>.py` as local
> Python code when the crew loads. Only run crew projects from sources you
> trust.
