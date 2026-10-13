"""Curricula: stages and readings by key, per-op isolation, lineage as evidenced links, reordering, the agent's outline,
the learner's syllabus, the REST view with reading progress, and the brief."""
import pytest
from fastapi.testclient import TestClient

from litledger import curricula
from litledger.prompts import BRIEFS
from litledger.server.app import create_app

from conftest import call, make_ledger


@pytest.fixture
def client(tmp_path):
    lg = make_ledger(tmp_path)
    with TestClient(create_app(ledger=lg)) as c:
        c.headers.update({"Authorization": f"Bearer {(lg.settings.token_dir / 'admin').read_text().strip()}"})
        yield c


def build(lg, actor):
    call(lg, actor, "resolve", items=["2412.06464", "2006.16236"], add=True)
    return call(lg, actor, "curriculum", title="Delta rule", idea="How fast-weight memories learn with the delta rule.",
                audience="ML engineers who know attention", ops=[
                    {"op": "stage", "key": "found", "title": "Foundations", "goal": "Explain linear attention as a fast-weight memory.",
                     "checkpoints": ["Why is linear attention an RNN?", "What does the state matrix store?"]},
                    {"op": "stage", "key": "dev", "title": "How it developed", "goal": "Explain what gating adds."},
                    {"op": "read", "stage": "found", "work": "katharopoulos2020transformers", "why": "Linear attention as an RNN.",
                     "focus": "§3.2", "depth": "study", "minutes": 60, "concepts": ["linear attention", "kernel feature map"]},
                    {"op": "read", "stage": "dev", "work": "yang2024gated", "why": "Gating plus the delta rule.", "minutes": 50},
                    {"op": "read", "stage": "nope", "work": "yang2024gated", "why": "x"},
                    {"op": "read", "stage": "found", "work": "nobody2099", "why": "x"},
                    {"op": "teach", "id": "s1"},
                ])


def test_curriculum_from_stages_and_readings(lg, actor):
    out = build(lg, actor)
    assert out.startswith("ok cur:delta-rule · added s1 s2 r1 r2 · applied 4"), out
    assert "op 4 (read): unknown stage 'nope'" in out and "op 5 (read): unknown work 'nobody2099'" in out and "op 6: op must be" in out
    outline = call(lg, actor, "curriculum", curriculum="cur:delta-rule")
    assert outline.splitlines()[0] == "cur:delta-rule · Delta rule · 2 stages, 2 readings, ~110 min"
    assert "  r1 katharopoulos2020transformers (2020) [study, 60m] Linear attention as an RNN. | focus: §3.2" in outline
    assert "  r2 yang2024gated" in outline and "[read, 50m]" in outline  # depth defaults to read
    assert "checkpoints: 2" in outline
    assert "1 curricula" in call(lg, actor, "curriculum", action="list")


def test_a_work_is_a_reading_once_and_readings_join_the_project(lg, actor):
    build(lg, actor)
    out = call(lg, actor, "curriculum", curriculum="Delta rule", ops=[{"op": "read", "stage": "s1", "work": "yang2024gated", "why": "again"}])
    assert "already a reading here" in out
    data = curricula.get(lg, actor.project, "cur:delta-rule")
    assert {r["work"]["citekey"] for s in data["stages"] for r in s["readings"]} == {"katharopoulos2020transformers", "yang2024gated"}


def test_lineage_is_an_evidenced_link(lg, actor):
    build(lg, actor)
    note = call(lg, actor, "note", items=[{"work": "yang2024gated", "text": "Gated DeltaNet builds on linear attention's recurrent form."}])
    assert note.startswith("ok 1/1"), note
    with lg.db.read() as conn:
        full = conn.execute("SELECT id FROM notes ORDER BY created_at DESC LIMIT 1").fetchone()["id"]
    out = call(lg, actor, "curriculum", curriculum="cur:delta-rule", ops=[
        {"op": "lineage", "work": "yang2024gated", "builds_on": "katharopoulos2020transformers", "relation": "extends",
         "change": "adds a gate and the delta rule to linear attention's state", "evidence": full},
        {"op": "lineage", "work": "yang2024gated", "builds_on": "katharopoulos2020transformers", "relation": "copies"},
    ])
    assert "applied 1" in out and "relation must be one of" in out
    data = curricula.get(lg, actor.project, "cur:delta-rule")
    [step] = data["lineage"]
    assert step["relation"] == "extends" and step["change"].startswith("adds a gate") and "recurrent form" in step["evidence"]
    assert "yang2024gated extends katharopoulos2020transformers: adds a gate" in curricula.outline(data)
    # it's an ordinary link, so the evidence graph knows it too
    assert "yang2024gated -extends/adds a gate" in call(lg, actor, "graph", work="yang2024gated", kind="evidence")


def test_lineage_takes_a_quote_or_the_short_note_id(lg, actor):
    build(lg, actor)
    out = call(lg, actor, "curriculum", curriculum="cur:delta-rule", ops=[
        {"op": "lineage", "work": "yang2024gated", "builds_on": "katharopoulos2020transformers", "relation": "extends",
         "change": "adds a gate", "quote": "We combine gating with the delta rule."}])
    assert "applied 1" in out and "kept as unverified evidence" in out, out  # no stored text here to check it against
    [step] = curricula.get(lg, actor.project, "cur:delta-rule")["lineage"]
    assert "gating with the delta rule" in step["evidence"]
    # the 10-character id the note tool prints is enough to name a note as evidence
    call(lg, actor, "note", items=[{"work": "katharopoulos2020transformers", "text": "Linear attention is an RNN."}])
    with lg.db.read() as conn:
        full = conn.execute("SELECT id FROM notes WHERE text LIKE 'Linear attention is%'").fetchone()["id"]
    out = call(lg, actor, "curriculum", curriculum="cur:delta-rule", ops=[
        {"op": "lineage", "work": "katharopoulos2020transformers", "builds_on": "yang2024gated", "relation": "compares_to",
         "change": "x", "evidence": full[:10]}])
    assert "applied 1" in out and not "problem" in out.lower(), out
    out = call(lg, actor, "curriculum", curriculum="cur:delta-rule", ops=[
        {"op": "lineage", "work": "katharopoulos2020transformers", "builds_on": "yang2024gated", "relation": "motivates",
         "change": "x", "evidence": "n:zzzz"}])
    assert "evidence note n:zzzz not found" in out, out


def test_move_update_and_remove(lg, actor):
    build(lg, actor)
    out = call(lg, actor, "curriculum", curriculum="cur:delta-rule", ops=[
        {"op": "move", "id": "r2", "stage": "s1", "before": "r1"},
        {"op": "read_update", "id": "r1", "depth": "skim", "minutes": "20"},
        {"op": "read_update", "id": "r1", "depth": "devour"},
        {"op": "remove", "id": "s2"},
        {"op": "stage_update", "id": "s1", "title": "Background"},
    ])
    assert "applied 4" in out and "depth must be one of" in out
    data = curricula.get(lg, actor.project, "cur:delta-rule")
    assert [s["title"] for s in data["stages"]] == ["Background"]
    assert [(r["id"], r["depth"], r.get("minutes")) for r in data["stages"][0]["readings"]] == [("r2", "read", 50), ("r1", "skim", 20)]
    blocked = call(lg, actor, "curriculum", curriculum="cur:delta-rule", ops=[{"op": "remove", "id": "s1"}])
    assert "still has readings" in blocked


def test_syllabus_markdown(lg, actor):
    build(lg, actor)
    md = curricula.markdown(curricula.get(lg, actor.project, "cur:delta-rule"))
    assert md.startswith("# Delta rule") and "*For ML engineers who know attention.*" in md
    assert "## 1. Foundations" in md and "1.1 **Transformers are RNNs" in md and "- Focus: §3.2" in md
    assert "**Check yourself**" in md and "- Why is linear attention an RNN?" in md and "About 1.8 hours of reading." in md


def test_rest_routes_and_reading_progress(client):
    c, h = client, {}
    c.post("/api/v1/tools/resolve", json={"items": ["2412.06464"], "add": True}, headers=h)
    made = c.post("/api/v1/curricula", json={"title": "Gating", "ops": [{"op": "stage", "key": "a", "title": "Core"},
                                                                      {"op": "read", "stage": "a", "work": "yang2024gated", "why": "w"}]}, headers=h).json()
    assert made["curriculum"] == "cur:gating" and made["added"] == ["s1", "r1"]
    c.post("/api/v1/tools/update_work", json={"items": [{"work": "yang2024gated", "read": "skimmed"}]}, headers=h)
    data = c.get("/api/v1/curricula/cur:gating", headers=h).json()
    assert data["stages"][0]["readings"][0]["work"]["read"] == "skimmed"
    assert c.get("/api/v1/curricula", headers=h).json()[0]["readings"] == 1
    md = c.get("/api/v1/curricula/cur:gating/markdown", headers=h)
    assert md.status_code == 200 and md.text.startswith("# Gating")
    assert c.delete("/api/v1/curricula/cur:gating", headers=h).json() == {"deleted": True}
    assert c.get("/api/v1/curricula/cur:gating", headers=h).status_code == 404


def test_build_curriculum_brief():
    fn, _, args = BRIEFS["build_curriculum"]
    assert args == ["idea", "audience", "hours"]
    text = fn("delta rule", hours="12")
    for must in ("Read the papers before you place them", "curriculum:delta-rule", "\"op\": \"lineage\"", "about 12 hours",
                 "extends, generalizes, simplifies, fixes", "curriculum action=get"):
        assert must in text, must
