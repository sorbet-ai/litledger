# 09 - Knowledge schema survey and recommendation for litledger

Date: 2026-10-06. Purpose: choose typed knowledge entities and relations that a Sonnet subagent can extract from one paper in one pass and a human can arrange on a mind map.

Verification note: items marked (verified) were confirmed via search/fetch this session. Items marked (unverified) come from background knowledge; searches for Miro, Kosmik, Scrintal, Litmaps and Research Rabbit templates returned nothing specific, so claims about them are not made.

## 1. Existing schemas

### SciERC (verified)
- Entity types (6): Task, Method, Metric, Material, OtherScientificTerm, Generic.
- Relation types (7): Used-for, Feature-of, Hyponym-of, Part-of, Compare, Conjunction, Evaluate-for.
- Plus coreference clusters. Sentence/abstract level, so there is no notion of a result value.
- Takeaway: Task/Method/Metric/Material is the stable core. Hyponym-of and Part-of map to instance_of / part_of. "Generic" (e.g. "our approach") is an extraction artifact and not worth storing.
- https://arxiv.org/pdf/1808.09602

### SciREX (verified)
- Document-level. Entities: Dataset, Metric, Task, Method (salient only). Core relation is a 4-ary tuple (Dataset, Metric, Task, Method), i.e. a leaderboard cell, with coreference clusters across the whole paper.
- Takeaway: a "result" is an n-ary object. Do not model it as a binary edge. Store it as a node with fields and edges to its dataset/metric/method.
- https://arxiv.org/pdf/2005.00512 , https://github.com/allenai/SciREX

### Papers with Code (verified; shut down)
- Meta sunset it 24-25 July 2025. The domain now redirects to Hugging Face Trending Papers. Roughly 9.3k leaderboards, 80k paper-code links and 5.6k datasets were no longer served. Community dumps survive (evaluation-tables JSON, `pwc-archive` on Hugging Face).
- Data model (verified via client docs and exports): Task (hierarchical parent/child), Dataset, Metric (name, description, is_loss), evaluation table row (model_name, paper, metrics dict, methodology, uses_additional_data, best_rank), SOTA table per (task, dataset). Results tuple = (task, dataset, metric name, metric value) tied to table/row/cell.
- Takeaway: do not depend on PwC as a live source. Reuse its shape and, optionally, seed canonical dataset/task names from the archive. The `is_loss` flag, which means higher-is-better, and "uses extra data" are worth copying.
- https://www.codesota.com/papers-with-code/shutdown , https://paperswithcode-client.readthedocs.io/en/latest/api/models/evaluation.html , https://arxiv.org/pdf/2004.14356 (AxCell)

### ORKG (verified)
- A paper has contributions. A contribution addresses a research problem and is described by materials/methods and results. Templates (sets of predicates per problem) standardize descriptions. Comparisons align contributions side by side. ORKG Benchmarks use Task, Dataset, Metric, Model, Code. ORKG-Leaderboards extract (Task, Dataset, Metric) triples.
- Takeaway: "contribution" is a useful per-paper wrapper, and the comparison table is the human payoff. A litledger comparison view over Result entities gives this for free.
- https://arxiv.org/pdf/2206.01439 , https://arxiv.org/pdf/2305.11068

### CS-KG / AI-KG (verified)
- Five classes: Task, Method, Material, Metric, OtherEntity. Relations are a large verb-derived vocabulary (about 179 in earlier versions; categories like based-on, uses, improves, includes, affects, proposes, limitation), each with active and passive forms. CS-KG 2.0 is 25M entities and 67M relations from 15M papers.
- Takeaway: the same four-class core as SciERC. The long verb vocabulary is an automated-extraction artifact, too noisy for a human-curated palette. Keep roughly 20 relations.
- https://scholkg.kmi.open.ac.uk/cskg/ontology.html , https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12149285/

### Claim / evidence datasets (verified)
- SciClaim: graph schema with entity spans as nodes, relations as edges, and attributes (qualifications, subtype, evidence). Covers causal, comparative, predictive, statistical and proportional claims over variables. https://arxiv.org/pdf/2109.10453
- SciFact: claim vs abstract labelled Supports / Refutes / NoInfo, with sentence-level rationales. (From search summary.)
- Takeaway: claims carry strength and qualifiers (scope, hedging). Support is a graded verdict with a rationale span.

### Discourse graphs (verified)
- Joel Chan et al. Default node types Question, Claim, Evidence, Source (the Obsidian plugin also allows custom types such as Hypothesis, Experiment). Relations supports, opposes, informs (and "is answered by" per Chan's writeups). Evidence is an atomic observation from a Source. Claims are synthesized by the researcher.
- The Roam/Obsidian plugin is explicitly built so graph authoring falls out of note-taking and mind-map-like canvases.
- https://community.obsidian.md/plugins/discourse-graphs , https://github.com/DiscourseGraphs/obsidian-lab-example , https://www.obsidianstats.com/plugins/discourse-graphs

### Zettelkasten (verified)
- Note types: fleeting, literature, permanent (atomic, own words), structure/index notes (and project notes per Ahrens).
- Takeaway: a literature note is per source and a permanent note is a claim or idea in your own words. Structure notes correspond to mind-map groupings. The "permanent note" is our Claim/Idea, project-scoped.
- https://notes.dsebastien.net/30+Areas/33+Permanent+notes/33.02+Content/Zettelkasten+method

### Argument mapping (verified)
- Toulmin: Claim, Grounds (data), Warrant, Backing, Qualifier, Rebuttal.
- IBIS: Issue (question), Position (idea), Argument (pro or con).
- Takeaway: Issue/Position/Argument is isomorphic to Question/Claim/Evidence. Toulmin's Qualifier belongs on a claim as a field, and Rebuttal is covered by `contradicts`. Warrant/Backing are too fine-grained for extraction.
- https://courses.lumenlearning.com/suny-jefferson-collegecomposition/?p=417 , https://eight2late.com/2009/04/07/issues-ideas-and-arguments-a-communication-centric-approach-to-tackling-project-complexity

## 2. What researchers put on maps in practice

Evidence is thinner than for formal schemas. Sources are tool docs and workflow guides, not rigorous surveys.

- Obsidian research workflows (verified): literature note per paper, concept notes for theories/methods, a project note with the research question; canvas with paper nodes plus Literature Overview, Method Taxonomy, Research Gaps, Claim Map; suggested edges paper->method family, paper->dataset, paper->gap/limitation, gap->experiment direction. https://aura-lab.siue.edu/intro-to-obsidian/10-research-workflow.html , https://tessl.io/registry/skills/github/Galaxy-Dawn/claude-scholar/obsidian-literature-workflow
- Heptabase (verified, thin): cards on whiteboards, whiteboard as the unit of thinking, tags/properties for homogeneous literature cards, table/kanban views of the same cards. https://wiki.heptabase.com/use-case-and-workflow
- Single-paper literature maps (verified): main argument, research question, theoretical background, methodology, findings, limitations, implications; across papers: themes, comparisons, gaps. https://mapify.so/blog/research-paper-to-literature-map , https://creately.com/diagram/example/jv3cihp53/literature-review-map-template
- Dissertation mind-map templates (verified): radial branches of themes, literature, methods, arguments. https://templates.xmind.com/templates/dissertation-zb3d

Recurring node types across these: question, concept, claim/finding, method, dataset, gap, limitation, future work/next experiment, hypothesis, idea, and the paper itself. People and labs appear rarely as nodes (they are usually metadata or filters), so treat them as optional and not in the palette. Benchmarks and baselines are ML-specific and come from section 3.

Design implication: the two main layers are (a) literature facts that an agent can extract and (b) the researcher's own questions, hypotheses, gaps, ideas and experiments. Maps mostly mix them. Gaps and next experiments are the connective tissue linking literature to own work.

## 3. ML-specific fields

- Result (from PwC, SciREX, ORKG-Leaderboards, MetaLead https://arxiv.org/pdf/2601.22420): value (numeric) plus raw string, unit, metric, dataset/benchmark, split (test/dev/val), setting (zero-shot, few-shot k, fine-tuned, prompt/decoding), compute (hardware, GPU-hours, training tokens), model size (params), role (baseline vs proposed vs ablation), higher_is_better, confidence interval/std/seeds, uses extra data, location in paper (table/figure/page), and whether it is reproduced or only copied from another paper.
- Dataset (HF dataset cards https://huggingface.co/docs/hub/en/datasets-cards, Datasheets for Datasets): size (examples, splits), modality, language, task categories, license, URL, creation method, version, aliases.
- Benchmark: a suite or leaderboard defined by tasks, datasets, metrics and protocol. Can be one dataset (GLUE is a suite of several). Fields: tasks, metrics, official URL, protocol notes.
- Method/model: aliases, family, introduced-in paper, parameter count, code URL, weights URL, architecture type, training data.
- Metric: name, direction (higher_is_better), range, definition.

## 4. Relation vocabulary

Paper -> entity (extracted, each carries a quote/location):
introduces, uses, evaluates_on, reports, states (claim), raises (question), notes_limitation / future_work (as paper->limitation).

Entity -> entity:
- Structural: instance_of (is-a, Hyponym-of), part_of, extends (method builds on method), variant_of / alias_of (dedupe, may live as alias field instead).
- Experimental: tested_on (method->dataset/benchmark), measured_by (benchmark/result->metric), baseline_for (method->result or method), outperforms (result->result, with delta), improves_on (method->method).
- Argument: supports, contradicts (with strength), answers (claim/result->question), addresses (method/paper->problem/question), motivates (gap/question->method/experiment), tests (experiment->hypothesis), limits (limitation->claim/method).

Mapping to existing vocabularies: supports/opposes/informs from discourse graphs (opposes renamed contradicts); Used-for -> uses/addresses; Evaluate-for -> evaluates_on/measured_by; Compare -> outperforms/baseline_for; Hyponym-of -> instance_of; Part-of -> part_of; Feature-of is absorbed into fields.

## 5. Recommended litledger schema

Scope key: LIB = library-wide fact about the literature (extracted, shared across projects, deduplicated); PRJ = project-scoped, the researcher's own thinking.

### Entity kinds (14)

| # | Kind | Scope | Definition | Key fields |
|---|------|-------|-----------|-----------|
| 1 | Paper | LIB | A source document | title, authors, year, venue, ids (arXiv/DOI), url, abstract_summary |
| 2 | Problem | LIB | Task or research problem a paper addresses | name, aliases, parent_problem |
| 3 | Method | LIB | Approach, model, algorithm, training recipe | name, aliases, family, introduced_in, params, code_url, summary |
| 4 | Dataset | LIB | Data resource | name, aliases, size, splits, modality, language, license, url, version |
| 5 | Benchmark | LIB | Standard evaluation suite/leaderboard | name, tasks, datasets, metrics, protocol, url |
| 6 | Metric | LIB | Evaluation measure | name, higher_is_better, definition, range |
| 7 | Result | LIB | One reported number (SciREX tuple as a node) | value, raw_text, unit, metric, dataset/benchmark, split, setting, compute, model_size, role (baseline/proposed/ablation/reference), higher_is_better, std/ci, source_loc, copied_from |
| 8 | Claim | LIB | Assertion a paper makes, with its stated evidence | text, kind (empirical/causal/comparative/theoretical), qualifier/scope, strength (stated), quote, source_loc |
| 9 | Limitation | LIB | Weakness or caveat the paper states, or the extractor notes | text, kind (data/method/eval/scope), stated_by (author/agent), source_loc |
| 10 | Concept | LIB (shared) / PRJ if user-created | Idea, term, phenomenon, technique category | name, aliases, definition |
| 11 | Question | PRJ (can be LIB if paper-posed) | Open research question | text, status (open/answered/dropped) |
| 12 | Hypothesis | PRJ | Testable conjecture of the researcher | text, status (untested/supported/refuted), confidence |
| 13 | Gap | PRJ | Missing piece the researcher identifies in the literature | text, why_it_matters, priority |
| 14 | Idea | PRJ | Researcher's own proposal or insight (permanent-note analogue) | text, status |
| 15 | Experiment | PRJ | Planned or run test | goal, setup, status, outcome_note |
| 16 | Note | PRJ | Free-text or structure/group node for the map | text, tags |

Count is 16 including Note. Evidence is deliberately not a separate kind: a Result or a quoted Claim span plays that role, and `supports` edges carry a quote. Future work extracted from a paper is stored as a Limitation (kind=future_work) or as a Question.

### Relations (19)

| Relation | Domain -> Range | Meaning |
|----------|-----------------|---------|
| introduces | Paper -> Method/Dataset/Benchmark/Metric/Concept | Paper first presents this entity |
| uses | Paper/Method -> Method/Dataset/Concept | Relies on it as a component or resource |
| addresses | Paper/Method -> Problem/Question | Targets this problem |
| evaluates_on | Paper/Method -> Dataset/Benchmark | Tested on it |
| reports | Paper -> Result/Claim/Limitation | Paper contains this item (provenance edge) |
| raises | Paper -> Question/Gap | Paper poses or exposes it |
| result_of | Result -> Method | The method that produced the number |
| measured_on | Result -> Dataset/Benchmark | Where it was measured |
| measured_by | Result -> Metric | Which metric |
| baseline_for | Method -> Result/Method | Method served as a comparison point |
| outperforms | Result -> Result, Method -> Method | Better under same metric/dataset; carries delta |
| extends | Method -> Method, Concept -> Concept | Builds on or specializes |
| instance_of | Method/Dataset/Problem/Concept -> same kind | Is-a (taxonomy) |
| part_of | any LIB entity -> same kind | Component or subset (e.g. dataset split) |
| supports | Result/Claim/Paper -> Claim/Hypothesis | Provides evidence for (quote, strength) |
| contradicts | Result/Claim/Paper -> Claim/Hypothesis | Conflicts with (quote, strength) |
| answers | Claim/Result/Paper -> Question | Gives (partial) answer |
| motivates | Gap/Limitation/Question -> Idea/Experiment/Method | Is the reason for it |
| tests | Experiment -> Hypothesis/Claim | Experiment designed to check it |
| limits | Limitation -> Claim/Method/Result | Caveat applies to it |

Paper->entity edges (introduces, uses, addresses, evaluates_on, reports, raises) are what the extraction agent emits. Everything below them is either extracted from one paper's tables (the result_of / measured_* group) or authored by the human on the map (supports/contradicts across papers, answers, motivates, tests, extends beyond what the paper states). An `origin` field on every edge (agent / human) and a `confidence` on agent edges keep that distinction visible. Aliases and dedupe (alias_of) are handled as a field plus a merge action instead of a relation.

### Practical rules for single-paper extraction
1. Emit Paper->entity edges first and Result nodes only for numbers in main tables or the abstract. Cap results per paper and tag role=baseline vs proposed.
2. Resolve entities against existing LIB entities by name/alias before creating new ones.
3. Every Claim/Result/Limitation stores a verbatim quote and location so a human can verify.
4. Agent never creates PRJ kinds except as suggestions (proposed Gap/Question) awaiting human acceptance.
5. Map palette: color by scope (LIB muted, PRJ saturated), shape by kind; a Result renders as a compact card showing metric, value, dataset.

### Open choices
- Whether Benchmark is separate from Dataset. This design keeps both because many results are quoted against suites (e.g. MMLU subsets, SWE-bench variants); a user may drop Benchmark and use Dataset only.
- Whether People/Lab nodes are needed. Not recommended for v1; use author fields on Paper.
