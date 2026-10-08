"""Make a small real database with an OLD litledger source tree, then compact it (tests/test_migrations.py upgrades
the results). The old tree goes first on sys.path, so its code writes the database:

    git worktree add <tmp>/wt 458f8ca                        # schema v1 (8c8e80d: v4)
    python tests/fixtures/db/make_old_db.py <tmp>/wt/src tests/fixtures/db/ledger-v1.sqlite3
    git worktree remove <tmp>/wt

Run it from an empty folder with the current venv's python; it prints the API token to put in OLD_DBS."""
import sqlite3
import sys
import tempfile
from pathlib import Path

src, out = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(src))
import litledger  # noqa: E402

assert Path(litledger.__file__).resolve().is_relative_to(src.resolve()), litledger.__file__
from litledger import auth, tools  # noqa: E402
from litledger.config import Settings  # noqa: E402
from litledger.db import Actor  # noqa: E402

try:
    from litledger.ledger import Ledger  # v4+
except ImportError:
    from litledger.core import Ledger  # v1-v3

data = Path(tempfile.mkdtemp()) / "data"
settings = Settings.from_env({"LITLEDGER_DATA": str(data), "LITLEDGER_OFFLINE": "1"})
kw = {"run_jobs_inline": False}
lg = Ledger(settings, **kw)
version = sqlite3.connect(lg.db.path).execute("PRAGMA user_version").fetchone()[0]
print("schema", version, "from", litledger.__file__)

# principals: a token agent, and (when this version has them) a person with an email
token = auth.create_token(lg.db, "laptop")
print("TOKEN", token)
if "create_person" in dir(auth):
    auth.create_person(lg.db, "ada", "ada@example.com")
a = Actor(name="laptop", kind="agent", project="thesis")
with lg.db.read() as conn:
    row = conn.execute("SELECT id FROM principals WHERE name='laptop'").fetchone()
a.principal_id = row[0]

BIB = ["@article{vaswani2017, title={Attention Is All You Need}, author={Vaswani, Ashish and Shazeer, Noam}, year={2017}, "
       "journal={NeurIPS}, doi={10.5555/3295222.3295349}}",
       "@article{yang2024, title={Gated Delta Networks: Improving Mamba2 with Delta Rule}, author={Yang, Songlin}, year={2024}, "
       "eprint={2412.06464}, archivePrefix={arXiv}}",
       "@inproceedings{schlag2021, title={Linear Transformers Are Secretly Fast Weight Programmers}, author={Schlag, Imanol}, "
       "year={2021}, booktitle={ICML}}"]


def call(name, **args):
    text = tools.call(lg, a, name, args)
    print(f"{name}: {text.splitlines()[0] if text else ''}")
    return text


print(call("resolve", items=BIB, add=True, tags=["phase:baselines"], why="core reading"))
with lg.db.read() as conn:
    keys = [r[0] for r in conn.execute("SELECT citekey FROM works ORDER BY citekey")]
print("keys", keys)
call("note", items=[{"work": keys[0], "text": "the paper that started it", "tags": ["key"]}])
call("tag", action="apply", tags=["todo:read"], works=[keys[1]])
call("entity", items=[{"kind": "topic", "title": "Linear attention"}])
call("link", items=[{"source": keys[1], "target": "topic:linear-attention", "relation": "about"}])
call("map_edit", title="Memory", outline="- Linear attention\n  - " + keys[1] + "\n- Open questions")
call("update_work", items=[{"work": keys[2], "read": "skimmed"}])

out.parent.mkdir(parents=True, exist_ok=True)
out.unlink(missing_ok=True)
conn = sqlite3.connect(lg.db.path)
conn.execute("VACUUM INTO ?", (str(out),))
conn.close()
check = sqlite3.connect(out)
print("wrote", out, out.stat().st_size, "bytes; user_version", check.execute("PRAGMA user_version").fetchone()[0],
      "journal_mode", check.execute("PRAGMA journal_mode").fetchone()[0])
small = sqlite3.connect(out, isolation_level=None)
small.execute("PRAGMA page_size=1024")
small.execute("VACUUM")
small.close()
print("compacted to", out.stat().st_size, "bytes")
