"""Links to the Frame Art guides must resolve to the files, which live at the
repo root (not under docs/).

examples/frame_art/README.md and www/community/folder-gallery-card/README.md
both linked ../../docs/Frame_Art.md, but the guides are Frame_Art.md and
Frame_Art_Gallery.md at the repo root, so the links 404'd.
"""

from pathlib import Path
import re

ROOT = Path(__file__).parents[1]
GUIDES = ("Frame_Art.md", "Frame_Art_Gallery.md")
# [text](target) markdown links, target up to ) or #anchor or whitespace.
_LINK = re.compile(r"\]\(([^)#\s]+)")


def _links_to_guides():
    """(md_file, link_target) for every relative link pointing at a guide."""
    for md in ROOT.rglob("*.md"):
        if ".git" in md.parts:
            continue
        for target in _LINK.findall(md.read_text()):
            if target.startswith(("http://", "https://")):
                continue
            if Path(target).name in GUIDES:
                yield md, target


def test_the_guides_live_at_the_repo_root():
    for guide in GUIDES:
        assert (ROOT / guide).is_file(), f"{guide} missing from repo root"


def test_every_link_to_a_guide_resolves():
    broken = [
        f"{md.relative_to(ROOT)} -> {target}"
        for md, target in _links_to_guides()
        if not (md.parent / target).resolve().is_file()
    ]
    assert not broken, "links to a Frame Art guide that do not resolve:\n" + "\n".join(
        broken
    )
