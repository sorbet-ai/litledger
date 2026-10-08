"""Rewrite constraints.txt from the current virtualenv (run after upgrading dependencies and passing the tests).

Only litledger's runtime dependency closure is pinned: the dependencies pyproject.toml declares, their extras
(uvicorn[standard]) and everything they pull in, with environment markers evaluated here. Dev tools in the same
virtualenv (pytest, pyflakes, playwright, …) stay out."""
import tomllib
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def closure(requirements: list[str]) -> dict[str, str]:
    """{name: installed version} for these requirements and everything they need."""
    pins: dict[str, str] = {}
    extras_seen: dict[str, set[str]] = {}
    todo = [Requirement(r) for r in requirements]
    while todo:
        req = todo.pop()
        key = canonicalize_name(req.name)
        new_extras = set(req.extras) - extras_seen.get(key, set())
        if key in pins and not new_extras:
            continue
        try:
            dist = distribution(req.name)
        except PackageNotFoundError:
            continue  # a marker-gated dependency for another platform
        extras_seen.setdefault(key, set()).update(req.extras)
        first = key not in pins
        pins[key] = dist.version
        for sub in map(Requirement, dist.requires or []):
            wanted = ({""} if first else set()) | new_extras
            if sub.marker is None:
                if first:
                    todo.append(sub)
            elif any(sub.marker.evaluate({"extra": e}) for e in wanted):
                todo.append(sub)
    return pins


project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
pins = closure(project["dependencies"])
lines = ["# Exact versions the test suite last passed with. The Docker build installs with: pip install -c constraints.txt .",
         "# Refresh after upgrading: python scripts/lock.py"] + [f"{n}=={v}" for n, v in sorted(pins.items())]
(ROOT / "constraints.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"{len(pins)} pins written")
