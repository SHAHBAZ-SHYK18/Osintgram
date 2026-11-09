"""Guards against the documentation drifting away from the code.

Deliberately narrow: it checks the facts that go stale silently when someone
adds a feature (a new command, a new environment variable), not the prose -
asserting on wording would just make editing the docs annoying.
"""
import re
from pathlib import Path

import pytest

import src.web.app as appmod
from src.web.tools import tool_specs

GUIDE = Path(__file__).resolve().parent.parent / "doc" / "web-ui.md"
API_GUIDE = Path(__file__).resolve().parent.parent / "doc" / "api.md"
SOURCES = ["src/osint_service.py", "src/web/app.py", "src/instagrapi_backend.py"]


@pytest.fixture(scope="module")
def guide():
    return GUIDE.read_text()


@pytest.fixture(scope="module")
def api_guide():
    return API_GUIDE.read_text()


def test_the_guide_exists_and_the_readme_links_to_it(guide):
    readme = (GUIDE.parent.parent / "README.md").read_text()
    assert "doc/web-ui.md" in readme
    assert len(guide.splitlines()) > 100


def test_every_environment_variable_is_documented(guide):
    """A new knob nobody can discover is a knob that doesn't exist."""
    root = GUIDE.parent.parent
    code = "".join((root / path).read_text() for path in SOURCES)
    variables = set(re.findall(r'os\.getenv\("([A-Z][A-Z0-9_]+)"', code))
    assert variables, "no environment variables found - did the sources move?"
    assert sorted(v for v in variables if v not in guide) == []


def test_the_command_count_is_current(guide):
    assert f"{len(tool_specs())} commands" in guide


def test_every_command_is_mentioned_by_its_ui_name(guide):
    """Each command must be findable in the guide under the label the UI shows
    for it - the name the reader actually sees on screen."""
    page = (GUIDE.parent.parent / "src/web/static/index.html").read_text()
    labels = dict(re.findall(r'^\s{4}(\w+): \["[^"]*", "([^"]+)"', page, re.M))
    assert len(labels) >= len(tool_specs()), "could not read TOOL_META labels"
    missing = sorted(labels[s["name"]] for s in tool_specs()
                     if s["name"] in labels and labels[s["name"]] not in guide)
    assert missing == []


def test_internal_anchors_resolve(guide):
    def slug(heading):
        return re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", heading.strip().lower())).strip("-")

    headings = {slug(m.group(2)) for m in re.finditer(r"^(#{1,6})\s+(.*)$", guide, re.M)}
    broken = [link for link in re.findall(r"\]\(#([^)]+)\)", guide) if link not in headings]
    assert broken == []


def test_documented_defaults_match_the_code(guide):
    assert f"`{appmod.OLLAMA_MODEL}`" in guide
    assert f"`{appmod.CACHE_PATH}`" in guide
    for spec in tool_specs():
        for param in spec["params"]:
            if param["name"] == "limit" and spec["name"] == "get_followers":
                assert f"`limit` ({param['default']})" in guide


# ---- doc/api.md: the HTTP surface ------------------------------------------
# The API guide is a promise about endpoints anyone can call, so it rots in
# the two ways that matter: an endpoint or a command that exists but is not
# documented, and a link that no longer resolves.

def _api_routes():
    return sorted({r.path for r in appmod.app.routes
                   if getattr(r, "path", "").startswith("/api")})


def _slugify(heading):
    return re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", heading.strip().lower())).strip("-")


def _headings(text):
    return {_slugify(m.group(2)) for m in re.finditer(r"^(#{1,6})\s+(.*)$", text, re.M)}


def test_the_api_guide_exists_and_is_reachable_from_the_readme(api_guide):
    readme = (API_GUIDE.parent.parent / "README.md").read_text()
    assert "doc/api.md" in readme
    assert len(api_guide.splitlines()) > 100


def test_every_api_endpoint_is_documented(api_guide):
    """A route nobody can discover is a route nobody can automate against."""
    missing = [route for route in _api_routes() if route not in api_guide]
    assert missing == []


def test_every_command_is_in_the_api_guide(api_guide):
    missing = sorted(spec["name"] for spec in tool_specs() if spec["name"] not in api_guide)
    assert missing == []


def test_every_command_parameter_is_in_the_api_guide(api_guide):
    """Renaming a parameter without updating the guide would make every
    snippet in it silently wrong."""
    missing = sorted(
        f'{spec["name"]}.{param["name"]}'
        for spec in tool_specs() for param in spec["params"]
        if param["name"] not in api_guide
    )
    assert missing == []


def test_the_api_guide_command_count_is_current(api_guide):
    assert f"{len(tool_specs())} commands" in api_guide


def test_api_guide_anchors_and_cross_links_resolve(api_guide):
    headings = _headings(api_guide)
    broken = [link for link in re.findall(r"\]\(#([^)]+)\)", api_guide) if link not in headings]
    assert broken == []
    # Cross-document links (doc/api.md -> web-ui.md, ../README.md, ...).
    for target, anchor in re.findall(r"\]\(([\w./-]+\.md)#([^)]+)\)", api_guide):
        linked = (API_GUIDE.parent / target).resolve()
        assert linked.exists(), f"missing document: {target}"
        assert anchor in _headings(linked.read_text()), f"broken anchor: {target}#{anchor}"
