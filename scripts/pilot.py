#!/usr/bin/env python3
"""The pilot's bot: on-demand syncs with /sync, /ready and /promote, votes on decisions,
membership, reviewer assignment and the ledger of who did what. PROCESS.md is the rulebook;
this enforces it.

Runs in GitHub Actions as the org's GitHub App (pilot.json `app_id`, its private key in
PILOT_APP_KEY), and locally (`--dry-run` prints what it would do on GitHub). Needs git, gh and
openssl; refreshing a Cargo.lock after re-pinning craft-libs needs cargo. Python 3.9+, standard
library only.
"""

import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
METADATA_SUBJECT = "pilot: fork metadata"
STATE_RE = re.compile(r"<!-- pilot-sync (\{.*?\}) -->", re.S)
REV_RE = re.compile(r'\brev\s*=\s*"([0-9a-f]{7,40})"')
MAX_COMMENT = 60000


class SyncError(Exception):
    """Something the bot can't do mechanically. It is reported, never worked around."""


# ---------------------------------------------------------------------------------------------
# Config


FORK_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]*$")
RECORD_RE = re.compile(r"(\d{4})-[a-z0-9][a-z0-9-]*\.md$")


def load_config(root=None):
    """pilot.json, with each fork from forks/<id>/fork.json. A fork's id is its directory, and
    its decision records are in forks/<id>/adr, numbered from 0001 within the fork."""
    root = Path(root or ROOT)
    cfg = json.loads((root / "pilot.json").read_text())
    cfg["forks"] = []
    for path in sorted((root / "forks").glob("*/fork.json")):
        fork = json.loads(path.read_text())
        fork.update(id=path.parent.name, adr=f"forks/{path.parent.name}/adr")
        cfg["forks"].append(fork)
    return cfg


def fork_file(fork):
    """A fork as it is stored in its fork.json."""
    return {k: v for k, v in fork.items() if k not in ("id", "adr")}


def records(root, fork_id):
    d = Path(root) / "forks" / fork_id / "adr"
    return sorted(p.name for p in d.glob("*.md")) if d.is_dir() else []


def fork_by_id(cfg, fork_id):
    for f in cfg["forks"]:
        if f["id"] == fork_id:
            return f
    raise SyncError(f"there is no fork {fork_id!r}")


def fork_order(cfg):
    """Forks with parents before children."""
    done, order = set(), []
    pending = list(cfg["forks"])
    while pending:
        ready = [f for f in pending if all(p in done for p in f["parents"])]
        if not ready:
            raise SyncError("the forks have a cycle or an unknown parent: "
                            + ", ".join(f["id"] for f in pending))
        for f in ready:
            order.append(f)
            done.add(f["id"])
            pending.remove(f)
    return order


def children(cfg, fork_id):
    return [f["id"] for f in cfg["forks"] if fork_id in f["parents"]]


def validate(cfg, root=None):
    """Problems with the configuration, as a list of messages (empty when it is sound)."""
    root = Path(root or ROOT)
    problems = []
    review = cfg["review"]
    if review.get("solo") and any(len(f["members"]) > 1 for f in cfg["forks"]):
        problems.append("review.solo is on but a fork has more than one member; turn it off")
    if not review.get("solo") and review["approvals"] < 2:
        problems.append("review.approvals must be at least 2 outside the solo phase")
    votes = cfg["votes"]
    if not review.get("solo") and (votes["hours"] < 48 or votes["quorum"] < 3):
        problems.append("votes need at least 48 hours and a quorum of 3 outside the solo phase")
    if cfg.get("app_id") is not None and not isinstance(cfg["app_id"], int):
        problems.append("app_id is the bot's GitHub App id, a number")
    if not 0.5 < votes["majority"] <= 1:
        problems.append("votes.majority must be more than half")
    ids = [f["id"] for f in cfg["forks"]]
    if len(ids) != len(set(ids)):
        problems.append("fork ids are not unique")
    try:
        fork_order(cfg)
    except SyncError as e:
        problems.append(str(e))
    for f in cfg["forks"]:
        if not FORK_ID_RE.match(f["id"]):
            problems.append(f"{f['id']}: a fork id is lowercase letters, digits and hyphens")
        names = records(root, f["id"])
        numbers = []
        for name in names:
            m = RECORD_RE.match(name)
            if not m:
                problems.append(f"{f['id']}: decision record {name} isn't named NNNN-slug.md")
            else:
                numbers.append(int(m.group(1)))
        if sorted(numbers) != list(range(1, len(numbers) + 1)):
            problems.append(f"{f['id']}: decision records must be numbered 0001, 0002, … without gaps")
        if f["status"] not in ("draft", "open", "done", "merged", "superseded", "stale"):
            problems.append(f"{f['id']}: unknown status {f['status']!r}")
        if len(set(f["members"])) != len(f["members"]):
            problems.append(f"{f['id']}: a member is listed twice")
        repos = [b["repo"] for b in f["branches"]]
        for b in f["branches"]:
            if b["repo"] not in cfg["repos"]:
                problems.append(f"{f['id']}: repo {b['repo']} isn't in repos")
            if b["branch"] != f"fork/{f['id']}":
                problems.append(f"{f['id']}: branch should be fork/{f['id']}, not {b['branch']}")
            base = b["base"]
            if base != "main" and not base.startswith("pinned-by:"):
                problems.append(f"{f['id']}: unknown base {base!r}")
            for dep_repo, dep_fork in b.get("pins", {}).items():
                if dep_fork != f["id"] and dep_fork not in f["parents"]:
                    problems.append(f"{f['id']}: pins {dep_repo} to {dep_fork}, which isn't a parent")
                if dep_fork == f["id"] and (dep_repo not in repos
                                            or repos.index(dep_repo) > repos.index(b["repo"])):
                    problems.append(f"{f['id']}: {b['repo']} pins {dep_repo}, which must come first")
    return problems


# ---------------------------------------------------------------------------------------------
# Reviewers


def patch_reviewers(members, author, key, k):
    """k members other than the author, chosen at random but repeatably from key."""
    eligible = [m for m in members if m.lower() != author.lower()]
    eligible.sort(key=lambda m: hashlib.sha256(f"{key}:{m}".encode()).hexdigest())
    return eligible[:k]


# ---------------------------------------------------------------------------------------------
# Pins: an app fork's craft-libs dependencies point at another fork's head


def pinned_revs(text, url):
    """The revisions a manifest pins `url` at, one per dependency line naming it."""
    revs = []
    for line in text.splitlines():
        if _names_url(line, url):
            m = REV_RE.search(line)
            if not m:
                raise SyncError(f"a dependency on {url} has no inline rev: {line.strip()}")
            revs.append(m.group(1))
    return revs


def rewrite_pins(text, url, fork_url, rev):
    """Points every inline dependency on `url` at `fork_url` and `rev`. Returns (text, count)."""
    out, count = [], 0
    for line in text.splitlines(keepends=True):
        if _names_url(line, url):
            if not REV_RE.search(line):
                raise SyncError(f"a dependency on {url} has no inline rev: {line.strip()}")
            line = re.sub(r'\bgit\s*=\s*"[^"]*"', f'git = "{fork_url}"', line, count=1)
            line = REV_RE.sub(f'rev = "{rev}"', line, count=1)
            count += 1
        out.append(line)
    return "".join(out), count


def _names_url(line, url):
    m = re.search(r'\bgit\s*=\s*"([^"]*)"', line)
    return bool(m) and m.group(1).rstrip("/").removesuffix(".git") == url


# ---------------------------------------------------------------------------------------------
# Running things


def run(args, cwd=None, check=True, env=None):
    r = subprocess.run(args, cwd=cwd, text=True, capture_output=True,
                       env={**os.environ, **(env or {})})
    if check and r.returncode:
        raise SyncError(f"`{' '.join(args)}` failed:\n{r.stderr.strip() or r.stdout.strip()}")
    return r


def git(wd, *args, check=True):
    return run(["git", *args], cwd=wd, check=check).stdout.strip()


BOT_ENV = {
    "GIT_AUTHOR_NAME": "collective-pilot[bot]", "GIT_AUTHOR_EMAIL": "pilot@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "collective-pilot[bot]", "GIT_COMMITTER_EMAIL": "pilot@users.noreply.github.com",
}


def git_commit(wd, *args):
    return run(["git", *args], cwd=wd, env=BOT_ENV).stdout.strip()


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=")


class App:
    """The bot is a GitHub App (pilot.json `app_id`). Its installation tokens last an hour and a
    sync can take three, so it mints a new one whenever the last is close to running out."""

    def __init__(self, app_id, key, org):
        self.app_id, self.key, self.org = app_id, key, org
        self._token, self._expires = None, 0.0

    def jwt(self, now=None):
        now = int(now or time.time())
        signing = (b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()) + b"." +
                   b64url(json.dumps({"iat": now - 60, "exp": now + 540, "iss": str(self.app_id)}).encode()))
        with tempfile.NamedTemporaryFile("w", suffix=".pem") as f:
            f.write(self.key)
            f.flush()
            r = subprocess.run(["openssl", "dgst", "-sha256", "-sign", f.name], input=signing,
                               capture_output=True)
        if r.returncode:
            raise SyncError(f"couldn't sign with the app's key: {r.stderr.decode().strip()}")
        return (signing + b"." + b64url(r.stdout)).decode()

    def _call(self, path, method="GET"):
        req = urllib.request.Request(f"https://api.github.com/{path}", method=method, headers={
            "Authorization": f"Bearer {self.jwt()}", "Accept": "application/vnd.github+json"})
        try:
            with urllib.request.urlopen(req) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise SyncError(f"GitHub App {method} {path} failed: {e.code} {e.read().decode()}")

    def login(self):
        return self._call("app")["slug"] + "[bot]"

    def token(self):
        if time.time() > self._expires - 600:
            installation = self._call(f"orgs/{self.org}/installation")["id"]
            t = self._call(f"app/installations/{installation}/access_tokens", "POST")
            self._token = t["token"]
            self._expires = datetime.datetime.fromisoformat(t["expires_at"].replace("Z", "+00:00")).timestamp()
        return self._token


class GitHub:
    """The GitHub side, through gh. DryGitHub prints writes instead of making them. As the App
    when PILOT_APP_KEY is set; otherwise as whoever gh is logged in as (an org owner, for the
    first `setup`)."""

    dry = False

    def __init__(self, cfg, git_base="https://github.com"):
        self.cfg = cfg
        self.git_base = git_base
        self.pilot = os.environ.get("GITHUB_REPOSITORY") or f"{cfg['org']}/pilot"
        key = os.environ.get("PILOT_APP_KEY")
        self.app = App(cfg["app_id"], key, cfg["org"]) if key and cfg.get("app_id") else None

    def auth(self):
        """gh, and git through the credential helper in push, read the token from GH_TOKEN."""
        if self.app:
            os.environ["GH_TOKEN"] = self.app.token()

    def url(self, owner, repo):
        return f"{self.git_base}/{owner}/{repo}"

    def api(self, path, method="GET", body=None, check=True):
        self.auth()
        args = ["gh", "api", "-X", method, path]
        if body is not None:
            args += ["--input", "-"]
        r = subprocess.run(args, text=True, capture_output=True,
                           input=json.dumps(body) if body is not None else None)
        if r.returncode:
            if check:
                raise SyncError(f"gh api {method} {path} failed: {r.stderr.strip()}")
            return None
        return json.loads(r.stdout) if r.stdout.strip() else {}

    def gh(self, *args):
        self.auth()
        return run(["gh", *args]).stdout.strip()

    def login(self):
        """The bot's login as REST gives it: `<slug>[bot]` for the App, which no person can have."""
        if not hasattr(self, "_login"):
            self._login = self.app.login() if self.app else self.api("user")["login"]
        return self._login

    def comments(self, number):
        """An issue's or PR's comments, through REST: GraphQL gives a bot's login without its
        `[bot]`, which a person could register."""
        out = self.gh("api", "--paginate", f"repos/{self.pilot}/issues/{number}/comments",
                      "--jq", ".[] | {id: .node_id, login: .user.login, body: .body}")
        return [json.loads(line) for line in out.splitlines()]

    def file_at(self, repo, path, ref):
        import base64
        return base64.b64decode(self.api(f"repos/{repo}/contents/{path}?ref={ref}")["content"]).decode()

    def edit_comment(self, comment_id, body):
        # gh's comment ids are GraphQL node ids.
        self.gh("api", "graphql", "-f", f"id={comment_id}", "-f", f"body={body[:MAX_COMMENT]}", "-f",
                "query=mutation($id: ID!, $body: String!) { updateIssueComment(input: {id: $id, body: $body}) { clientMutationId } }")

    def merge_pr(self, number, head):
        self.gh("pr", "merge", str(number), "-R", self.pilot, "--squash", "--admin",
                "--match-head-commit", head)

    def file(self, owner, repo, path, ref="main"):
        import base64
        data = self.api(f"repos/{owner}/{repo}/contents/{path}?ref={ref}")
        return base64.b64decode(data["content"]).decode()

    def open_sync_issue(self, fork_id):
        out = self.gh("issue", "list", "-R", self.pilot, "--state", "open", "--label", "sync",
                      "--label", f"fork:{fork_id}", "--json", "number")
        issues = json.loads(out)
        return issues[0]["number"] if issues else None

    def issue(self, number):
        return json.loads(self.gh("issue", "view", str(number), "-R", self.pilot,
                                  "--json", "number,body,labels,state,assignees"))

    def create_issue(self, title, body, labels, assignees):
        args = ["issue", "create", "-R", self.pilot, "--title", title, "--body", body]
        for label in labels:
            args += ["--label", label]
        for a in assignees:
            args += ["--assignee", a]
        return self.gh(*args)

    def edit_issue(self, number, body=None, add_labels=(), remove_labels=(), assignees=()):
        args = ["issue", "edit", str(number), "-R", self.pilot]
        if body is not None:
            args += ["--body", body]
        for label in add_labels:
            args += ["--add-label", label]
        for label in remove_labels:
            args += ["--remove-label", label]
        for a in assignees:
            args += ["--add-assignee", a]
        self.gh(*args)

    def comment(self, number, body, repo=None):
        self.gh("issue", "comment", str(number), "-R", repo or self.pilot, "--body", body[:MAX_COMMENT])

    def close_issue(self, number, comment):
        self.gh("issue", "close", str(number), "-R", self.pilot, "--comment", comment)

    def push(self, wd, *args):
        self.auth()
        helper = ([] if not self.app else
                  ["-c", "credential.helper=",
                   "-c", "credential.helper=!f() { echo username=x-access-token; echo \"password=$GH_TOKEN\"; }; f"])
        git(wd, *helper, "push", *args)


class DryGitHub(GitHub):
    dry = True

    def _say(self, *what):
        print("[dry-run]", *what)

    def open_sync_issue(self, fork_id):
        return None

    def create_issue(self, title, body, labels, assignees):
        self._say("create issue", repr(title), "labels", labels, "assignees", assignees)
        print(body)
        return "(dry run)"

    def edit_issue(self, number, body=None, add_labels=(), remove_labels=(), assignees=()):
        self._say("edit issue", number, "add", list(add_labels), "remove", list(remove_labels),
                  "assign", list(assignees))
        if body:
            print(body)

    def comment(self, number, body, repo=None):
        self._say("comment on", repo or self.pilot, number)
        print(body[:MAX_COMMENT])

    def close_issue(self, number, comment):
        self._say("close issue", number, comment)

    def edit_comment(self, comment_id, body):
        self._say("edit comment", comment_id)
        print(body)

    def merge_pr(self, number, head):
        self._say("merge PR", number, "at", head)

    def push(self, wd, *args):
        self._say("git push", *args)


# ---------------------------------------------------------------------------------------------
# Sync


def clone(gh, repo, workroot):
    wd = Path(workroot) / repo
    if not wd.exists():
        run(["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout",
             gh.url(gh.cfg["org"], repo), str(wd)])
        git(wd, "remote", "add", "upstream", gh.url(gh.cfg["upstream_owner"], repo))
    git(wd, "fetch", "--quiet", "--filter=blob:none", "origin")
    git(wd, "fetch", "--quiet", "--filter=blob:none", "upstream", "main")
    return wd


def resolve_base(gh, wd, repo, spec):
    """The commit a branch syncs onto: upstream main, or the revision of this repo that the
    named apps' upstream main branches pin (the newest, if they differ along one line)."""
    if spec == "main":
        return git(wd, "rev-parse", "upstream/main")
    apps = spec.split(":", 1)[1].split(",")
    url = gh.url(gh.cfg["upstream_owner"], repo)
    revs = set()
    for app in apps:
        revs.update(pinned_revs(gh.file(gh.cfg["upstream_owner"], app, "Cargo.toml"), url))
    if not revs:
        raise SyncError(f"none of {', '.join(apps)} pins {repo}")
    full = set()
    for rev in revs:
        git(wd, "fetch", "--quiet", "--filter=blob:none", "upstream", rev)
        full.add(git(wd, "rev-parse", f"{rev}^{{commit}}"))
    for candidate in full:
        if all(is_ancestor(wd, r, candidate) for r in full):
            return candidate
    raise SyncError(f"{', '.join(apps)} pin {repo} at diverging revisions: {', '.join(sorted(full))}")


def is_ancestor(wd, a, b):
    return run(["git", "merge-base", "--is-ancestor", a, b], cwd=wd, check=False).returncode == 0


def stack(wd, base, head):
    """The fork's patches between base and head, oldest first, without the metadata commit."""
    if git(wd, "rev-list", "--merges", f"{base}..{head}"):
        raise SyncError("the fork branch has merge commits; patches must be squash-merged")
    shas = git(wd, "rev-list", "--reverse", f"{base}..{head}").split()
    return [s for s in shas if git(wd, "log", "-1", "--format=%s", s) != METADATA_SUBJECT]


def metadata_pins(wd, head):
    """The pins recorded in head's metadata commit, or None when head isn't one."""
    if git(wd, "log", "-1", "--format=%s", head) != METADATA_SUBJECT:
        return None
    body = git(wd, "log", "-1", "--format=%b", head)
    pins = {}
    for line in body.splitlines():
        m = re.match(r"pins (\S+) at ([0-9a-f]{40})$", line.strip())
        if m:
            pins[m.group(1)] = m.group(2)
    return pins


def pin_targets(gh, fork, branch, heads):
    """repo -> revision for each pin. A pin on this fork's own earlier branch takes this
    sync's head for it; a pin on a parent takes the parent's promoted branch."""
    targets = {}
    for dep_repo, dep_fork in branch.get("pins", {}).items():
        if dep_fork == fork["id"]:
            if dep_repo not in heads:
                raise SyncError(f"{dep_repo} hasn't synced yet in this fork")
            targets[dep_repo] = heads[dep_repo]
        else:
            ref = f"refs/heads/fork/{dep_fork}"
            out = run(["git", "ls-remote", gh.url(gh.cfg["org"], dep_repo), ref]).stdout.split()
            if not out:
                raise SyncError(f"{gh.cfg['org']}/{dep_repo} has no {ref}")
            targets[dep_repo] = out[0]
    return targets


def write_metadata(gh, fork, wd, targets, refresh_lock=True):
    """Commits CODEOWNERS and the pins on top of the working tree's HEAD."""
    org = gh.cfg["org"]
    codeowners = wd / ".github" / "CODEOWNERS"
    codeowners.parent.mkdir(exist_ok=True)
    codeowners.write_text(f"# Written by {org}/pilot on every sync. Don't edit it here.\n"
                          f"* @{org}/{fork['id']}\n")
    body = []
    for dep_repo, rev in sorted(targets.items()):
        url = gh.url(gh.cfg["upstream_owner"], dep_repo)
        fork_url = gh.url(org, dep_repo)
        total = 0
        manifests = [m for m in git(wd, "ls-files").splitlines()
                     if m == "Cargo.toml" or m.endswith("/Cargo.toml")]
        for manifest in manifests:
            p = wd / manifest
            text, count = rewrite_pins(p.read_text(), url, fork_url, rev)
            if count:
                p.write_text(text)
                total += count
        if total == 0:
            raise SyncError(f"the branch pins {dep_repo} but no manifest depends on it")
        body.append(f"pins {dep_repo} at {rev}")
    if targets and refresh_lock and (wd / "Cargo.lock").exists():
        # Cargo rewrites the lock entries for the changed sources and keeps the rest.
        run(["cargo", "metadata", "--format-version", "1", "--quiet"], cwd=wd)
    git(wd, "add", "-A")
    git_commit(wd, "commit", "--quiet", "--allow-empty", "-m", METADATA_SUBJECT,
               "-m", "\n".join(body) or "no pins")
    return git(wd, "rev-parse", "HEAD")


def replay(wd, commits):
    """Cherry-picks commits onto HEAD. Returns (applied, dropped, conflict) where conflict is
    None or (sha, files) for the first patch that doesn't apply, which is left aborted."""
    applied, dropped = [], []
    for sha in commits:
        r = run(["git", "cherry-pick", "--allow-empty-message", sha], cwd=wd, check=False, env=BOT_ENV)
        if r.returncode == 0:
            applied.append(sha)
            continue
        unmerged = git(wd, "diff", "--name-only", "--diff-filter=U").split()
        if unmerged:
            git(wd, "cherry-pick", "--abort")
            return applied, dropped, (sha, unmerged)
        if "empty" in (r.stdout + r.stderr):
            git(wd, "cherry-pick", "--skip")
            dropped.append(sha)
            continue
        raise SyncError(f"cherry-pick {sha} failed:\n{r.stderr.strip()}")
    return applied, dropped, None


def sync_branch(gh, fork, branch, workroot, date, heads, refresh_lock=True):
    """Rebases one branch of a fork onto its sync point and pushes the result to a sync branch.
    Returns its state record."""
    repo = branch["repo"]
    wd = clone(gh, repo, workroot)
    name = branch["branch"]
    if not git(wd, "ls-remote", "origin", f"refs/heads/{name}"):
        raise SyncError(f"{gh.cfg['org']}/{repo} has no {name}; run `pilot.py setup` first")
    old_head = git(wd, "rev-parse", f"origin/{name}")
    new_base = resolve_base(gh, wd, repo, branch["base"])
    old_base = git(wd, "merge-base", old_head, new_base)
    commits = stack(wd, old_base, old_head)
    targets = pin_targets(gh, fork, branch, heads)
    sync_ref = f"sync/{fork['id']}/{date}"
    state = {"repo": repo, "branch": name, "old_head": old_head, "old_base": old_base,
             "new_base": new_base, "sync": sync_ref, "patches": commits, "pins": targets}

    if old_base == new_base and metadata_pins(wd, old_head) == targets:
        state.update(status="current", head=old_head, dropped=[])
        heads[repo] = old_head
        return state

    git(wd, "checkout", "--quiet", "--detach", new_base)
    applied, dropped, conflict = replay(wd, commits)
    state["dropped"] = dropped
    if conflict:
        sha, files = conflict
        state.update(status="conflict", conflict=sha, files=files,
                     remaining=commits[commits.index(sha):], head=git(wd, "rev-parse", "HEAD"))
    else:
        state.update(status="clean", head=write_metadata(gh, fork, wd, targets, refresh_lock))
        heads[repo] = state["head"]
        state["_range_diff"] = git(wd, "range-diff", "--no-color", f"{old_base}..{old_head}",
                                   f"{new_base}..{state['head']}")
    gh.push(wd, "--force", "origin", f"HEAD:refs/heads/{sync_ref}")
    return state


def sync_fork(gh, fork, workroot, date, refresh_lock=True, start=0, prior=None):
    """Syncs a fork's branches in order from `start`, stopping at the first conflict."""
    records = list(prior or [])
    heads = {r["repo"]: r["head"] for r in records if r["status"] in ("clean", "resolved", "current")}
    for branch in fork["branches"][start:]:
        record = sync_branch(gh, fork, branch, workroot, date, heads, refresh_lock)
        records.append(record)
        if record["status"] == "conflict":
            for rest in fork["branches"][len(records):]:
                records.append({"repo": rest["repo"], "branch": rest["branch"], "status": "pending"})
            break
    return records


# ---------------------------------------------------------------------------------------------
# Sync issues


def render_issue(gh, fork, state):
    org = gh.cfg["org"]
    adr = f"https://github.com/{gh.pilot}/tree/main/{fork['adr']}"
    lines = [f"Sync of **{fork['id']}** ([records]({adr})), started by @{state['syncer']} at {state['date']}.", ""]
    if state.get("reviewer"):
        lines.append(f"Asked to test and review: @{state['reviewer']}. Any member except whoever synced "
                     "or resolved it may do it instead; whoever does gets the credit.")
    lines.append("")
    for r in state["branches"]:
        repo_url = f"https://github.com/{org}/{r['repo']}"
        lines.append(f"### {r['repo']}: {r['status']}")
        if r["status"] == "pending":
            lines += ["Waits for the branch above.", ""]
            continue
        if r["status"] == "current":
            lines += ["Already on the sync point, with its pins current. Nothing to do.", ""]
            continue
        lines.append(f"`{r['old_base'][:10]}` → `{r['new_base'][:10]}`, {len(r['patches'])} patches. "
                     f"Sync branch: [`{r['sync']}`]({repo_url}/tree/{r['sync']})")
        for sha in r.get("dropped", []):
            lines.append(f"- Already upstream, dropped: {repo_url}/commit/{sha}")
        if r["status"] == "conflict":
            files = ", ".join(f"`{f}`" for f in r["files"])
            lines += [
                "",
                f"Patch {repo_url}/commit/{r['conflict']} conflicts in {files}. Any member may resolve it:",
                "```sh",
                f"git fetch origin {r['branch']} {r['sync']}",
                f"git switch -c {r['sync']} origin/{r['sync']}",
                f"git cherry-pick {' '.join(r['remaining'])}",
                "# resolve each conflict, `git add`, `git cherry-pick --continue`",
                f"git push origin {r['sync']}",
                "```",
                f"Leave out the `{METADATA_SUBJECT}` commit; the bot writes it. Then comment `/ready`.",
            ]
        if r["status"] == "resolved":
            lines.append(f"Resolved by @{r['resolver']}.")
        if r["status"] in ("clean", "resolved"):
            check = gh.cfg["repos"][r["repo"]]["check"]
            lines += ["", f"To test and review: run `{check}` on the sync branch and read the range-diff "
                          "(posted below), including every conflict resolution. Comment `/promote` if both pass."]
        lines.append("")
    lines.append(f"<!-- pilot-sync {json.dumps(state, sort_keys=True)} -->")
    return "\n".join(lines)


def read_state(body):
    m = STATE_RE.search(body or "")
    if not m:
        raise SyncError("this issue has no sync state")
    return json.loads(m.group(1))


def status_label(state):
    statuses = {r["status"] for r in state["branches"]}
    if "conflict" in statuses or "pending" in statuses:
        return "sync:conflict"
    return "sync:resolved" if "resolved" in statuses else "sync:clean"


SYNC_LABELS = ("sync:clean", "sync:conflict", "sync:resolved")


def sync_stamp(now):
    """Names one sync: syncs are on demand, so several can happen in a day."""
    return now.strftime("%Y-%m-%d-%H%M")


def range_diff_comment(rds):
    parts = [f"<details><summary>git range-diff: {repo}</summary>\n\n```diff\n{rd}\n```\n</details>"
             for repo, rd in rds]
    return "\n\n".join(parts)


def take_range_diffs(branches):
    return [(b["repo"], b.pop("_range_diff")) for b in branches if "_range_diff" in b]


def cmd_sync(gh, args):
    """Syncs one fork on demand, for the member who asked; that member is credited with it."""
    cfg = gh.cfg
    fork = fork_by_id(cfg, args.fork)
    user = args.user
    if user not in fork["members"]:
        raise SyncError(f"@{user} isn't a member of {fork['id']}; `/join {fork['id']}` first")
    if fork["status"] in ("merged", "superseded"):
        raise SyncError(f"{fork['id']} is {fork['status']}; there is nothing to sync")
    waiting = [p for p in fork["parents"] if gh.open_sync_issue(p)]
    if waiting:
        raise SyncError(f"{fork['id']} syncs onto its parents' promoted heads; "
                        f"finish the open sync of {', '.join(waiting)} first")
    existing = gh.open_sync_issue(fork["id"])
    if existing:
        raise SyncError(f"{fork['id']} already has an open sync, #{existing}; finish that one first")
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = sync_stamp(now)
    with tempfile.TemporaryDirectory() as workroot:
        branches = sync_fork(gh, fork, workroot, stamp, refresh_lock=not args.no_lock)
    rds = take_range_diffs(branches)
    if all(b["status"] == "current" for b in branches):
        print(f"{fork['id']} is already current; nothing to sync")
        return None
    state = {"fork": fork["id"], "date": stamp, "syncer": user, "branches": branches}
    conflicted = status_label(state) == "sync:conflict"
    state["reviewer"] = None if conflicted else sync_reviewer(fork["members"], stamp, {user})
    assignees = [user] + ([state["reviewer"]] if state["reviewer"] else [])
    url = gh.create_issue(f"Sync {fork['id']}, {stamp}", render_issue(gh, fork, state),
                          ["sync", f"fork:{fork['id']}", status_label(state)], assignees)
    number = int(str(url).rstrip("/").split("/")[-1]) if str(url).rstrip("/").split("/")[-1].isdigit() else None
    if rds and number:
        gh.comment(number, range_diff_comment(rds))
    print(f"{fork['id']}: {status_label(state)} {url}")
    return state


def sync_reviewer(members, stamp, exclude):
    """A member to ask to test and review a sync, at random but repeatably; never someone who
    synced or resolved it. None when the fork has nobody else."""
    eligible = [m for m in members if m not in exclude]
    picked = patch_reviewers(eligible, "", f"sync:{stamp}", 1)
    return picked[0] if picked else None


def cmd_command(gh, args):
    """Handles /sync <fork> on any issue, and /ready and /promote on a sync issue."""
    cfg = gh.cfg
    try:
        words = args.body.strip().split()
        word = words[0] if words else ""
        if word == "/sync":
            if len(words) < 2:
                raise SyncError("say which fork: `/sync <fork>`")
            state = cmd_sync(gh, argparse.Namespace(fork=words[1], user=args.user, no_lock=args.no_lock))
            if state is None:
                gh.comment(args.issue, f"{words[1]} is already current with upstream; nothing to sync.")
            return
        issue = gh.issue(args.issue)
        state = read_state(issue["body"])
        fork = fork_by_id(cfg, state["fork"])
        if args.user not in fork["members"]:
            raise SyncError(f"@{args.user} isn't a member of {fork['id']}")
        if word == "/ready":
            ready(gh, fork, state, args)
        elif word == "/promote":
            promote(gh, fork, state, args)
    except SyncError as e:
        gh.comment(args.issue, f"Not done: {e}")


def ready(gh, fork, state, args):
    i = next((i for i, r in enumerate(state["branches"]) if r["status"] == "conflict"), None)
    if i is None:
        raise SyncError("no branch is waiting on a resolution")
    r = state["branches"][i]
    with tempfile.TemporaryDirectory() as workroot:
        wd = clone(gh, r["repo"], workroot)
        head = git(wd, "rev-parse", f"origin/{r['sync']}")
        if not is_ancestor(wd, r["new_base"], head):
            raise SyncError(f"{r['sync']} isn't built on the sync point {r['new_base'][:10]}")
        if git(wd, "rev-list", "--merges", f"{r['new_base']}..{head}"):
            raise SyncError(f"{r['sync']} has merge commits")
        subjects = git(wd, "log", "--format=%s", f"{r['new_base']}..{head}").splitlines()
        if METADATA_SUBJECT in subjects:
            raise SyncError(f"{r['sync']} contains a `{METADATA_SUBJECT}` commit; leave it out")
        heads = {b["repo"]: b["head"] for b in state["branches"][:i] if b["status"] != "pending"}
        targets = pin_targets(gh, fork, fork["branches"][i], heads)
        git(wd, "checkout", "--quiet", "--detach", head)
        new_head = write_metadata(gh, fork, wd, targets, refresh_lock=not args.no_lock)
        gh.push(wd, f"--force-with-lease=refs/heads/{r['sync']}:{head}", "origin",
                f"HEAD:refs/heads/{r['sync']}")
        rds = [(r["repo"], git(wd, "range-diff", "--no-color", f"{r['old_base']}..{r['old_head']}",
                               f"{r['new_base']}..{new_head}"))]
    r.update(status="resolved", resolver=args.user, head=new_head, pins=targets)
    for key in ("conflict", "files", "remaining"):
        r.pop(key, None)

    # Carry on with the branches after this one, which may conflict in turn.
    if any(b["status"] == "pending" for b in state["branches"]):
        with tempfile.TemporaryDirectory() as workroot:
            state["branches"] = sync_fork(gh, fork, workroot, state["date"], not args.no_lock,
                                          start=i + 1, prior=state["branches"][: i + 1])
        rds += take_range_diffs(state["branches"])

    done = status_label(state) != "sync:conflict"
    if done:
        exclude = {state["syncer"]} | {b["resolver"] for b in state["branches"] if b.get("resolver")}
        state["reviewer"] = sync_reviewer(fork["members"], state["date"], exclude)
    gh.edit_issue(args.issue, body=render_issue(gh, fork, state),
                  add_labels=[status_label(state)],
                  remove_labels=[x for x in SYNC_LABELS if x != status_label(state)],
                  assignees=[state["reviewer"]] if done and state.get("reviewer") else [])
    if done:
        who = (f"@{state['reviewer']}, please test and review it" if state.get("reviewer")
               else "This fork needs another member to test and review it")
        gh.comment(args.issue, f"Resolved. {who}: run the checks, and read the resolution, which is "
                               "the hunks that changed against the patches as they were.\n\n"
                               + range_diff_comment(rds))
    else:
        gh.comment(args.issue, "Resolved; the next branch conflicts too (above).\n\n" + range_diff_comment(rds))


def promote(gh, fork, state, args):
    """The test-and-review: whoever promotes attests that the checks pass and the range-diff
    holds, and is credited with it. It can't be someone who synced or resolved this sync."""
    solo = gh.cfg["review"].get("solo")
    for r in state["branches"]:
        if r["status"] in ("conflict", "pending"):
            raise SyncError(f"{r['repo']} is still {r['status']}")
        if r["status"] == "resolved" and r["resolver"] == args.user and not solo:
            raise SyncError(f"@{args.user} resolved {r['repo']}; another member has to test and review it")
    if state["syncer"] == args.user and not solo:
        raise SyncError(f"@{args.user} ran this sync; another member has to test and review it")
    org = gh.cfg["org"]
    with tempfile.TemporaryDirectory() as workroot:
        pushes = []
        for r in state["branches"]:
            if r["status"] == "current":
                continue
            url = gh.url(org, r["repo"])
            remote = dict(line.split("\t")[::-1] for line in
                          run(["git", "ls-remote", url, f"refs/heads/{r['sync']}",
                               f"refs/heads/{r['branch']}"]).stdout.splitlines())
            if remote.get(f"refs/heads/{r['sync']}") != r["head"]:
                raise SyncError(f"{r['sync']} has moved since it was checked; comment /ready again")
            if remote.get(f"refs/heads/{r['branch']}") != r["old_head"]:
                raise SyncError(f"{r['branch']} in {r['repo']} has new commits since the sync started. "
                                "Don't merge into a fork while it syncs; close this issue and sync again.")
            pushes.append((r, url))
        for r, url in pushes:
            wd = Path(workroot) / r["repo"]
            run(["git", "init", "--quiet", str(wd)])
            git(wd, "fetch", "--quiet", "--filter=blob:none", url, r["old_head"], r["head"])
            tag = f"archive/{fork['id']}/{state['date']}"
            gh.push(wd, url, f"{r['old_head']}:refs/tags/{tag}")
            gh.push(wd, f"--force-with-lease=refs/heads/{r['branch']}:{r['old_head']}", url,
                    f"{r['head']}:refs/heads/{r['branch']}")
            r["status"] = "promoted"
    state["promoter"] = args.user
    gh.edit_issue(args.issue, body=render_issue(gh, fork, state))
    message = (f"Tested, reviewed and promoted by @{args.user}. The old heads are tagged "
               f"`archive/{fork['id']}/{state['date']}`.")
    kids = children(gh.cfg, fork["id"])
    if kids:
        message += ("\n\nThese forks build on this one and are now behind it: "
                    + ", ".join(f"`{k}`" for k in kids) + ". Any of their members can `/sync` them.")
    gh.close_issue(args.issue, message)


# ---------------------------------------------------------------------------------------------
# Reviewers for patches


CHECKLIST = """Reviewers for this patch to **{fork}**: {reviewers}. Chosen at random from the fork's members; see PROCESS.md.

Judge it against the ADR: {adr}
- [ ] **Conformance:** it serves the ADR's decision and is within its scope. Quote the line it serves, or the line it breaks.
- [ ] **Safety:** look for network calls, process spawning, `unsafe`, build scripts, CI changes and new dependencies.
- [ ] **Quality:** it follows the project's conventions (`CLAUDE.md`, `AGENTS.md`), has tests, and stays upstream-ready.
{extra}
Out of scope isn't the same as bad: it may belong in a child fork with its own ADR."""


def cmd_assign(gh, args):
    cfg = gh.cfg
    k = cfg["review"]["approvals"]
    for fork in cfg["forks"]:
        adr = f"https://github.com/{gh.pilot}/tree/main/{fork['adr']}"
        for b in fork["branches"]:
            repo = f"{cfg['org']}/{b['repo']}"
            prs = json.loads(gh.gh("pr", "list", "-R", repo, "--base", b["branch"], "--state", "open",
                                   "--json", "number,author,labels"))
            for pr in prs:
                if any(label["name"] == "pilot:assigned" for label in pr["labels"]):
                    continue
                author = pr["author"]["login"]
                reviewers = patch_reviewers(fork["members"], author, f"{repo}#{pr['number']}", k)
                extra = ""
                if fork["id"] == "web-host":
                    extra = "- [ ] The author has labelled it with its area: `area:bridge`, `area:storage`, `area:size` or `area:resources`.\n"
                if len(reviewers) < k:
                    extra += f"\nThis fork has too few members for {k} reviewers; another member is needed.\n"
                body = CHECKLIST.format(fork=fork["id"], adr=adr, extra=extra,
                                        reviewers=", ".join(f"@{r}" for r in reviewers) or "nobody yet")
                if gh.dry:
                    print(f"[dry-run] {repo}#{pr['number']}: reviewers {reviewers}")
                    continue
                if reviewers:
                    gh.gh("pr", "edit", str(pr["number"]), "-R", repo,
                          *sum([["--add-reviewer", r] for r in reviewers], []))
                gh.gh("pr", "edit", str(pr["number"]), "-R", repo, "--add-label", "pilot:assigned")
                gh.comment(pr["number"], body, repo=repo)
            mark_open(gh, fork, repo, b["branch"])


def mark_open(gh, fork, repo, branch):
    """A draft fork opens, freezing its 0001, once a patch has been admitted to it."""
    if fork["status"] != "draft":
        return
    merged = json.loads(gh.gh("pr", "list", "-R", repo, "--base", branch, "--state", "merged",
                              "--limit", "1", "--json", "number"))
    if merged:
        set_fork(gh, fork["id"], lambda f: f.update(status="open"),
                 f"{fork['id']}: opens with {repo}#{merged[0]['number']}; 0001 is now frozen")
        fork["status"] = "open"


# ---------------------------------------------------------------------------------------------
# Decisions: every change to this repository is a PR that the people it binds vote on


VOTE_RE = re.compile(r"<!-- pilot-vote (\{.*?\}) -->", re.S)
FORK_PATH_RE = re.compile(r"forks/([^/]+)/(.+)$")


def classify(files, cfg):
    """The scope of a PR from its changed files: ("fork", id, new) or ("process", None, False).
    Refuses PRs that span scopes, edit adopted records, or set members (which are self-service)."""
    scopes = set()
    for f in files:
        m = FORK_PATH_RE.match(f["filename"])
        scopes.add(m.group(1) if m else None)
    if len(scopes) != 1:
        raise SyncError("a PR changes one fork, or the process, never more than one; split it")
    fork_id = scopes.pop()
    if fork_id is None:
        return "process", None, False
    if not FORK_ID_RE.match(fork_id):
        raise SyncError(f"{fork_id!r} isn't a fork id: lowercase letters, digits and hyphens")
    existing = {f["id"]: f for f in cfg["forks"]}.get(fork_id)
    paths = {FORK_PATH_RE.match(f["filename"]).group(2): f["status"] for f in files}
    if existing is None:
        if paths.get("fork.json") != "added" or not any(
                p.startswith("adr/0001-") and st == "added" for p, st in paths.items()):
            raise SyncError("a new fork adds forks/<id>/fork.json and forks/<id>/adr/0001-<slug>.md")
        return "fork", fork_id, True
    for path, status in paths.items():
        if not path.startswith("adr/"):
            continue
        if status == "added":
            if not RECORD_RE.match(path[4:]):
                raise SyncError(f"{path} isn't named adr/NNNN-slug.md")
            continue
        if path.startswith("adr/0001-") and existing["status"] == "draft" and status == "modified":
            continue
        raise SyncError(f"{path} is an adopted record and can't change; add a new record that supersedes it")
    return "fork", fork_id, False


def voters(cfg, scope, fork_id, new, author, joiners):
    """Who may vote: a fork's members on its own changes; for a new fork, its proposer and
    whoever joined on the PR; for the process, every member of every fork."""
    if scope == "process":
        return sorted({m for f in cfg["forks"] for m in f["members"]})
    if new:
        return sorted({author, *joiners})
    return list(fork_by_id(cfg, fork_id)["members"])


def tally(state, head, eligible, rules, now):
    """(passed, what it waits for, yes, no) for the votes cast on the PR's current head."""
    cast = {u: v for u, v in state["votes"].items() if v["sha"] == head and u in eligible}
    yes = sum(1 for v in cast.values() if v["choice"] == "yes")
    no = len(cast) - yes
    need = min(rules["quorum"], len(eligible))
    waits = []
    if not eligible:
        waits.append("nobody is eligible to vote")
    opened = datetime.datetime.fromisoformat(state["heads"][head])
    closes = opened + datetime.timedelta(hours=rules["hours"])
    if now < closes:
        waits.append(f"discussion stays open until {closes:%Y-%m-%d %H:%M} UTC")
    if len(cast) < need:
        waits.append(f"{len(cast)} of the {need} votes needed")
    if cast and yes < rules["majority"] * len(cast):
        waits.append(f"yes from {rules['majority']:.0%} of the votes cast")
    return not waits, waits, yes, no


def parse_vote(body):
    """("yes"|"no", reason) from a /vote comment. A no needs a reason."""
    words = body.strip().split(None, 2)
    if len(words) < 2 or words[0] != "/vote" or words[1] not in ("yes", "no"):
        raise SyncError("vote with `/vote yes`, or `/vote no` followed by the reason")
    reason = words[2].strip() if len(words) > 2 else ""
    if words[1] == "no" and not reason:
        raise SyncError("a no vote needs a reason, so the proposal can be changed to meet it")
    return words[1], reason


def render_tally(state, eligible, result):
    passed, waits, yes, no = result
    lines = [f"**Vote on this {state['scope']}** (PROCESS.md, Decisions). Eligible: "
             + (", ".join(eligible) or "nobody") + ".", "",
             f"On `{state['head'][:10]}`: {yes} yes, {no} no. "
             + ("It passes, and the bot merges it." if passed else "Waiting for: " + "; ".join(waits) + ".")]
    for user, v in sorted(state["votes"].items()):
        if v["sha"] == state["head"] and v["choice"] == "no":
            lines.append(f"- {user}, no: {v['reason']}")
    stale = sorted(u for u, v in state["votes"].items() if v["sha"] != state["head"])
    if stale:
        lines.append(f"\nVotes on earlier versions don't count; vote again: {', '.join(stale)}.")
    lines += ["", "Comment `/vote yes` or `/vote no <reason>`. Changing the PR resets the votes."]
    if state.get("new"):
        lines.append("To become a member of this new fork, and vote, comment `/join`.")
    lines.append(f"<!-- pilot-vote {json.dumps(state, sort_keys=True)} -->")
    return "\n".join(lines)


def cmd_vote(gh, args):
    """Records a /vote or /join on a PR (with --user and --body), re-tallies, and merges the PR
    when it passes. Without --pr it re-tallies every open PR."""
    numbers = [args.pr] if args.pr else [
        p["number"] for p in json.loads(gh.gh("pr", "list", "-R", gh.pilot, "--state", "open",
                                              "--json", "number"))]
    for number in numbers:
        try:
            vote_on(gh, number, args.user, args.body)
        except SyncError as e:
            gh.comment(number, f"Not counted: {e}")


def vote_on(gh, number, user=None, body=None):
    cfg = gh.cfg
    pr = json.loads(gh.gh("pr", "view", str(number), "-R", gh.pilot,
                          "--json", "author,headRefOid,isDraft,state"))
    if pr["state"] != "OPEN":
        return
    files = gh.api(f"repos/{gh.pilot}/pulls/{number}/files?per_page=100")
    scope, fork_id, new = classify(files, cfg)
    if scope == "fork" and not new:
        check_members_unchanged(gh, fork_by_id(cfg, fork_id), f"refs/pull/{number}/head")
    bot = gh.login()
    # Only the bot's own comment holds the tally; anyone can paste the marker.
    mine = [c for c in gh.comments(number) if c["login"] == bot and VOTE_RE.search(c["body"])]
    state = (json.loads(VOTE_RE.search(mine[-1]["body"]).group(1)) if mine
             else {"votes": {}, "heads": {}, "joiners": []})
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    head = pr["headRefOid"]
    state.update(scope=f"fork {fork_id}" if fork_id else "process change", new=new, head=head)
    state["heads"].setdefault(head, now.isoformat())
    author = pr["author"]["login"]
    who = (("process" if fork_id is None else "fork"), fork_id, new)

    if body:
        word = body.strip().split()[0]
        if word == "/join":
            if not new:
                raise SyncError(f"join an existing fork with `/join {fork_id}` on an issue")
            if cfg["review"].get("solo"):
                raise SyncError("the pilot is in its solo phase; joining opens when it ends")
            check_account(gh, user)
            if user not in state["joiners"] and user != author:
                state["joiners"].append(user)
        elif word == "/vote":
            choice, reason = parse_vote(body)
            eligible = voters(cfg, *who, author, state["joiners"])
            if user not in eligible:
                raise SyncError(f"@{user} can't vote on this; eligible: {', '.join(eligible) or 'nobody'}")
            check_account(gh, user)
            state["votes"][user] = {"choice": choice, "reason": reason, "sha": head, "at": now.isoformat()}

    eligible = voters(cfg, *who, author, state["joiners"])
    result = tally(state, head, eligible, cfg["votes"], now)
    text = render_tally(state, eligible, result)
    if mine:
        gh.edit_comment(mine[-1]["id"], text)
    else:
        gh.comment(number, text)
    if result[0] and not pr["isDraft"]:
        gh.merge_pr(number, head)
        if new:
            set_fork(gh, fork_id, lambda f: f.update(members=[author, *state["joiners"]]),
                     f"{fork_id}: founding members")


def check_members_unchanged(gh, fork, ref):
    head = json.loads(gh.file_at(gh.pilot, f"forks/{fork['id']}/fork.json", ref))
    if sorted(head.get("members", [])) != sorted(fork["members"]):
        raise SyncError("members change only through `/join` and `/leave`, not in a PR")


def check_account(gh, user):
    """Fails closed: an account younger than the minimum, or one that can't be looked up, can't
    vote or join."""
    info = gh.api(f"users/{user}", check=False)
    if not info or "created_at" not in info:
        raise SyncError(f"couldn't look up @{user}")
    created = datetime.datetime.fromisoformat(info["created_at"].replace("Z", "+00:00"))
    days = gh.cfg["votes"]["min_account_age_days"]
    if (datetime.datetime.now(datetime.timezone.utc) - created).days < days:
        raise SyncError(f"@{user}'s account is younger than {days} days")


def set_fork(gh, fork_id, mutate, message):
    """Changes forks/<id>/fork.json on main as the bot, through the contents API."""
    import base64
    path = f"forks/{fork_id}/fork.json"
    current = gh.api(f"repos/{gh.pilot}/contents/{path}?ref=main")
    fork = json.loads(base64.b64decode(current["content"]))
    mutate(fork)
    text = json.dumps(fork, indent=2) + "\n"
    if gh.dry:
        print(f"[dry-run] {message}:\n{text}")
        return
    gh.api(f"repos/{gh.pilot}/contents/{path}", "PUT", {
        "message": message, "sha": current["sha"], "branch": "main",
        "content": base64.b64encode(text.encode()).decode()})


def cmd_member(gh, args):
    """`/join <fork>` and `/leave <fork>` on an issue. Membership is open: joining takes on the
    fork's reviewers and gives a vote on its decisions."""
    try:
        words = args.body.strip().split()
        if len(words) < 2 or words[0] not in ("/join", "/leave"):
            raise SyncError("use `/join <fork>` or `/leave <fork>`")
        fork = fork_by_id(gh.cfg, words[1])
        user = args.user
        if words[0] == "/join":
            if gh.cfg["review"].get("solo"):
                raise SyncError("the pilot is in its solo phase; joining opens when it ends")
            if user in fork["members"]:
                raise SyncError(f"@{user} is already a member of {fork['id']}")
            check_account(gh, user)
            set_fork(gh, fork["id"], lambda f: f["members"].append(user), f"{fork['id']}: @{user} joins")
            gh.comment(args.issue, f"Welcome to {fork['id']}, @{user}. You can now `/sync` it, and "
                                   "you'll be asked to review its patches and syncs.")
        else:
            if user not in fork["members"]:
                raise SyncError(f"@{user} isn't a member of {fork['id']}")
            set_fork(gh, fork["id"], lambda f: f["members"].remove(user), f"{fork['id']}: @{user} leaves")
            gh.comment(args.issue, f"@{user} has left {fork['id']}.")
    except SyncError as e:
        gh.comment(args.issue, f"Not done: {e}")


# ---------------------------------------------------------------------------------------------
# Setup, ledger, check


def cmd_setup(gh, args):
    """Forks the repositories into the org, makes each fork's team and branch, and protects the
    fork branches. Safe to run again: it skips what exists. The first run is an org owner's, by
    hand: the App can't fork another account's repositories. After that, the App keeps it current."""
    cfg = gh.cfg
    org, up = cfg["org"], cfg["upstream_owner"]

    def act(what, fn):
        print(("[dry-run] " if gh.dry else "") + what)
        if not gh.dry:
            fn()

    bypass = [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]
    if cfg.get("app_id"):
        bypass.append({"actor_id": cfg["app_id"], "actor_type": "Integration", "bypass_mode": "always"})

    for repo in cfg["repos"]:
        if gh.api(f"repos/{org}/{repo}", check=False) is None:
            if gh.app:
                raise SyncError(f"{org}/{repo} doesn't exist, and the App can't fork it: an org owner "
                                "runs `pilot.py setup` once by hand (SETUP.md)")
            act(f"fork {up}/{repo} into {org}",
                lambda: gh.gh("repo", "fork", f"{up}/{repo}", "--org", org, "--clone=false",
                              "--default-branch-only"))
            # GitHub makes a fork in the background; branches can't be made in it until it has one.
            for _ in range(60 if not gh.dry else 0):
                if gh.api(f"repos/{org}/{repo}/git/ref/heads/main", check=False) is not None:
                    break
                time.sleep(5)
            else:
                if not gh.dry:
                    raise SyncError(f"{org}/{repo} was forked but isn't ready after five minutes")
        act(f"{org}/{repo}: squash merges only",
            lambda: gh.api(f"repos/{org}/{repo}", "PATCH", {
                "allow_squash_merge": True, "allow_merge_commit": False, "allow_rebase_merge": False,
                "delete_branch_on_merge": True}))
        labels = ["pilot:assigned"] + (["area:bridge", "area:storage", "area:size", "area:resources"]
                                       if repo == "vectorcraft" else [])
        for label in labels:
            act(f"{org}/{repo}: label {label}",
                lambda: gh.api(f"repos/{org}/{repo}/labels", "POST", {"name": label}, check=False))
        rulesets = gh.api(f"repos/{org}/{repo}/rulesets", check=False) or []
        existing = next((r["id"] for r in rulesets if r["name"] == "pilot forks"), None)
        path, method = ((f"repos/{org}/{repo}/rulesets/{existing}", "PUT") if existing
                        else (f"repos/{org}/{repo}/rulesets", "POST"))
        # Created, or updated so that a change to review in pilot.json takes effect.
        act(f"{org}/{repo}: protect fork/*", lambda: gh.api(path, method, {
            "name": "pilot forks", "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"include": ["refs/heads/fork/*"], "exclude": []}},
            # The App bypasses, so it can promote syncs; so do repository admins (org owners).
            "bypass_actors": bypass,
            "rules": [
                {"type": "deletion"},
                {"type": "non_fast_forward"},
                {"type": "pull_request", "parameters": {
                    "required_approving_review_count": cfg["review"]["approvals"],
                    "require_code_owner_review": cfg["review"]["approvals"] > 0,
                    "dismiss_stale_reviews_on_push": True,
                    "require_last_push_approval": True,
                    "required_review_thread_resolution": False,
                    "allowed_merge_methods": ["squash"]}},
            ]}))

    # Nothing reaches the pilot's main except a passed vote, which the bot merges.
    rulesets = gh.api(f"repos/{gh.pilot}/rulesets", check=False) or []
    existing = next((r["id"] for r in rulesets if r["name"] == "decisions by vote"), None)
    path, method = ((f"repos/{gh.pilot}/rulesets/{existing}", "PUT") if existing
                    else (f"repos/{gh.pilot}/rulesets", "POST"))
    act(f"{gh.pilot}: main takes only voted PRs", lambda: gh.api(path, method, {
        "name": "decisions by vote", "target": "branch", "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "bypass_actors": bypass,
        "rules": [{"type": "deletion"}, {"type": "non_fast_forward"},
                  {"type": "pull_request", "parameters": {
                      "required_approving_review_count": 0, "require_code_owner_review": False,
                      "dismiss_stale_reviews_on_push": False, "require_last_push_approval": False,
                      "required_review_thread_resolution": False, "allowed_merge_methods": ["squash"]}}]}))

    for label in ("sync", "sync-failed") + SYNC_LABELS + tuple(f"fork:{f['id']}" for f in cfg["forks"]):
        act(f"{gh.pilot}: label {label}",
            lambda: gh.api(f"repos/{gh.pilot}/labels", "POST", {"name": label}, check=False))

    for fork in cfg["forks"]:
        team = fork["id"]
        if gh.api(f"orgs/{org}/teams/{team}", check=False) is None:
            act(f"team {team}", lambda: gh.api(f"orgs/{org}/teams", "POST",
                                               {"name": team, "privacy": "closed"}))
        for m in fork["members"]:
            act(f"team {team}: add @{m}",
                lambda: gh.api(f"orgs/{org}/teams/{team}/memberships/{m}", "PUT", {"role": "member"}))
        for b in fork["branches"]:
            repo = b["repo"]
            act(f"team {team}: push on {org}/{repo}",
                lambda: gh.api(f"orgs/{org}/teams/{team}/repos/{org}/{repo}", "PUT", {"permission": "push"}))
            if gh.api(f"repos/{org}/{repo}/git/ref/heads/{b['branch']}", check=False) is None:
                if b["base"] == "main":
                    sha = gh.api(f"repos/{up}/{repo}/commits/main")["sha"]
                else:
                    # From upstream alone, so that it works before the org's copy exists (--dry-run).
                    with tempfile.TemporaryDirectory() as workroot:
                        wd = Path(workroot) / repo
                        run(["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout",
                             "--origin", "upstream", gh.url(up, repo), str(wd)])
                        sha = resolve_base(gh, wd, repo, b["base"])
                act(f"{org}/{repo}: branch {b['branch']} at {sha[:10]}",
                    lambda: gh.api(f"repos/{org}/{repo}/git/refs", "POST",
                                   {"ref": f"refs/heads/{b['branch']}", "sha": sha}))
    print("Now run the sync workflow once per fork, in order, to write each branch's metadata commit.")


LEDGER_COLUMNS = ["patches", "reviews", "syncs", "resolutions", "sync reviews"]


def sync_credit(state):
    """(user, column) credits for one sync. Only a promoted sync counts: the syncer for the
    sync, each resolver for a conflict they resolved, and the promoter for testing and
    reviewing it."""
    if not state.get("promoter"):
        return []
    credits = [(state["syncer"], "syncs")]
    credits += [(b["resolver"], "resolutions") for b in state["branches"] if b.get("resolver")]
    credits.append((state["promoter"], "sync reviews"))
    return credits


def cmd_ledger(gh, args):
    """Who has done what: admitted patches, patch reviews, syncs, conflict resolutions, and
    the tests and reviews of syncs."""
    cfg = gh.cfg
    counts = {}

    def add(user, column):
        counts.setdefault(user, dict.fromkeys(LEDGER_COLUMNS, 0))
        counts[user][column] += 1

    for fork in cfg["forks"]:
        for b in fork["branches"]:
            repo = f"{cfg['org']}/{b['repo']}"
            prs = json.loads(gh.gh("pr", "list", "-R", repo, "--base", b["branch"], "--state", "merged",
                                   "--limit", "1000", "--json", "author,reviews"))
            for pr in prs:
                add(pr["author"]["login"], "patches")
                for reviewer in {r["author"]["login"] for r in pr["reviews"] if r["state"] == "APPROVED"}:
                    add(reviewer, "reviews")
    issues = json.loads(gh.gh("issue", "list", "-R", gh.pilot, "--label", "sync", "--state", "closed",
                              "--limit", "1000", "--json", "body"))
    for issue in issues:
        try:
            state = read_state(issue["body"])
        except SyncError:
            continue
        for user, column in sync_credit(state):
            add(user, column)
    print(f"{'member':24}" + "".join(f"{c:>14}" for c in LEDGER_COLUMNS))
    for user, c in sorted(counts.items()):
        print(f"{user:24}" + "".join(f"{c[col]:>14}" for col in LEDGER_COLUMNS))


def cmd_invites(gh, args):
    """Prints each invitation in invites/roster.csv, filled in from invites/templates.md."""
    import csv
    sections, name = {}, None
    for line in (ROOT / "invites" / "templates.md").read_text().splitlines():
        if line.startswith("## "):
            name = line[3:].strip()
            sections[name] = []
        elif name:
            sections[name].append(line)
    sections = {k: "\n".join(v).strip() for k, v in sections.items()}
    with open(ROOT / "invites" / "roster.csv", newline="") as f:
        for row in csv.DictReader(f):
            if args.fork and row["fork"] != args.fork:
                continue
            fork = fork_by_id(gh.cfg, row["fork"])
            owner_repo, number = row["item"].split("#")
            kind = "issues"  # GitHub redirects an issue URL to the PR when it is one
            message = sections[f"role:{row['role']}"].format(
                handle=row["handle"], item=row["item"], note=row["note"], fork=fork["id"],
                adr=f"https://github.com/{gh.pilot}/tree/main/{fork['adr']}", org=gh.cfg["org"],
                pitch=sections[f"fork:{fork['id']}"])
            print(f"### @{row['handle']}: https://github.com/{owner_repo}/{kind}/{number}\n\n{message}\n")


def cmd_check(gh, args):
    problems = validate(gh.cfg)
    for p in problems:
        print(p)
    if problems:
        sys.exit(1)
    print("the configuration is sound")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dry-run", action="store_true", help="print GitHub writes instead of making them")
    p.add_argument("--root", help="the pilot repository to read (default: this one)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync", help="sync a fork on demand, credited to --user")
    s.add_argument("fork")
    s.add_argument("--user", required=True, help="the member running the sync")
    s.add_argument("--no-lock", action="store_true", help="don't refresh Cargo.lock (no cargo needed)")
    c = sub.add_parser("command", help="handle a /sync, /ready or /promote comment")
    c.add_argument("--issue", type=int, required=True)
    c.add_argument("--user", required=True)
    c.add_argument("--body", required=True)
    c.add_argument("--no-lock", action="store_true")
    v = sub.add_parser("vote", help="record a /vote or /join on a PR and re-tally; no --pr: every open PR")
    v.add_argument("--pr", type=int)
    v.add_argument("--user")
    v.add_argument("--body")
    m = sub.add_parser("member", help="handle /join <fork> or /leave <fork> on an issue")
    m.add_argument("--issue", type=int, required=True)
    m.add_argument("--user", required=True)
    m.add_argument("--body", required=True)
    sub.add_parser("assign", help="assign reviewers to new patch PRs")
    sub.add_parser("setup", help="create the org's forks, teams, branches and rules")
    sub.add_parser("ledger", help="count each member's work")
    sub.add_parser("check", help="validate pilot.json and the forks")
    i = sub.add_parser("invites", help="print the invitations from invites/")
    i.add_argument("--fork")
    args = p.parse_args(argv)
    if os.environ.get("GITHUB_ACTIONS") and not os.environ.get("PILOT_APP_KEY") and args.cmd not in ("check", "invites"):
        print("PILOT_APP_KEY isn't set, so the bot does nothing yet (SETUP.md).")
        return
    cfg = load_config(args.root)
    gh = (DryGitHub if args.dry_run else GitHub)(cfg, os.environ.get("PILOT_GIT_BASE", "https://github.com"))
    handlers = {"sync": cmd_sync, "command": cmd_command, "assign": cmd_assign, "setup": cmd_setup,
                "ledger": cmd_ledger, "check": cmd_check, "invites": cmd_invites,
                "vote": cmd_vote, "member": cmd_member}
    try:
        handlers[args.cmd](gh, args)
    except SyncError as e:
        sys.exit(f"pilot: {e}")


if __name__ == "__main__":
    main()
