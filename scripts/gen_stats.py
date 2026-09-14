#!/usr/bin/env python3
"""Refresh public GitHub stats with GITHUB_TOKEN and the Python standard library.

Repos/stars/languages: owned public non-forks, including archived repositories.
Commits/lines: unique, user-authored non-merge commits on default branches of
owned public non-forks and discovered public collaborations. This is accessible
history, not a claim of exhaustive lifetime activity. Private/deleted repositories,
unmerged work, unlinked author emails, and commits absent from default branches
are excluded. Lines changed are GitHub diff totals, including non-code text;
they do not measure the current size of a codebase or individual productivity.

Discovery unions owned repos, recent contributions, historical contribution
windows, and the public collaborations featured on the website. Contribution
windows are subdivided when their repository cap is reached; an unsplittable
window fails rather than silently truncating. No API error publishes partial data.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time
from urllib import error, request
import xml.etree.ElementTree as ET

from svg import PALETTES, document, text

LOGIN = "Jordan-Leis"
HOST = "University of Waterloo · Electrical Engineering, 3B"
KERNEL = "SystemVerilog · Vivado · Zynq UltraScale+"
SHELL = "Python · C++ · RISC-V asm"
SITE = "jordanleis.com"
LOOKING = "FPGA / RTL co-op, 2027"
WHERE = "Asia Jan–Apr 2027 · Cansbridge Fellowship"
COLLABORATIONS = ("ScienceGPTstream2/SummarizationTool", "Madhav-Malhotra/political-chatbot")
ASSETS = Path(__file__).resolve().parents[1] / "assets"
REPO_LIMIT = 100
PAGE = "pageInfo { hasNextPage endCursor }"
REPO = "id nameWithOwner isPrivate isFork stargazerCount primaryLanguage { name }"


class CollectionError(RuntimeError):
    pass


class GitHub:
    def __init__(self, token):
        if not token:
            raise CollectionError("Set GITHUB_TOKEN to a GitHub token with public repository read access.")
        self.token = token

    def query(self, query, **variables):
        payload = json.dumps({"query": query, "variables": variables}).encode()
        for attempt in range(4):
            req = request.Request("https://api.github.com/graphql", data=payload, headers={
                "Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
                "User-Agent": "Jordan-Leis-profile-stats"})
            try:
                with request.urlopen(req, timeout=30) as response:
                    body = json.load(response)
            except error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise CollectionError(f"GitHub HTTP {exc.code}; assets were not updated.") from exc
            except (error.URLError, TimeoutError) as exc:
                if attempt == 3:
                    raise CollectionError("GitHub request failed after four attempts.") from exc
            else:
                if body.get("errors") or not body.get("data"):
                    raise CollectionError(f"GitHub GraphQL error: {body.get('errors', 'missing data')}")
                return body["data"]
            time.sleep(2 ** attempt)


def pages(fetch):
    cursor, seen = None, set()
    while True:
        connection = fetch(cursor)
        if connection is None or any(node is None for node in connection["nodes"]):
            raise CollectionError("Incomplete GitHub connection.")
        yield from connection["nodes"]
        info = connection["pageInfo"]
        if not info["hasNextPage"]:
            break
        cursor = info["endCursor"]
        if not cursor or cursor in seen:
            raise CollectionError("GitHub pagination did not advance.")
        seen.add(cursor)


def iso(value):
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def historical_repos(api, start, end):
    query = """query($login:String!,$from:DateTime!,$to:DateTime!,$limit:Int!){
      user(login:$login){contributionsCollection(from:$from,to:$to){
        commitContributionsByRepository(maxRepositories:$limit){repository{REPO}}
      }}}""".replace("REPO", REPO)

    def window(left, right):
        rows = api.query(query, login=LOGIN, **{"from": iso(left), "to": iso(right), "limit": REPO_LIMIT})["user"]["contributionsCollection"]["commitContributionsByRepository"]
        if len(rows) >= REPO_LIMIT:
            if right - left <= timedelta(seconds=1):
                raise CollectionError("Contribution discovery exceeded the repository cap at one-second resolution.")
            middle = left + timedelta(seconds=int((right - left).total_seconds()) // 2)
            return window(left, middle) + window(middle, right)
        return [row["repository"] for row in rows]

    result = []
    while start < end:
        stop = min(start + timedelta(days=365), end)
        result.extend(window(start, stop))
        start = stop
    return result


def collect(api, now):
    user = api.query("""query($login:String!){user(login:$login){id createdAt followers{totalCount}}}""", login=LOGIN)["user"]
    created = datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00"))
    owned_query = """query($login:String!,$after:String){user(login:$login){repositories(
      first:100,after:$after,ownerAffiliations:[OWNER],privacy:PUBLIC,isFork:false){nodes{REPO} PAGE}}}""".replace("REPO", REPO).replace("PAGE", PAGE)
    owned = list(pages(lambda cursor: api.query(owned_query, login=LOGIN, after=cursor)["user"]["repositories"]))
    recent_query = """query($login:String!,$after:String){user(login:$login){repositoriesContributedTo(
      first:100,after:$after,privacy:PUBLIC,contributionTypes:[COMMIT],includeUserRepositories:false){nodes{REPO} PAGE}}}""".replace("REPO", REPO).replace("PAGE", PAGE)
    recent = list(pages(lambda cursor: api.query(recent_query, login=LOGIN, after=cursor)["user"]["repositoriesContributedTo"]))
    candidates = owned + recent + historical_repos(api, created, now)
    for full_name in COLLABORATIONS:
        owner, name = full_name.split("/")
        repo = api.query("query($owner:String!,$name:String!){repository(owner:$owner,name:$name){" + REPO + "}}", owner=owner, name=name)["repository"]
        if repo is None:
            raise CollectionError(f"Cannot read featured public collaboration {full_name}.")
        candidates.append(repo)
    repos = {repo["id"]: repo for repo in candidates if not repo["isPrivate"]}
    history_query = """query($id:ID!,$author:ID!,$after:String){node(id:$id){... on Repository{
      defaultBranchRef{target{... on Commit{history(first:100,after:$after,author:{id:$author}){
        nodes{oid additions deletions author{user{id}} parents{totalCount}} PAGE
      }}}}
    }}}""".replace("PAGE", PAGE)
    commits, additions, deletions = set(), 0, 0
    for repo in sorted(repos.values(), key=lambda item: item["nameWithOwner"].lower()):
        def fetch(cursor):
            node = api.query(history_query, id=repo["id"], author=user["id"], after=cursor)["node"]
            if node is None:
                raise CollectionError(f"Cannot read {repo['nameWithOwner']}.")
            branch = node["defaultBranchRef"]
            if branch is None:
                return {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}
            return branch["target"]["history"]
        count = 0
        for commit in pages(fetch):
            author = commit["author"]["user"]
            if not author or author["id"] != user["id"] or commit["parents"]["totalCount"] > 1:
                continue
            if commit["oid"] not in commits:
                commits.add(commit["oid"])
                additions += commit["additions"]
                deletions += commit["deletions"]
                count += 1
        print(f"{repo['nameWithOwner']}: {count} unique authored non-merge commits", file=sys.stderr)
    languages = Counter(repo["primaryLanguage"]["name"] for repo in owned if repo["primaryLanguage"])
    return dict(repos=len(owned), stars=sum(repo["stargazerCount"] for repo in owned),
                followers=user["followers"]["totalCount"], created_at=user["createdAt"],
                commits=len(commits), additions=additions, deletions=deletions,
                languages=[name for name, _ in sorted(languages.items(), key=lambda item: (-item[1], item[0]))[:4]])


def uptime(created_at, now):
    created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    months = (now.year - created.year) * 12 + now.month - created.month - (now.day < created.day)
    years, months = divmod(max(0, months), 12)
    return f"{years} year{'s' if years != 1 else ''}, {months} month{'s' if months != 1 else ''} on GitHub"


def render(stats, theme, now):
    p = PALETTES[theme]
    parts = [f'<rect x="0.5" y="0.5" width="859" height="399" rx="10" fill="{p["bg"]}" stroke="{p["line"]}"/>']
    y = 32

    def head(label):
        nonlocal y
        parts.append(text(label, 28, y, p["fg"], weight=600))
        parts.append(text(" " + "─" * (76 - len(label)), 28 + len(label) * 9, y, p["muted"]))
        y += 23

    def row(key, value):
        nonlocal y
        dots = " " + "." * max(2, 76 - len(key) - len(value) - 2) + " "
        parts.append(text(key, 28, y, p["accent"]))
        parts.append(text(dots, 28 + len(key) * 9, y, p["muted"]))
        parts.append(text(value, 28 + (len(key) + len(dots)) * 9, y, p["fg"]))
        y += 23

    head("jordan@waterloo")
    for key, value in (("Host", HOST), ("Kernel", KERNEL), ("Shell", SHELL),
                       ("Uptime", uptime(stats["created_at"], now)), ("Site", SITE)):
        row(key, value)
    y += 8
    head("GitHub")
    row("Repos", f"{stats['repos']:,}   Stars ..... {stats['stars']:,}   Followers ..... {stats['followers']:,}")
    row("Commits", f"{stats['commits']:,}")
    row("Lines changed", f"{stats['additions']:,}++  {stats['deletions']:,}--")
    row("Languages", " · ".join(stats["languages"]) or "None yet")
    parts.append(text("Commits/lines: public owned + collaborations · default branches", 28, y - 3, p["muted"], 12))
    y += 25
    head("Looking for")
    row("Co-op", LOOKING)
    row("Where", WHERE)
    description = (f"{HOST}. {stats['repos']} owned public non-fork repositories, {stats['stars']} stars, "
                   f"{stats['followers']} followers. {stats['commits']} unique authored non-merge commits "
                   f"in public owned repositories and discovered collaborations, default branches only. "
                   f"{stats['additions']} lines added, {stats['deletions']} deleted. {LOOKING}. {WHERE}.")
    return document(860, 400, "Jordan's GitHub neofetch", description, parts)


def generate(api, now, output=ASSETS):
    stats = collect(api, now)
    rendered = {theme: render(stats, theme, now) for theme in PALETTES}
    for value in rendered.values():
        ET.fromstring(value)
    output.mkdir(parents=True, exist_ok=True)
    for theme, value in rendered.items():
        target = output / f"neofetch-{theme}.svg"
        if not target.exists() or target.read_text(encoding="utf-8") != value:
            temporary = target.with_suffix(".svg.tmp")
            temporary.write_text(value, encoding="utf-8")
            temporary.replace(target)
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ASSETS)
    args = parser.parse_args()
    try:
        stats = generate(GitHub(os.environ.get("GITHUB_TOKEN")), datetime.now(timezone.utc), args.output_dir)
    except (CollectionError, KeyError, TypeError, ValueError, OSError) as exc:
        print(f"Stats refresh failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(stats, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
