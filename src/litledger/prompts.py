"""Agent briefs (MCP prompts / `litledger brief`): repeatable jobs to hand to a subagent, e.g. 'read this paper and
fill in its knowledge'."""
from __future__ import annotations

from .knowledge import KINDS, RELATIONS

PROJECT_KINDS = [k for k, v in KINDS.items() if v.scope == "project"]


def extract_paper(work: str, focus: str = "") -> str:
    kinds = "\n".join(f"   - {k}: {v.meaning}" + (f" (data: {', '.join(f for f in v.fields if f != 'aliases')})" if [f for f in v.fields if f != 'aliases'] else "")
                      for k, v in KINDS.items() if v.scope == "library")
    rels = ", ".join(r for r in RELATIONS)
    focus_line = f"\nFocus on what matters for: {focus}\n" if focus else ""
    return f"""Extract the knowledge in paper `{work}` into the litledger ledger.
{focus_line}
1. `read work={work}` shows the outline, including how many tables the stored text has. Read the abstract,
   introduction, method, experiments and limitations with `read mode=section target="3.1"`; read result tables with
   `read mode=section target="Table 2"` (rows are cells joined by " | "). Don't read the whole paper at once.
2. `entity action=list kind=<kind>` (or query=…) shows what already exists. Reuse those titles; items are matched on
   title and aliases, so the same benchmark in another paper merges instead of duplicating.
3. Write everything in ONE `entity` call. Each item is linked to the paper with `from` and `rel`:
{kinds}
   rel (paper → item): introduces (its own new method/dataset/benchmark/concept), uses, evaluates_on, reports (results),
   addresses (tasks), raises (limitations, open questions).
   Between items use `links: [{{relation, target}}]`; target may be an item id (method:deltanet), an exact title or
   alias of an item (also one created earlier in the same call), or a citekey. Relations: {rels}.
   Example item: {{"kind": "method", "title": "Gated DeltaNet", "aliases": ["GDN"], "from": "{work}", "rel": "introduces",
                  "links": [{{"relation": "extends", "target": "DeltaNet"}}]}}
4. Results: only numbers printed in the paper's tables or abstract. One item per method × benchmark × metric (× setting):
   {{"kind": "result", "from": "{work}", "data": {{"method": "Gated DeltaNet", "benchmark": "S-NIAH-1", "metric": "accuracy",
   "value": 91.8, "setting": "8K", "role": "proposed", "higher_is_better": true, "source": "Table 2"}},
   "links": [{{"relation": "result_of", "target": "Gated DeltaNet"}}, {{"relation": "measured_on", "target": "S-NIAH-1"}}]}}
   Use the benchmark item's exact title. When a table splits a suite into sub-benchmarks (S-NIAH-1/2/3), make each
   its own benchmark item linked `part_of` the suite; lengths, sizes and shots go in `setting`.
   For big tables record the main comparison (the paper's method and the strongest baselines), not every cell.
5. Claims and limitations: put the verbatim sentence in data.quote and its location in data.source. For key claims
   also add `note items=[{{"work": "{work}", "text": "…", "quote": "<verbatim sentence>"}}]` — litledger verifies the
   quote against the stored text.
6. Don't create {', '.join(PROJECT_KINDS)} items — those are the user's own thinking. Mention any clear open question
   or gap in your final reply instead.
7. Check the reply: it reports "links N added, M FAILED" — fix failed links with a follow-up call.
   Optionally build a map: `map_edit title="{work}" ops=[{{"op": "expand", "ref": "{work}"}}]`.

Keep calls batched; never paste long paper text into notes."""


BRIEFS = {"extract_paper": (extract_paper, "Read a paper and record its methods, datasets, benchmarks, metrics, results, claims and limitations",
                            ["work", "focus"])}
