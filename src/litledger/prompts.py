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


def build_curriculum(idea: str, audience: str = "", hours: str = "", slug: str = "") -> str:
    from .curricula import LINEAGE
    from .textutil import title_slug

    slug = slug or title_slug(idea) or "idea"
    who = audience or "a researcher or engineer who knows the basics of the field but not this idea"
    budget = f"about {hours} hours" if hours else "8 to 20 hours"
    return f"""Build a curriculum for learning **{idea}**, for {who}: what to read, in what order and why, and how the idea
evolved. This is a reading job. Read the papers before you place them; a reading list assembled from titles and
abstracts is not a curriculum. Total reading time for the learner: {budget}.

1. Start. `curriculum action=list`: if one for this idea exists, continue it instead of starting over.
2. Map the territory before writing anything.
   - `find query="…"` in this project, and with scope=library; try the idea's names and aliases. `entity action=list
     query="…"` shows concepts and methods already recorded.
   - `discover` for what's missing: the origin of the idea (it may be decades old), the paper that made it what it is
     today, surveys or tutorials, and the last 12 to 18 months.
   - Follow citations both ways: `graph work=<central paper> kind=references` for ancestors, `kind=citations` for
     descendants; `snowball` to keep a frontier across rounds.
   - Capture what you'll use: `resolve items=[…] add=true tags=["curriculum:{slug}"] why="…"`.
3. Read. For each candidate (most important first): `read work=…` shows the outline; then read the sections that carry
   the idea: abstract, introduction, the method or derivation, and the related work, which says what the paper builds
   on. For each paper settle: what it adds to the idea, what a reader must know first, and what changed from its
   predecessors. Don't read whole papers when sections will do, and never paste long text into notes.
4. Record the lineage as you go: how each work relates to the earlier one it builds on.
   - `curriculum ops=[{{"op": "lineage", "work": "<newer>", "builds_on": "<older>", "relation": "…",
     "change": "one line: what changed", "quote": "<the newer paper's own sentence that says so, verbatim, e.g.
     'we extend DeltaNet by…'>"}}]`. litledger checks the quote against the stored text and keeps it as the step's
     evidence; the reply says when a quote wasn't found.
   - relation is one of: {", ".join(LINEAGE)}.
   - Only claim what the papers support. A step you can't anchor in a quote gets no evidence and is reported as such.
5. Design the course, 4 to 6 stages in order. Typically:
   - Foundations: what the learner must know first, kept short.
   - The core idea: the paper or two that define it, read closely.
   - How it developed: variants and improvements, in the order they happened.
   - Making it work: efficiency, hardware or scale, if that's part of the story.
   - The frontier: the newest work.
   - Open problems: limitations and questions, optional.
   Each stage gets a goal ("after this stage you can …"), 2 to 6 readings in reading order, and 2 to 4 checkpoint
   questions that test understanding rather than recall. Each reading gets:
   - why: what it teaches, and why it comes at this point.
   - focus: exact sections, equations or figures, e.g. "§3.2 and Eq. 4–6; skip §5".
   - depth: study (the core), read (most), skim (context).
   - minutes: realistic.
   - concepts: the 2 to 4 ideas it introduces.
   Prefer fewer, better readings: one paper per step in the story, a good survey or tutorial if one exists, nothing
   redundant.
6. Write it in one or a few calls:
   `curriculum title="{idea}" idea="2–3 sentences: what the learner will understand at the end" audience="…"
   intro="the story of the idea in 5–8 sentences" ops=[{{"op": "stage", "key": "found", "title": "…", "goal": "…",
   "checkpoints": ["…"]}}, {{"op": "read", "stage": "found", "work": "<citekey>", "why": "…", "focus": "…",
   "depth": "read", "minutes": 40, "concepts": ["…"]}}, …]`
   Keys let reads in the same call name their stage. Later calls name the curriculum (`curriculum="cur:{slug}"`) and
   use the ids the reply gives (s1, r3, …).
7. Review: `curriculum action=get curriculum="cur:{slug}"`. Every reading needs a why and a focus, every stage
   checkpoints, and every lineage step evidence. Fix what's missing.
8. Reply with a short summary: the stages, the total time, and anything you couldn't read (no full text, paywalled),
   so the learner knows where the curriculum is thinner. People see it in the web UI under Curricula."""


def curriculum_command() -> str:
    """The Claude Code plugin's /litledger:curriculum command: the same brief, for whatever the user types after it."""
    return ("---\ndescription: Read the literature on an idea and build a curriculum in litledger (staged readings, "
            "lineage with evidence, checkpoint questions)\nargument-hint: <idea> [who it's for] [time budget]\n---\n\n"
            "The request: $ARGUMENTS\n\nThe idea is the first part of that request; anything after it says who the "
            "curriculum is for or how much time it should take, and replaces those defaults below. `<idea-slug>` below is the idea's name in lowercase with "
            "hyphens (e.g. delta-rule).\n\n"
            + build_curriculum("the idea", slug="<idea-slug>") + "\n")


BRIEFS = {"extract_paper": (extract_paper, "Read a paper and record its methods, datasets, benchmarks, metrics, results, claims and limitations",
                            ["work", "focus"]),
          "build_curriculum": (build_curriculum, "Read the literature on an idea and build a curriculum: staged readings, "
                                                 "lineage with evidence, checkpoint questions", ["idea", "audience", "hours"])}
