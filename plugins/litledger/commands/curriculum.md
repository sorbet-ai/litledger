---
description: Read the literature on an idea and build a curriculum in litledger (staged readings, lineage with evidence, checkpoint questions)
argument-hint: <idea> [who it's for] [time budget]
---

The request: $ARGUMENTS

The idea is the first part of that request; anything after it says who the curriculum is for or how much time it should take, and replaces those defaults below. `<idea-slug>` below is the idea's name in lowercase with hyphens (e.g. delta-rule).

Build a curriculum for learning **the idea**, for a researcher or engineer who knows the basics of the field but not this idea: what to read, in what order and why, and how the idea
evolved. This is a reading job. Read the papers before you place them; a reading list assembled from titles and
abstracts is not a curriculum. Total reading time for the learner: 8 to 20 hours.

1. Start. `curriculum action=list`: if one for this idea exists, continue it instead of starting over.
2. Map the territory before writing anything.
   - `find query="…"` in this project, and with scope=library; try the idea's names and aliases. `entity action=list
     query="…"` shows concepts and methods already recorded.
   - `discover` for what's missing: the origin of the idea (it may be decades old), the paper that made it what it is
     today, surveys or tutorials, and the last 12 to 18 months.
   - Follow citations both ways: `graph work=<central paper> kind=references` for ancestors, `kind=citations` for
     descendants; `snowball` to keep a frontier across rounds.
   - Capture what you'll use: `resolve items=[…] add=true tags=["curriculum:<idea-slug>"] why="…"`.
3. Read. For each candidate (most important first): `read work=…` shows the outline; then read the sections that carry
   the idea: abstract, introduction, the method or derivation, and the related work, which says what the paper builds
   on. For each paper settle: what it adds to the idea, what a reader must know first, and what changed from its
   predecessors. Don't read whole papers when sections will do, and never paste long text into notes.
4. Record the lineage as you go: how each work relates to the earlier one it builds on.
   - Anchor the claim in the paper's own words: `note items=[{"work": "<newer>", "text": "…", "quote": "<verbatim
     sentence, e.g. 'we extend DeltaNet by…'>"}]`. The reply gives the note's id, and litledger checks the quote
     against the stored text.
   - Then `curriculum ops=[{"op": "lineage", "work": "<newer>", "builds_on": "<older>", "relation": "…",
     "change": "one line: what changed", "evidence": "<note id>"}]`.
   - relation is one of: extends, generalizes, simplifies, fixes, reinterprets, outperforms, contradicts, compares_to, instance_of, motivates.
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
   `curriculum title="the idea" idea="2–3 sentences: what the learner will understand at the end" audience="…"
   intro="the story of the idea in 5–8 sentences" ops=[{"op": "stage", "key": "found", "title": "…", "goal": "…",
   "checkpoints": ["…"]}, {"op": "read", "stage": "found", "work": "<citekey>", "why": "…", "focus": "…",
   "depth": "read", "minutes": 40, "concepts": ["…"]}, …]`
   Keys let reads in the same call name their stage. Later calls name the curriculum (`curriculum="cur:<idea-slug>"`) and
   use the ids the reply gives (s1, r3, …).
7. Review: `curriculum action=get curriculum="cur:<idea-slug>"`. Every reading needs a why and a focus, every stage
   checkpoints, and every lineage step evidence. Fix what's missing.
8. Reply with a short summary: the stages, the total time, and anything you couldn't read (no full text, paywalled),
   so the learner knows where the curriculum is thinner. People see it in the web UI under Curricula.
