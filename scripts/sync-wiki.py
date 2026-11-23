#!/usr/bin/env python3
"""Publish docs/ to the GitHub wiki, so the wiki mirrors the docs in the repo.

    python3 scripts/sync-wiki.py            # clone the wiki, update pages, commit and push
    python3 scripts/sync-wiki.py --dry-run  # write pages to a temp folder only

The wiki must exist first: open https://github.com/<owner>/<repo>/wiki once and
save any first page. docs/README.md becomes Home; every other docs/*.md becomes
a page named after its file (getting-started.md -> Getting-started). Links between docs become wiki links; links into
the repo and images point at the main branch.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = "danztensai/ec2-sim"
BRANCH = "main"
ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
BLOB = f"https://github.com/{REPO}/blob/{BRANCH}"
RAW = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"


def page_name(md: Path) -> str:
    if md.name == "README.md":
        return "Home"
    stem = md.stem.replace("amis", "AMIs")
    return stem[0].upper() + stem[1:]


PAGES = {md.name: page_name(md) for md in sorted(DOCS.glob("*.md"))}


def rewrite(text: str) -> str:
    def link(m: re.Match) -> str:
        bang, label, target = m.group(1), m.group(2), m.group(3)
        if re.match(r"^[a-z]+:", target) or target.startswith("#"):
            return m.group(0)
        path, _, anchor = target.partition("#")
        anchor = f"#{anchor}" if anchor else ""
        if bang:
            return f"![{label}]({RAW}/docs/{path}{anchor})"
        if path in PAGES:
            return f"[{label}]({PAGES[path]}{anchor})"
        resolved = (DOCS / path).resolve().relative_to(ROOT).as_posix()
        if resolved == "README.md":
            return f"[{label}](https://github.com/{REPO}{anchor})"
        return f"[{label}]({BLOB}/{resolved}{anchor})"

    return re.sub(r"(!?)\[([^\]]*)\]\(([^)\s]+)\)", link, text)


def build(out: Path) -> None:
    for md in sorted(DOCS.glob("*.md")):
        (out / f"{PAGES[md.name]}.md").write_text(rewrite(md.read_text()))
    side = ["**[ec2-sim](https://github.com/%s)**" % REPO, ""]
    order = ["README.md", "why-ec2-sim.md", "getting-started.md", "desktop-app.md", "baking-amis.md",
             "networking.md", "stacks.md", "multi-tier-testing.md", "troubleshooting.md", "architecture.md"]
    for name in order + sorted(set(PAGES) - set(order)):
        if name in PAGES:
            label = PAGES[name].replace("-", " ").replace("ec2 sim", "ec2-sim").replace("Multi tier", "Multi-tier")
            side.append(f"- [{label}]({PAGES[name]})")
    side += ["", f"_Generated from [docs/]({BLOB}/docs). Edit there, not here._"]
    (out / "_Sidebar.md").write_text("\n".join(side) + "\n")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ec2sim-wiki-"))
    if "--dry-run" in sys.argv:
        build(tmp)
        print(tmp)
        return 0
    wiki = tmp / "wiki"
    if subprocess.run(["git", "clone", "-q", f"git@github.com:{REPO}.wiki.git", str(wiki)]).returncode:
        print(f"Wiki not found. Create its first page at https://github.com/{REPO}/wiki, then rerun.")
        return 1
    for old in wiki.glob("*.md"):
        old.unlink()
    build(wiki)
    git = lambda *a: subprocess.run(["git", "-C", str(wiki), *a], check=True)
    git("add", "-A")
    if subprocess.run(["git", "-C", str(wiki), "diff", "--cached", "--quiet"]).returncode == 0:
        print("Wiki already up to date.")
        return 0
    rev = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    git("commit", "-q", "-m", f"Sync from docs/ at {rev}")
    git("push", "-q", "origin", "HEAD")
    print(f"Wiki updated: https://github.com/{REPO}/wiki")
    return 0


if __name__ == "__main__":
    sys.exit(main())
