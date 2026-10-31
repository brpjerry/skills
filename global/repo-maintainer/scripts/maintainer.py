#!/usr/bin/env python3
"""Timer runner for the repo-maintainer skill.

Systemd timers call `maintainer.py run <slug> <task>`. The runner takes the
repo's lock, fetches in the owner's checkout and checks whether the task has
anything to do. Only then does it start a headless Claude Code or Codex
session in the checkout, handing it the facts in <run-dir>/context.json, and
afterwards it puts the checkout back where the owner left it. Steps that need
no judgment (fast-forwarding a fork's mirror branch, fast-forwarding local
branches, a quiet day's report) run without an agent. Every agent run is kept
as a session the owner can resume with `maintainer.py resume`.

Configs live in ~/.config/repo-maintainer/<slug>.toml; state, run records and
run directories in ~/.local/state/repo-maintainer/<slug>/.
"""
import argparse
import collections
import contextlib
import dataclasses
import datetime as dt
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import tomllib
import urllib.parse
from pathlib import Path
from typing import Callable

SCRIPT = Path(__file__).absolute()  # not resolved, so units keep using the global skill link
SKILL_DIR = SCRIPT.parent.parent
SKILL = "repo-maintainer"
CONFIG_DIR = Path.home() / ".config" / SKILL
STATE_DIR = Path.home() / ".local/state" / SKILL
UNIT_DIR = Path.home() / ".config/systemd/user"
TASKS = ("prs", "deps", "sync", "scan", "report")
CHECKOUT_TASKS = ("prs", "deps", "sync", "scan")
GITHUB_TASKS = ("prs", "deps")
FORGES = ("github", "forgejo", "git")
MARKER = "<!-- repo-maintainer -->"
LOCK_WAIT_MINUTES = 180
REPO_RE = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
WRITE_ACCESS = ("ADMIN", "MAINTAIN", "WRITE")
DEFAULT_PORTS = {"https": 443, "http": 80, "ssh": 22, "git": 9418}

# Never part of the job, whatever the agent reads. Claude only; Codex relies on its sandbox and the skill's rules.
CLAUDE_DENY = [
    "Bash(gh auth *)", "Bash(gh secret *)", "Bash(gh variable *)", "Bash(gh ssh-key *)", "Bash(gh gpg-key *)",
    "Bash(gh repo delete *)", "Bash(gh repo edit *)", "Bash(gh repo archive *)", "Bash(gh repo rename *)",
    "Bash(gh release create *)", "Bash(gh release delete *)", "Bash(gh workflow disable *)",
    "Bash(gh pr merge * --admin*)", "Bash(gh pr merge --admin*)", "Bash(fj auth *)", "Bash(fj repo delete *)",
    "Bash(fj release *)", "Bash(fj tag *)", "Bash(git push --force *)", "Bash(git push -f *)",
    "Bash(git push * --force)", "Bash(git push * --force *)", "Bash(git push * -f)", "Bash(git push * -f *)",
    "Bash(git push * +*)", "Bash(git push * :*)", "Bash(git push --mirror*)", "Bash(git push * --mirror*)",
    "Bash(git push --delete *)", "Bash(git push -d *)", "Bash(git push * --delete *)", "Bash(git push * -d *)",
    "Bash(sudo *)",
]

# Files a dependency update may change: manifests, lockfiles, workflow and container files.
DEP_FILE = re.compile(r"""(^|/)(
    package\.json | package-lock\.json | npm-shrinkwrap\.json | yarn\.lock | pnpm-lock\.yaml | bun\.lockb? |
    Cargo\.toml | Cargo\.lock | go\.mod | go\.sum | go\.work(\.sum)? |
    requirements[^/]*\.(txt|in) | pyproject\.toml | poetry\.lock | uv\.lock | pdm\.lock | Pipfile(\.lock)? | setup\.cfg |
    Gemfile(\.lock)? | [^/]+\.gemspec | composer\.(json|lock) | mix\.(exs|lock) | pubspec\.(yaml|lock) |
    pom\.xml | build\.gradle(\.kts)? | settings\.gradle(\.kts)? | libs\.versions\.toml | gradle-wrapper\.properties |
    [^/]+\.(cs|fs|vb)proj | packages\.config | packages\.lock\.json | Directory\.Packages\.props | global\.json |
    Package\.swift | Package\.resolved | Podfile(\.lock)? |
    [^/]*Dockerfile[^/]* | Containerfile | (docker-)?compose[^/]*\.ya?ml |
    flake\.lock | \.pre-commit-config\.yaml | \.terraform\.lock\.hcl | [^/]+\.tf | Chart\.(yaml|lock) | devcontainer\.json
)$ | (^|/)\.github/workflows/[^/]+\.ya?ml$ | (^|/)action\.ya?ml$""", re.X)
UPDATE_TYPE = re.compile(r"update-type:\s*version-update:semver-(major|minor|patch)")
FAILED = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR"}
PR_FIELDS = ("number,title,url,author,headRefName,headRefOid,baseRefName,isDraft,isCrossRepository,"
             "mergeable,reviewDecision,statusCheckRollup,createdAt,updatedAt,comments,reviews")
# Operations an interrupted git command leaves behind, keyed by the file that marks them, with how to undo them.
IN_PROGRESS = {"MERGE_HEAD": ("merge", "--abort"), "rebase-merge": ("rebase", "--abort"),
               "rebase-apply": ("rebase", "--abort"), "CHERRY_PICK_HEAD": ("cherry-pick", "--abort"),
               "REVERT_HEAD": ("revert", "--abort"), "BISECT_LOG": ("bisect", "reset")}

# Children never wait on a prompt or an editor, and never inherit a repo from whatever started the runner
# (git exports these to hooks): every git command names its checkout with -C.
GIT_LOCAL_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR",
                  "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PREFIX", "GIT_NAMESPACE")
ENV = {k: v for k, v in os.environ.items() if k not in GIT_LOCAL_VARS} | {
    "GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true", "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"}


class Failure(Exception):
    """Stops a run; it's recorded as failed."""


def sh(cmd, cwd=None, check=True, timeout=600, strip=True):
    try:
        p = subprocess.run(cmd, cwd=cwd, env=ENV, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Failure(f"`{' '.join(cmd[:4])}` timed out after {timeout}s")
    if check and p.returncode:
        raise Failure(f"`{' '.join(cmd[:4])}` exited {p.returncode}: {(p.stderr or p.stdout).strip()[-400:]}")
    return p.stdout.strip() if strip else p.stdout


def nul_list(out):
    """Paths from -z output, kept exactly (no stripping: a name may start with a space)."""
    return [p for p in out.split("\0") if p]


def gh(*args, **kw):
    return sh(["gh", *args], **kw)


def gh_json(*args, **kw):
    return json.loads(gh(*args, **kw) or "null")


def gh_pages(path):
    return [x for page in gh_json("api", "--paginate", "--slurp", path) for x in page]


def now():
    return dt.datetime.now().astimezone()


def days_since(iso, t):
    return (t - dt.datetime.fromisoformat(iso)).days


def die(msg):
    sys.exit(f"maintainer: {msg}")


def load_toml(path):
    with open(path, "rb") as f:
        return tomllib.load(f)


def merge(base, over):
    out = dict(base)
    for k, v in over.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def slugify(repo):
    """owner_name on GitHub, host_owner_name elsewhere, so the same owner/name on two hosts can't collide."""
    return "_".join(repo.lower().split("/"))


def github_repo(url):
    """owner/name if `url` is a github.com remote, else None."""
    parsed = parse_remote(url)
    if parsed and parsed[0].lower() == "github.com" and REPO_RE.fullmatch(parsed[2]):
        return parsed[2]
    return None


def parse_remote(url):
    """(host, port, owner/name) of a git URL such as ssh://u@h:2222/o/r.git, u@h:o/r.git or https://h/o/r."""
    url = url.strip()
    if "://" in url:
        u = urllib.parse.urlsplit(url)
        if not u.hostname:
            return None
        return u.hostname, u.port or DEFAULT_PORTS.get(u.scheme, 22), u.path.strip("/").removesuffix(".git")
    m = re.match(r"^(?:[^@/]+@)?([^:/]+):(.+)$", url)
    return (m.group(1), 22, m.group(2).strip("/").removesuffix(".git")) if m else None


def forgejo_host(host):
    """The fj login, if any, for the Forgejo server at `host` (matched by address, since remotes often use an IP)."""
    if not shutil.which("fj"):
        return None
    try:
        logins = sh(["fj", "auth", "list"], cwd=Path.home()).split()
        addr = socket.gethostbyname(host)
    except (Failure, OSError):
        return None
    for login in logins:
        name = login.split("@")[-1]
        with contextlib.suppress(OSError):
            if name == host or socket.gethostbyname(name) == addr:
                return name
    return None


def find_slug(arg):
    """Accept a slug, owner/repo, or a path inside a configured checkout."""
    path = Path(arg).expanduser()
    if path.exists():
        top = sh(["git", "-C", str(path), "rev-parse", "--show-toplevel"], check=False)
        for f in sorted(CONFIG_DIR.glob("*.toml")):
            if load_toml(f).get("checkout") == top:
                return f.stem
        die(f"no {SKILL} config for the checkout at {top or path}; create one with: maintainer.py add")
    return slugify(arg) if "/" in arg else arg


class Repo:
    """One maintained repo: its config, its state directory, and the owner's checkout."""

    def __init__(self, slug):
        self.slug = slug
        self.config = CONFIG_DIR / f"{slug}.toml"
        self.dir = STATE_DIR / slug
        self.runs_dir = self.dir / "runs"
        self.reports = self.dir / "reports"
        self.checkout = None

    def git(self, *args, **kw):
        return sh(["git", "-C", str(self.checkout), *args], **kw)

    def state(self):
        try:
            return json.loads((self.dir / "state.json").read_text())
        except FileNotFoundError:
            return {}

    def save(self, state):
        tmp = self.dir / "state.json.tmp"
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
        tmp.replace(self.dir / "state.json")

    def runs(self):
        try:
            return [json.loads(line) for line in (self.dir / "runs.jsonl").read_text().splitlines() if line.strip()]
        except FileNotFoundError:
            return []

    def record(self, rec):
        with open(self.dir / "runs.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")


def load_config(slug):
    repo = Repo(slug)
    if not repo.config.is_file():
        die(f"no config at {repo.config}; create one with: maintainer.py add (run it inside the checkout)")
    cfg = merge(load_toml(SCRIPT.parent / "defaults.toml"), load_toml(repo.config))
    cfg["models"] = merge(load_toml(SKILL_DIR / "models.toml"), cfg.get("models", {}))
    cfg["slug"] = slug
    problems = []
    if cfg["forge"] not in FORGES:
        problems.append(f"forge must be one of {', '.join(FORGES)}")
    elif not (REPO_RE.match(cfg.get("repo", "")) if cfg["forge"] == "github" else cfg.get("repo", "").count("/") >= 2):
        problems.append("repo must be owner/name on GitHub, host/owner/name elsewhere")
    if not cfg.get("branch") or not cfg.get("remote"):
        problems.append("branch and remote are required")
    if not Path(cfg.get("checkout", "")).is_absolute():
        problems.append("checkout must be an absolute path")
    if cfg.get("upstream") and not cfg.get("upstream_branch"):
        problems.append("upstream needs upstream_branch")
    if cfg["agent"] not in ("claude", "codex"):
        problems.append("agent must be claude or codex")
    if not set(cfg["deps"]["merge"]) <= {"patch", "minor", "major"}:
        problems.append("deps.merge may only list patch, minor and major")
    if cfg["deps"]["merge_method"] not in ("squash", "merge", "rebase"):
        problems.append("deps.merge_method must be squash, merge or rebase")
    if cfg["scan"]["mode"] not in ("pr", "report"):
        problems.append("scan.mode must be pr or report")
    for task in TASKS:
        tier = cfg["models"]["tasks"].get(task)
        if not cfg["models"].get(cfg["agent"], {}).get(tier, {}).get("model"):
            problems.append(f"no {cfg['agent']} model for {task} (tier {tier!r}) in models.toml")
    if problems:
        die(f"{repo.config}: " + "; ".join(problems))
    repo.checkout = Path(cfg["checkout"])
    return cfg, repo


def model_for(cfg, task):
    return cfg["models"][cfg["agent"]][cfg["models"]["tasks"][task]]


def enabled(cfg, task):
    return (cfg[task].get("enabled", True) and (task != "sync" or bool(cfg.get("upstream")))
            and (task not in GITHUB_TASKS or cfg["forge"] == "github"))


def whoami(cfg):
    if cfg["forge"] == "github":
        return gh("api", "user", "--jq", ".login")
    if cfg["forge"] == "forgejo":
        out = sh(["fj", "-H", cfg["repo"].split("/")[0], "whoami"], cwd=Path.home(), check=False)
        return m.group(1) if (m := re.search(r"signed into (\S+?)@", out)) else None
    return None


# --- the owner's checkout -----------------------------------------------------

def base_ref(cfg):
    return f"{cfg['remote']}/{cfg['branch']}"


def upstream_ref(cfg):
    # A private ref, so the owner's own remotes are left alone.
    return f"refs/{SKILL}/upstream/{cfg['upstream_branch']}" if cfg.get("upstream") else None


def git_path(repo, which, cwd=None):
    return Path(sh(["git", "-C", str(cwd or repo.checkout), "rev-parse", "--path-format=absolute", which]))


def operations(repo, cwd=None):
    gd = git_path(repo, "--git-dir", cwd)
    return [marker for marker in IN_PROGRESS if (gd / marker).exists()]


def dirty(repo, cwd=None):
    """Why the working tree at `cwd` (default: the checkout) can't be switched or moved, or None."""
    if ops := operations(repo, cwd):
        return f"a {IN_PROGRESS[ops[0]][0]} is in progress"
    if sh(["git", "-C", str(cwd or repo.checkout), "status", "--porcelain", "--untracked-files=no"]):
        return "it has uncommitted changes"
    return None


def check_checkout(cfg, repo):
    """Fail if the checkout isn't the configured repo's; return why it can't be used right now, or None."""
    if not repo.checkout.is_dir():
        raise Failure(f"the checkout {repo.checkout} doesn't exist")
    url = repo.git("config", "--get", f"remote.{cfg['remote']}.url", check=False)
    if (remote_identity(cfg, url) or "").lower() != cfg["repo"].lower():
        raise Failure(f"remote {cfg['remote']} in {repo.checkout} isn't {cfg['repo']} ({url or 'missing'})")
    return f"the checkout can't be used: {why}" if (why := dirty(repo)) else None


def remote_identity(cfg, url):
    """The repo a remote URL points at, in the config's form: owner/name on GitHub, host/owner/name
    elsewhere, with a Forgejo host named as its fj login, so another server's same path doesn't match."""
    if cfg["forge"] == "github":
        return github_repo(url)
    if not (parsed := parse_remote(url)):
        return None
    host = parsed[0]
    if cfg["forge"] == "forgejo":
        host = forgejo_host(host) or host
    return f"{host}/{parsed[2]}"


def fetch(cfg, repo):
    repo.git("fetch", "--quiet", "--prune", cfg["remote"], timeout=1800)
    if cfg.get("upstream"):
        repo.git("fetch", "--quiet", "--no-tags", cfg["upstream"],
                 f"+refs/heads/{cfg['upstream_branch']}:{upstream_ref(cfg)}", timeout=1800)


def untracked(repo):
    return sorted(nul_list(repo.git("ls-files", "--others", "--exclude-standard", "-z", strip=False)))


def position(repo):
    """Where the owner left HEAD, and the untracked files that are theirs."""
    branch = repo.git("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    return {"head": branch or repo.git("rev-parse", "HEAD"), "detached": not branch, "untracked": untracked(repo)}


def restore(repo, pos, label):
    """Put the checkout back where the owner left it, stashing whatever the agent left uncommitted."""
    notes = []
    for marker in operations(repo):
        repo.git(*IN_PROGRESS[marker], check=False)
        notes.append(f"undid a {IN_PROGRESS[marker][0]} the agent left")
    changed = set(nul_list(repo.git("diff", "--name-only", "-z", "HEAD", strip=False)))
    left = sorted(changed | (set(untracked(repo)) - set(pos["untracked"])))
    if left:  # literal pathspecs, so a name like `*.txt` can't also select the owner's files
        repo.git("--literal-pathspecs", "stash", "push", "--include-untracked", "-m", f"{SKILL}: left by {label}", "--", *left)
        notes.append(f"stashed {len(left)} files the agent left uncommitted (see git stash list)")
    repo.git("switch", "--quiet", *(["--detach"] if pos["detached"] else []), pos["head"])
    return notes


def recover(cfg, repo, state):
    """Finish putting the checkout back after a run that was killed before it could. The agent works on a
    detached HEAD or its own branches, so any other branch checked out means the owner has moved on."""
    pos = state.get("position")
    if not pos:
        return None
    branch = repo.git("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if branch and not branch.startswith(cfg["branch_prefix"]):
        state.pop("position")
        return None
    notes = restore(repo, pos, "an interrupted run")
    state.pop("position")  # only once it's back, so a failed recovery is tried again next run
    return "; ".join(["recovered from an interrupted run"] + notes)


def ancestor(repo, a, b):
    return subprocess.run(["git", "-C", str(repo.checkout), "merge-base", "--is-ancestor", a, b],
                          env=ENV, capture_output=True).returncode == 0


def count(repo, revs):
    return int(repo.git("rev-list", "--count", revs))


def worktrees(repo):
    """Branch name -> path of the worktree that has it checked out."""
    out, path = {}, None
    for line in repo.git("worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line.startswith("branch refs/heads/"):
            out[line[len("branch refs/heads/"):]] = path
    return out


def update_local(cfg, repo):
    """Fast-forward local branches that are strictly behind the same branch on the remote, inside the
    worktree that has them checked out when there is one, and only if that worktree is clean."""
    remote, trees, moved, left = cfg["remote"], worktrees(repo), [], []
    for line in repo.git("for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads").splitlines():
        branch, sha = line.rsplit(" ", 1)
        theirs = repo.git("rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{branch}", check=False)
        if not theirs or theirs == sha or not ancestor(repo, sha, theirs):
            continue
        try:
            if path := trees.get(branch):
                if why := dirty(repo, path):
                    left.append(f"{branch} ({path}: {why})")
                    continue
                sh(["git", "-C", path, "merge", "--ff-only", "--quiet", theirs])
            else:
                repo.git("update-ref", f"refs/heads/{branch}", theirs, sha)
            moved.append(branch)
        except Failure as e:
            left.append(f"{branch} ({e})")
    notes = [f"fast-forwarded local {', '.join(moved)}"] if moved else []
    return notes + ([f"left local {', '.join(left)} behind"] if left else [])


# --- GitHub state -------------------------------------------------------------

def reachable(host, port, wait_minutes=5):
    """Wait a few minutes for the network, since timers fire right after resume."""
    deadline = time.monotonic() + wait_minutes * 60
    while True:
        try:
            socket.create_connection((host, port), timeout=10).close()
            return True
        except OSError:
            if time.monotonic() > deadline:
                return False
            time.sleep(15)


def online(cfg, repo):
    if cfg["forge"] == "github":
        return reachable("api.github.com", 443)
    remote = parse_remote(repo.git("config", "--get", f"remote.{cfg['remote']}.url", check=False) or "")
    return reachable(*remote[:2]) if remote else True


def check_state(pr):
    """Return (none|pending|failure|success, names of failed checks)."""
    rollup = pr.get("statusCheckRollup") or []
    if not rollup:
        return "none", []
    pending, failed = False, []
    for c in rollup:
        if c.get("__typename") == "StatusContext":
            if c.get("state") in ("PENDING", "EXPECTED"):
                pending = True
            elif c.get("state") in FAILED:
                failed.append(c.get("context"))
        elif c.get("status") != "COMPLETED":
            pending = True
        elif c.get("conclusion") in FAILED:
            failed.append(c.get("name"))
    return ("pending" if pending else "failure" if failed else "success"), failed


def bot_aliases(bots):
    """Every login a bot shows up under: app/dependabot, dependabot, dependabot[bot]."""
    names = {b.removeprefix("app/") for b in bots}
    return set(bots) | names | {f"{n}[bot]" for n in names}


def is_mine(pr, cfg, login):
    return (pr["author"]["login"] == login and not pr["isCrossRepository"]
            and pr["headRefName"].startswith(cfg["branch_prefix"]))


def talk_key(pr, ignore=()):
    """The comments and reviews on a PR the agent didn't write, inline ones too when fetched. Their text
    is included, so an edited comment counts as new feedback."""
    items = (pr.get("comments") or []) + (pr.get("reviews") or []) + (pr.get("review_comments") or [])
    talk = sorted(
        json.dumps([x.get("id"), x.get("body") or ""]) for x in items
        if MARKER not in (x.get("body") or "")
        and not (x.get("body") or "").lstrip().startswith("@dependabot")
        and (x.get("author") or x.get("user") or {}).get("login") not in ignore
    )
    return hashlib.sha256(json.dumps(talk).encode()).hexdigest()[:16]


def fingerprint(pr, ignore=()):
    """Changes when a PR needs another look: new commits, finished checks, a conflict, a
    review decision, or new or edited feedback the agent didn't write."""
    key = [pr["headRefOid"], pr["baseRefName"], check_state(pr)[0], pr.get("mergeable") == "CONFLICTING",
           pr.get("reviewDecision"), pr["isDraft"], talk_key(pr, ignore)]
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16]


def settled(pr, pending, grace_hours, t):
    """True once checks finish, or after they've stayed pending on this head for the grace period."""
    key = str(pr["number"])
    if check_state(pr)[0] != "pending":
        pending.pop(key, None)
        return True
    entry = pending.get(key)
    if not entry or entry["head"] != pr["headRefOid"]:
        pending[key] = {"head": pr["headRefOid"], "since": t.isoformat()}
        return False
    return t - dt.datetime.fromisoformat(entry["since"]) >= dt.timedelta(hours=grace_hours)


def changed_prs(prs, seen, pending, grace_hours, t, ignore=(), include=lambda pr: True):
    """PRs whose fingerprint differs from the last successful run that handled them."""
    numbers = {str(p["number"]) for p in prs}
    for m in (seen, pending):
        for k in [k for k in m if k not in numbers]:
            del m[k]
    out = []
    for pr in prs:
        if not include(pr) or not settled(pr, pending, grace_hours, t):
            continue
        fp = fingerprint(pr, ignore)
        prev = seen.get(str(pr["number"]))
        if not prev or prev["fp"] != fp:
            out.append((pr, fp, prev))
    return out


def pr_facts(pr, cfg, login):
    checks, failed = check_state(pr)
    return {
        "number": pr["number"], "title": pr["title"], "url": pr["url"], "author": pr["author"]["login"],
        "branch": pr["headRefName"], "base": pr["baseRefName"], "head": pr["headRefOid"],
        "from_fork": pr["isCrossRepository"], "draft": pr["isDraft"], "checks": checks, "failed_checks": failed,
        "conflicting": pr.get("mergeable") == "CONFLICTING", "review_decision": pr.get("reviewDecision") or None,
        "created": pr["createdAt"], "updated": pr["updatedAt"], "yours": is_mine(pr, cfg, login),
    }


def verify_bot_pr(cfg, pr):
    """The facts a bot merge rests on, checked here so a PR's own text can't vouch for it."""
    path = f"repos/{cfg['repo']}/pulls/{pr['number']}"
    commits = gh_pages(f"{path}/commits")
    files = [f["filename"] for f in gh_pages(f"{path}/files")]
    logins = {f"{b.removeprefix('app/')}[bot]" for b in cfg["deps"]["bots"]}
    problems = []
    if pr["isCrossRepository"]:
        problems.append("opened from another repository")
    for c in commits:
        who = (c.get("author") or {}).get("login") or c["commit"]["author"]["email"]
        if who not in logins:
            problems.append(f"commit {c['sha'][:10]} is by {who}, not the bot")
        elif not c["commit"]["verification"]["verified"]:
            problems.append(f"commit {c['sha'][:10]} has no verified signature")
        elif (c.get("committer") or {}).get("login") not in logins | {"web-flow"}:
            # Anyone can claim the bot's author email; GitHub only signs as web-flow for the bot itself.
            problems.append(f"commit {c['sha'][:10]} was committed by "
                            f"{(c.get('committer') or {}).get('login') or 'an unknown account'}, not the bot or GitHub")
    if not commits or commits[-1]["sha"] != pr["headRefOid"]:
        problems.append("head moved while checking; it's checked again next run")
    other = [f for f in files if not DEP_FILE.search(f)]
    if other:
        problems.append("changes files that aren't dependency manifests: " + ", ".join(other[:10]))
    types = [m for c in commits for m in UPDATE_TYPE.findall(c["commit"]["message"])]
    update = max(types, key=["patch", "minor", "major"].index) if types else "unknown"
    return {
        "update_type": update, "verified": not problems, "problems": problems, "files": files[:50],
        "merge_allowed_by_policy": (update if update != "unknown" else "major") in cfg["deps"]["merge"],
    }


# --- gates: decide whether a task has work, and gather its facts --------------

@dataclasses.dataclass
class Gate:
    status: str                    # skipped, done (handled without an agent), failed, or work
    note: str = ""
    context: dict | None = None    # task facts for the agent
    commit: Callable | None = None  # state updates once the agent succeeds
    after: Callable | None = None   # follow-up once the agent succeeds; gets the run dir, returns a note


class Ctx:
    def __init__(self, cfg, dry_run):
        self.cfg, self.dry_run = cfg, dry_run
        self.now = now()
        self.login = None
        self.day = None
        self._prs = None

    def prs(self):
        """Open GitHub PRs by the owner's account (which opens the agent's) or a dependency bot. The skill
        reads no one else's. Empty off GitHub, where the runner doesn't read PRs."""
        if self.cfg["forge"] != "github":
            return []
        if self._prs is None:
            authors = [a for a in (self.login, *self.cfg["deps"]["bots"]) if a]
            self._prs = [p for a in authors for p in gh_json(
                "pr", "list", "-R", self.cfg["repo"], "--state", "open", "--limit", "500", "--author", a,
                "--json", PR_FIELDS)]
        return self._prs


def gate_prs(cfg, repo, state, ctx):
    """The agent's own PRs. Other people's PRs aren't this skill's to review; bot PRs are `deps`."""
    prs = [p for p in ctx.prs() if is_mine(p, cfg, ctx.login)]
    for pr in prs:  # replies in inline review threads aren't part of `gh pr list`
        pr["review_comments"] = gh_pages(f"repos/{cfg['repo']}/pulls/{pr['number']}/comments")
    seen, todo = state.setdefault("prs", {}), []
    for pr, fp, prev in changed_prs(prs, seen, state.setdefault("prs_pending", {}),
                                    cfg["prs"]["pending_grace_hours"], ctx.now):
        talk = talk_key(pr)
        if (talk == (prev or {}).get("talk", talk_key({})) and check_state(pr)[0] in ("success", "none")
                and pr.get("mergeable") != "CONFLICTING" and pr.get("reviewDecision") != "CHANGES_REQUESTED"):
            # Only new commits, usually its own push, and their checks passed: nothing to look after.
            seen[str(pr["number"])] = {"fp": fp, "head": pr["headRefOid"], "talk": talk}
        else:
            todo.append((pr, fp, prev, talk))
    if not todo:
        return Gate("skipped", f"{len(prs)} open PRs of yours, none needing a look")
    todo.sort(key=lambda x: x[0]["updatedAt"])
    todo = todo[:cfg["prs"]["max_per_run"]]

    def commit():
        for pr, fp, _, talk in todo:
            seen[str(pr["number"])] = {"fp": fp, "head": pr["headRefOid"], "talk": talk}

    items = [dict(pr_facts(pr, cfg, ctx.login), last_seen_head=prev and prev["head"]) for pr, _, prev, _ in todo]
    return Gate("work", f"{len(items)} of your PRs to look after", {"prs": items}, commit)


def gate_deps(cfg, repo, state, ctx):
    bots = cfg["deps"]["bots"]
    prs = [p for p in ctx.prs() if p["author"]["login"] in bots]
    todo = changed_prs(prs, state.setdefault("deps", {}), state.setdefault("deps_pending", {}),
                       cfg["deps"]["pending_grace_hours"], ctx.now, ignore=bot_aliases(bots))
    if not todo:
        return Gate("skipped", f"{len(prs)} open bot PRs, none ready or changed")
    todo.sort(key=lambda x: x[0]["createdAt"])
    todo = todo[:cfg["deps"]["max_per_run"]]

    def commit():
        for pr, fp, _ in todo:
            state["deps"][str(pr["number"])] = {"fp": fp, "head": pr["headRefOid"]}

    items = [dict(pr_facts(pr, cfg, ctx.login), **verify_bot_pr(cfg, pr)) for pr, _, _ in todo]
    policy = {k: cfg["deps"][k] for k in ("merge", "merge_method", "rebase_conflicts", "local_checks_without_ci")}
    return Gate("work", f"{len(items)} bot PRs to decide", {"prs": items, "policy": policy}, commit)


def fork_features(cfg, repo):
    """The fork's feature branches: `sync.features` if set, otherwise every branch on the remote with
    commits upstream lacks, other than the mirror, the integration branch, skipped ones and the agent's."""
    s, prefix = cfg["sync"], f"refs/remotes/{cfg['remote']}/"
    if s["features"]:
        return list(s["features"])
    names = [ref[len(prefix):] for ref in repo.git("for-each-ref", "--format=%(refname)", prefix).splitlines()]
    return sorted(n for n in names
                  if n != "HEAD" and n not in (s["mirror"], s["integration"])
                  and not n.startswith(cfg["branch_prefix"]) and not any(fnmatch.fnmatch(n, p) for p in s["skip"])
                  and count(repo, f"{upstream_ref(cfg)}..{prefix}{n}"))


def gate_sync(cfg, repo, state, ctx):
    """Refresh a fork the owner's way: fast-forward the mirror here, then leave merging it into each
    feature branch and reintegrating to the agent."""
    if not cfg.get("upstream"):
        return Gate("skipped", "not a fork")
    s, remote, ub = cfg["sync"], cfg["remote"], cfg["upstream_branch"]
    up, notes = repo.git("rev-parse", upstream_ref(cfg)), []
    target = upstream_ref(cfg)
    if mirror := s["mirror"]:
        target = f"{remote}/{mirror}"
        ahead, behind = map(int, repo.git("rev-list", "--left-right", "--count", f"{target}...{up}").split())
        if ahead:
            return Gate("failed", f"{mirror} has {ahead} commits upstream doesn't; it should be a clean mirror")
        if behind and ctx.dry_run:
            notes.append(f"would fast-forward {mirror} by {behind} commits to upstream {up[:10]}")
        elif behind:
            repo.git("push", "--quiet", remote, f"{up}:refs/heads/{mirror}", timeout=900)
            notes.append(f"fast-forwarded {mirror} by {behind} commits to upstream {up[:10]}")
    trees, features = worktrees(repo), []
    for name in fork_features(cfg, repo):
        ref = f"{remote}/{name}"
        if not repo.git("rev-parse", "--verify", "--quiet", ref, check=False):
            notes.append(f"feature branch {name} isn't on {remote}")
            continue
        features.append({"name": name, "ref": ref, "behind": count(repo, f"{ref}..{up}"),
                         "own_commits": count(repo, f"{upstream_ref(cfg)}..{ref}"), "worktree": trees.get(name)})
    integration = s["integration"] or None
    for f in features:
        f["needs_refresh"] = f["behind"] > 0
        f["integrated"] = bool(integration) and ancestor(repo, f["ref"], f"{remote}/{integration}")
    if not any(f["needs_refresh"] or (integration and not f["integrated"]) for f in features):
        return Gate("done" if notes else "skipped", "; ".join(notes) or "the fork is up to date")
    heads = {f["name"]: repo.git("rev-parse", f["ref"]) for f in features}
    fp = hashlib.sha256(json.dumps([up, heads, integration and repo.git("rev-parse", f"{remote}/{integration}")])
                        .encode()).hexdigest()[:16]
    if state.get("sync", {}).get("fp") == fp:
        return Gate("done" if notes else "skipped",
                    "; ".join(notes + ["already handled for these branch heads and upstream"]))

    def commit():
        state["sync"] = {"fp": fp}

    stale = sum(f["needs_refresh"] for f in features)
    message = (f"Merge branch '{mirror}' into {{branch}}" if mirror
               else f"Merge branch '{ub}' of {cfg['upstream']} into {{branch}}")
    return Gate("work", "; ".join(notes + [f"{stale} of {len(features)} feature branches to refresh"]), {
        "upstream_branch": ub, "upstream_head": up, "mirror": mirror or None, "target": target,
        "merge_message": message, "features": features, "integration": integration,
        "skip": s["skip"], "review_prefix": cfg["branch_prefix"] + "refresh-", "runner_did": notes,
    }, commit)


def gate_scan(cfg, repo, state, ctx):
    s, prefix = cfg["scan"], cfg["branch_prefix"] + "scan-"
    integration = cfg["sync"]["integration"] if cfg.get("upstream") else ""
    scan_ref = f"{cfg['remote']}/{integration}" if integration else base_ref(cfg)
    head, last = repo.git("rev-parse", scan_ref), state.get("scan", {}).get("head")
    if cfg["forge"] == "github":
        mine = [{k: p.get(k) for k in ("number", "title", "url")}
                for p in ctx.prs() if is_mine(p, cfg, ctx.login) and p["headRefName"].startswith(prefix)]
        closed = [p for p in gh_json("pr", "list", "-R", cfg["repo"], "--state", "closed", "--limit", "100",
                                     "--author", ctx.login, "--json", "number,title,url,headRefName,mergedAt,closedAt")
                  if p["headRefName"].startswith(prefix)]
    else:  # without a PR API, an improvement branch still on the remote counts as open until it's merged
        mine = [{"branch": r.split("/", 1)[1]} for r in
                repo.git("for-each-ref", "--format=%(refname:short)", f"refs/remotes/{cfg['remote']}/{prefix}*").split()
                if not ancestor(repo, r, scan_ref)  # nor rebase- or single-commit squash-merged
                and any(c.startswith("+") for c in repo.git("cherry", scan_ref, r).splitlines())]
        closed = []
    if s["mode"] == "pr" and len(mine) >= s["max_open_prs"]:
        # Waiting on the owner, not idle: recorded as blocked so the daily report shows it.
        return Gate("blocked", f"{len(mine)} improvement PRs or branches wait for your review")
    if s["mode"] == "report" and head == last:
        return Gate("skipped", "no new commits since the last scan")
    brief = lambda p: {k: p.get(k) for k in ("number", "title", "url", "closedAt")}
    changes = repo.git("diff", "--stat=100", "--stat-count=200", f"{last}..{head}", check=False) if last else None
    fork_scope = None
    if cfg.get("upstream"):
        fork_scope = {"base": f"{cfg['remote']}/{cfg['sync']['mirror']}" if cfg["sync"]["mirror"] else upstream_ref(cfg),
                      "head": scan_ref}

    def commit():
        state["scan"] = {"head": head}

    return Gate("work", f"scan at {head[:10]}", {
        "head": head, "scan_ref": scan_ref, "last_scan_head": last, "changes_since_last_scan": changes,
        "fork_scope": fork_scope, "branch_prefix_scan": prefix, "open": mine,
        "declined_prs": [brief(p) for p in closed if not p["mergedAt"]][:20],
        "merged_prs": [brief(p) for p in closed if p["mergedAt"]][:20],
        "backlog": str(repo.dir / "findings.md"), "mode": s["mode"],
        "max_new_prs": max(0, min(s["max_new_prs"], s["max_open_prs"] - len(mine))),
        "focus": s["focus"], "exclude": s["exclude"],
    }, commit)


def acted(run):
    return run["status"] not in ("skipped", "blocked")


def due_days(cfg, repo, state, t):
    """Days with runs that aren't reported yet, or that gained activity since their report."""
    first = (t - dt.timedelta(days=cfg["report"]["catch_up_days"])).date().isoformat()
    today, cutoff = t.date().isoformat(), dt.time.fromisoformat(cfg["report"]["time"])
    total = collections.Counter()
    for r in repo.runs():
        if r["task"] != "report":
            total[r["date"]] += acted(r)
    reported = state.get("reports", {})
    return [day for day in sorted(total)
            if day >= first and not (day == today and t.time() < cutoff)
            and (day not in reported or total[day] > reported[day])]


def open_pr_snapshot(cfg, ctx):
    """The open PRs this skill answers for: its own and the dependency bot's."""
    if cfg["forge"] != "github":
        return None
    try:
        prs = [p for p in ctx.prs() if is_mine(p, cfg, ctx.login) or p["author"]["login"] in cfg["deps"]["bots"]]
    except Failure:
        return None
    keep = ("number", "title", "url", "author", "draft", "checks", "conflicting", "review_decision", "yours")
    return [dict({k: f[k] for k in keep}, days_open=days_since(p["createdAt"], ctx.now),
                 days_idle=days_since(p["updatedAt"], ctx.now))
            for p in prs for f in [pr_facts(p, cfg, ctx.login)]]


def paused_tasks(state):
    return [{"task": t, "failures_in_a_row": f["count"], "last_error": f.get("error", "")}
            for t, f in state.get("failures", {}).items() if f["count"] >= 3]


def gate_report(cfg, repo, state, ctx):
    day = ctx.day
    runs = [r for r in repo.runs() if r["date"] == day and r["task"] != "report"]
    done = [r for r in runs if acted(r)]
    skipped = collections.Counter(r["task"] for r in runs if r["status"] == "skipped")
    blocked = collections.Counter((r["task"], r["note"]) for r in runs if r["status"] == "blocked")

    def mark():
        state.setdefault("reports", {})[day] = len(done)

    if not any(r.get("session") for r in done):
        if ctx.dry_run:
            return Gate("done", f"would write a report without an agent for {day}")
        mark()
        text = quiet_report(cfg, repo, day, done, skipped, blocked, standing_attention(cfg, ctx, state))
        return Gate("done", f"no agent runs on {day}: {publish(cfg, repo, day, text)}")

    def after(run_dir):
        text = (run_dir / "report.md").read_text().rstrip() + "\n\n" + runs_section(cfg, repo, done, skipped, blocked)
        mark()
        return f"report for {day}: {publish(cfg, repo, day, text)}"

    return Gate("work", f"report for {day}", {
        "date": day,
        "runs": [{"time": r["start"][11:16], "task": r["task"], "status": r["status"], "note": r.get("note", ""),
                  "summary": r.get("summary"), "run_dir": r.get("run_dir"), "denied": r.get("denied", 0)}
                 for r in done],
        "skipped": dict(skipped),
        "blocked": [{"task": t, "reason": why, "count": n} for (t, why), n in sorted(blocked.items())],
        "open_prs": open_pr_snapshot(cfg, ctx),
        "stale_days": cfg["prs"]["stale_days"],
        "paused": paused_tasks(state),
    }, after=after)


GATES = {"prs": gate_prs, "deps": gate_deps, "sync": gate_sync, "scan": gate_scan, "report": gate_report}


# --- reports ------------------------------------------------------------------

def tally(skipped, blocked):
    lines = []
    if blocked:
        lines.append("Runs that couldn't start: "
                     + "; ".join(f"{t} ×{n} ({why})" for (t, why), n in sorted(blocked.items())) + ".")
    if skipped:
        lines.append("Runs with nothing to do: " + ", ".join(f"{t} ×{n}" for t, n in sorted(skipped.items())) + ".")
    return lines


def standing_attention(cfg, ctx, state):
    """What needs the owner whether or not anything ran today: PRs of the skill's left idle, and tasks
    backing off after failures. A quiet day's report lists these without starting an agent."""
    items = []
    stale = [p for p in open_pr_snapshot(cfg, ctx) or [] if p["days_idle"] >= cfg["prs"]["stale_days"]]
    if stale:
        items.append("**Idle PRs:** " + ", ".join(f"[#{p['number']}]({p['url']}) {p['title']} ({p['days_idle']} days)"
                                              for p in stale))
    for t in paused_tasks(state):
        items.append(f"**{t['task']} is backing off** after {t['failures_in_a_row']} failures in a row: {t['last_error']}")
    return items


def quiet_report(cfg, repo, day, done, skipped, blocked, attention=()):
    """A report for a day no agent ran: only the runner acted, if anything."""
    if failed := sum(r["status"] in ("failed", "timeout") for r in done):
        verdict = f"Needs attention: {failed} runs failed; see Runs below."
    elif attention:
        verdict = "Nothing new ran into trouble, but some things still need you; see below."
    elif done:
        verdict = "No agent was needed; the runner handled what came up."
    elif blocked:
        verdict = "Nothing was done; some runs couldn't start."
    else:
        verdict = "Quiet day: nothing needed doing."
    body = "\n\n".join([f"# {cfg['repo']}: {day}", verdict]
                       + (["## Needs your attention", "\n".join(f"- {a}" for a in attention)] if attention else []))
    return body + "\n\n" + (runs_section(cfg, repo, done, skipped, blocked) if done else "\n\n".join(tally(skipped, blocked)) + "\n")


def resume_command(cfg, repo, session):
    return (f"cd {repo.checkout} && " +
            (f"claude --resume {session}" if cfg["agent"] == "claude" else f"codex resume {session}"))


def runs_section(cfg, repo, done, skipped, blocked):
    lines = ["## Runs", "", "| Time | Task | Result | Notes | Session |", "|---|---|---|---|---|"]
    for r in done:
        note = r.get("note", "")
        if r.get("cost_usd"):
            note += f" (${r['cost_usd']:.2f})"
        if r.get("denied"):
            note += f"; {r['denied']} actions denied by auto mode"
        if r.get("checkout"):
            note += f"; {r['checkout']}"
        session = f"`{r['session']}`" if r.get("session") else ""
        lines.append(f"| {r['start'][11:16]} | {r['task']} | {r['status']} | {note.replace('|', '/')} | {session} |")
    if any(r.get("session") for r in done):
        lines += ["", f"To continue a run in its own session, with everything it saw and did: "
                      f"`python3 {SCRIPT} resume {cfg['slug']} <task>` for a task's latest run, "
                      f"or `{resume_command(cfg, repo, '<session>')}`."]
    if extra := tally(skipped, blocked):
        lines += ["", *extra]
    return "\n".join(lines) + "\n"


def publish(cfg, repo, day, text):
    """Keep a copy in the state directory and put the report in the central report folder."""
    repo.reports.mkdir(parents=True, exist_ok=True)
    (repo.reports / f"{day}.md").write_text(text)
    dest = Path(cfg["report_dir"]).expanduser() / day / f"{cfg['slug']}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(dest)
    return dest


def prune_runs(cfg, repo):
    cutoff = (now() - dt.timedelta(days=cfg["keep_run_days"])).date().isoformat()
    if repo.runs_dir.is_dir():
        for d in repo.runs_dir.iterdir():
            if d.is_dir() and d.name < cutoff:
                shutil.rmtree(d)


# --- the agent ----------------------------------------------------------------

def claude_settings(cfg, repo):
    """Keep runs out of auto memory, and tell auto mode's classifier what this session maintains and
    what it may do there."""
    name, prefix, remote = cfg["repo"], cfg["branch_prefix"], cfg["remote"]
    host = {"github": "GitHub", "forgejo": "Forgejo"}.get(cfg["forge"], "git")
    scope = f"the {host} repo {name}" + (f", a fork of {cfg['upstream']}" if cfg.get("upstream") else "")
    allow = ["$defaults",
             f"Maintainer branches: pushing branches named {prefix}* to {remote} ({name}), force-pushing them with "
             f"--force-with-lease, and opening, updating, commenting on and closing PRs from them in {name}."]
    if cfg.get("upstream"):
        s = cfg["sync"]
        allow.append(f"Fork refresh: pushing to {remote} ({name}), without force, merge commits that bring "
                     f"{s['mirror'] or 'upstream ' + cfg['upstream_branch']} into the fork's feature branches"
                     + (f" and merge feature branches into {s['integration']}" if s["integration"] else "")
                     + ", once the repo's checks pass.")
    if enabled(cfg, "deps") and cfg["deps"]["merge"]:
        allow.append(f"Dependency merges: merging Dependabot PRs in {name} with gh pr merge --match-head-commit once "
                     f"the runner verified them and their checks passed, and commenting @dependabot rebase on them.")
    return {"autoMemoryEnabled": False, "autoMode": {
        "environment": [f"**Maintained repo**: this unattended session maintains {scope} for its owner, who set "
                        f"it up. The working directory is the owner's checkout of it, with {remote} as its remote. "
                        f"{repo.dir} holds this session's run files and is in scope too."],
        "allow": allow,
    }}


def agent_command(cfg, repo, task, run_dir, start):
    m = model_for(cfg, task)
    args = f"{task} {run_dir}"
    if cfg["agent"] == "claude":
        settings = run_dir / "claude-settings.json"
        settings.write_text(json.dumps(claude_settings(cfg, repo), indent=2))
        # Kept as a named session in the checkout's /resume list, so the owner can step in afterwards.
        cmd = ["claude", "-p", f"/{SKILL} {args}", "--model", m["model"], "--name", f"maintainer {task} {start:%Y-%m-%d %H:%M}",
               "--permission-mode", "auto", "--permission-prompts", "none", "--settings", str(settings),
               "--add-dir", str(repo.dir),
               "--output-format", "stream-json", "--verbose", "--max-budget-usd", str(cfg[task]["budget_usd"])]
        if m.get("effort"):
            cmd += ["--effort", m["effort"]]
        return cmd + ["--disallowedTools", *CLAUDE_DENY]
    # --approve-for-me implies the workspace-write sandbox, which makes the checkout's .git read-only unless
    # it's added; escalations go to Codex's automatic reviewer.
    cmd = ["codex", "exec", "-m", m["model"], "-C", str(repo.checkout), "--approve-for-me",
           "-c", "sandbox_workspace_write.network_access=true",
           "--add-dir", str(git_path(repo, "--git-common-dir")), "--add-dir", str(repo.dir),
           "--json", "-o", str(run_dir / "last-message.md")]
    if m.get("effort"):
        cmd += ["-c", f'model_reasoning_effort="{m["effort"]}"']
    return cmd + [f"${SKILL} {args}"]


def read_events(path):
    for line in path.read_text().splitlines():
        try:
            yield json.loads(line)
        except ValueError:
            continue


def parse_claude(run_dir):
    result = {}
    for ev in read_events(run_dir / "agent.jsonl"):
        if ev.get("type") == "result":
            result = ev
    info = {k: result[v] for k, v in (("cost_usd", "total_cost_usd"), ("session", "session_id")) if v in result}
    if denials := result.get("permission_denials"):
        info["denied"] = len(denials)
        (run_dir / "denied.json").write_text(json.dumps(denials, indent=2))
    error = None if result and not result.get("is_error") else result.get("subtype") or "no result from claude"
    return info, error


def parse_codex(run_dir):
    info, error = {}, None
    for ev in read_events(run_dir / "agent.jsonl"):
        kind = ev.get("type")
        if kind == "thread.started":
            info["session"] = ev.get("thread_id")
        elif kind == "turn.completed":
            usage = ev.get("usage") or {}
            info["tokens"] = info.get("tokens", 0) + usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        elif kind in ("turn.failed", "error"):
            error = (ev.get("error") or {}).get("message") or ev.get("message") or kind
    return info, error


def stop_group(p, grace=30):
    """Stop the agent's whole process group: SIGTERM, then SIGKILL for anything still there after `grace`."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(p.pid, signal.SIGTERM)
    try:
        p.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    with contextlib.suppress(ProcessLookupError):
        os.killpg(p.pid, signal.SIGKILL)
    p.wait()


def run_agent(cfg, repo, task, run_dir, start):
    """Start the agent and wait for it. Returns (status, error note or None, record fields)."""
    m = model_for(cfg, task)
    fields = {"agent": cfg["agent"], "model": m["model"]}
    timeout = cfg[task]["timeout_minutes"] * 60
    with open(run_dir / "agent.jsonl", "w") as out, open(run_dir / "agent.err", "w") as err:
        p = subprocess.Popen(agent_command(cfg, repo, task, run_dir, start), cwd=repo.checkout, env=ENV,
                             stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True)
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            rc = None
        finally:
            # On a timeout, or when the runner itself is stopped, end the agent before the checkout is
            # restored. Either way, end anything it left running: a CLI can exit before its tool processes.
            stop_group(p)
    info, error = (parse_claude if cfg["agent"] == "claude" else parse_codex)(run_dir)
    fields.update(info)
    if rc is None:
        return "timeout", f"agent still running after {timeout // 60} minutes; stopped", fields
    output = run_dir / ("report.md" if task == "report" else "summary.md")
    if output.exists() and task != "report":
        fields["summary"] = str(output)
    if rc or error:
        tail = (run_dir / "agent.err").read_text().strip()[-300:]
        return "failed", f"agent exited {rc}: {error or tail or 'no output'}", fields
    if not output.exists():
        return "failed", f"agent finished without writing {output.name}", fields
    return "ok", None, fields


def base_context(cfg, repo, task, ctx, run_dir):
    return {
        "task": task, "forge": cfg["forge"], "repo": cfg["repo"], "branch": cfg["branch"],
        "upstream": cfg.get("upstream"), "checkout": str(repo.checkout), "remote": cfg["remote"],
        "base_ref": base_ref(cfg), "upstream_ref": upstream_ref(cfg), "run_dir": str(run_dir),
        "output": str(run_dir / ("report.md" if task == "report" else "summary.md")),
        "login": ctx.login, "marker": MARKER, "branch_prefix": cfg["branch_prefix"], "checks": cfg["checks"],
        "now": ctx.now.isoformat(timespec="minutes"),
        "subagent_model": cfg["models"][cfg["agent"]]["worker"]["model"],
    }


def agent_run(cfg, repo, task, ctx, gate, state, start):
    """Run the agent for a gate with work, in the checkout on a detached HEAD at the fetched default branch
    (for a scan, at what it scans), then put the checkout back. Returns (status, note, record fields)."""
    suffix = f"-{ctx.day}" if task == "report" else ""
    run_dir = repo.runs_dir / start.date().isoformat() / f"{start:%H%M%S}-{task}{suffix}"
    run_dir.mkdir(parents=True)
    context = base_context(cfg, repo, task, ctx, run_dir) | gate.context
    (run_dir / "context.json").write_text(json.dumps(context, indent=2))
    pos = None
    if task in CHECKOUT_TASKS:
        pos = state["position"] = position(repo)
        repo.save(state)  # so a killed run can still be undone by the next one
        repo.git("switch", "--quiet", "--detach", gate.context["scan_ref"] if task == "scan" else base_ref(cfg))
    label = f"the {task} run in {run_dir.name}"
    try:
        status, error, fields = run_agent(cfg, repo, task, run_dir, start)
    except BaseException:  # stopped by systemd or a crash: put the checkout back before going down
        if pos:
            with contextlib.suppress(Failure):
                restore(repo, pos, label)
                state.pop("position")
                repo.save(state)
        raise
    fields["run_dir"] = str(run_dir)
    if pos:
        try:
            if notes := restore(repo, pos, label):
                fields["checkout"] = "; ".join(notes)
            state.pop("position")
        except Failure as e:
            return "failed", f"agent {status}; couldn't put the checkout back on {pos['head']}: {e}", fields
    if status != "ok":
        return status, f"{gate.note}: {error}", fields
    if gate.commit:
        gate.commit()
    return status, gate.after(run_dir) if gate.after else gate.note, fields


# --- running a task -----------------------------------------------------------

def paused_until(state, task, t):
    """After 3 failures in a row, wait 2h, then 4h, 8h ... up to a day before trying again."""
    f = state.get("failures", {}).get(task)
    if not f or f["count"] < 3:
        return None
    until = dt.datetime.fromisoformat(f["last"]) + dt.timedelta(hours=min(24, 2 ** (f["count"] - 2)))
    return until if t < until else None


def run_task(cfg, repo, task, ctx, args):
    start = now()
    ctx.now = start
    rec = {"task": task, "date": start.date().isoformat(), "start": start.isoformat(timespec="seconds")}
    if task == "report":
        rec["day"] = ctx.day
    state, fields = repo.state(), {}
    try:
        until = None if args.force else paused_until(state, task, start)
        if task in CHECKOUT_TASKS and not args.dry_run and (note := recover(cfg, repo, state)):
            fields["checkout"] = note
        if until:
            status, note = "blocked", f"paused after repeated failures until {until:%Y-%m-%d %H:%M}"
        elif not online(cfg, repo):
            status, note = "blocked", "offline"
        elif task in CHECKOUT_TASKS and (reason := check_checkout(cfg, repo)):
            status, note = "blocked", reason
        else:
            ctx.login = ctx.login or whoami(cfg)
            if task in CHECKOUT_TASKS:
                fetch(cfg, repo)
            gate = GATES[task](cfg, repo, state, ctx)
            status, note = gate.status, gate.note
            if gate.status == "work" and args.dry_run:
                print(json.dumps(gate.context, indent=2))
            elif gate.status == "work":
                status, note, more = agent_run(cfg, repo, task, ctx, gate, state, start)
                fields |= more
            if task == "sync" and status in ("ok", "done", "skipped") and cfg["sync"]["update_local"] and not args.dry_run:
                if moved := update_local(cfg, repo):
                    status, note = ("done" if status == "skipped" else status), "; ".join([note, *moved])
    except Failure as e:
        status, note = "failed", str(e)
    except Exception as e:  # a bug or an unexpected reply: still recorded, reported and backed off
        status, note = "failed", f"{type(e).__name__}: {e}"
    print(f"{cfg['slug']} {task}: {status}: {note}")
    if args.dry_run:
        return 0
    if status in ("ok", "done"):
        state.get("failures", {}).pop(task, None)
    elif status in ("failed", "timeout"):
        f = state.setdefault("failures", {}).setdefault(task, {"count": 0})
        f.update(count=f["count"] + 1, last=start.isoformat(), error=note[:300])
    repo.save(state)
    repo.record(rec | {"end": now().isoformat(timespec="seconds"), "status": status, "note": note} | fields)
    return 1 if status in ("failed", "timeout") else 0


@contextlib.contextmanager
def locked(repo):
    """One run per repo at a time; resumed timers fire together and queue here."""
    repo.dir.mkdir(parents=True, exist_ok=True)
    with open(repo.dir / "lock", "w") as f:
        deadline = time.monotonic() + LOCK_WAIT_MINUTES * 60
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise Failure(f"another run held the lock for over {LOCK_WAIT_MINUTES} minutes")
                time.sleep(15)
        yield


def cmd_run(args):
    cfg, repo = load_config(find_slug(args.slug))
    # systemd stops units with SIGTERM; turn it into an exception so the checkout gets put back.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    ctx = Ctx(cfg, args.dry_run)
    with locked(repo):
        if args.task != "report":
            if not enabled(cfg, args.task):
                print(f"{cfg['slug']} {args.task}: not enabled for this repo")
                return 0
            return run_task(cfg, repo, args.task, ctx, args)
        days = [dt.date.fromisoformat(args.date).isoformat()] if args.date else due_days(cfg, repo, repo.state(), now())
        if not days:
            print(f"{cfg['slug']} report: nothing due")
        rc = 0
        for day in days:
            ctx.day = day
            rc |= run_task(cfg, repo, "report", ctx, args)
        if not args.dry_run:
            prune_runs(cfg, repo)
        return rc


def cmd_resume(args):
    """Open an agent run's session interactively, in the checkout, to follow up on what it did."""
    cfg, repo = load_config(find_slug(args.slug))
    runs = [r for r in repo.runs() if r.get("session") and (not args.task or r["task"] == args.task)]
    if not runs:
        die("no agent run with a saved session" + (f" for {args.task}" if args.task else ""))
    r = runs[-1]
    cmd = ["claude", "--resume", r["session"]] if r["agent"] == "claude" else ["codex", "resume", r["session"]]
    print(f"resuming the {r['task']} run from {r['start'][:16].replace('T', ' ')} in {repo.checkout}")
    if why := dirty(repo):
        print(f"note: the checkout is in use ({why}); the session sees it as it is now")
    sys.stdout.flush()  # exec replaces this process before buffered output is written
    os.chdir(repo.checkout)
    os.execvp(cmd[0], cmd)


# --- setup commands -----------------------------------------------------------

CONFIG = """\
# {skill} config for {repo}.
# Any key in {defaults} can be set here.
# After changing a schedule, run: python3 {script} install {slug}

repo = "{repo}"
forge = "{forge}"  # github, forgejo or git
branch = "{branch}"
checkout = "{checkout}"
remote = "{remote}"  # the checkout's remote for {repo}
agent = "{agent}"  # claude or codex
{upstream}
# Commands that verify a change. Empty: the agent works them out from the CI config.
# checks = ["cargo test", "cargo clippy -- -D warnings"]

# [deps]
# merge = ["patch", "minor"]  # leave major bumps for you

# [scan]
# mode = "report"             # record inefficiencies without opening PRs
{sync}"""

UPSTREAM = """
# A fork: refreshed from this upstream the way references/sync.md describes.
upstream = "{url}"
upstream_branch = "{branch}"
"""

SYNC = """
[sync]
mirror = "{mirror}"            # clean mirror of upstream, only ever fast-forwarded; "" if the fork has none
integration = "{integration}"  # merges every feature branch; "" if the fork has none
# features = []         # feature branches to keep current; empty: every branch with commits upstream lacks
# skip = ["bench/*"]    # never refreshed or merged into integration
"""

SERVICE = """\
[Unit]
Description={skill} {task} for {repo}

[Service]
Type=oneshot
WorkingDirectory={checkout}
Environment={path}
ExecStart={python} {script} run {slug} {task}
TimeoutStartSec={timeout}min
Nice=10
"""

TIMER = """\
[Unit]
Description={skill} {task} timer for {repo}

[Timer]
OnCalendar={schedule}
Persistent=true
RandomizedDelaySec={delay}

[Install]
WantedBy=timers.target
"""


def toml_str(value):
    """`value` escaped for the inside of a TOML basic string ("..."); JSON's escapes are all valid TOML."""
    return json.dumps(str(value), ensure_ascii=False)[1:-1]


def unit_str(value, exec_line=False):
    """`value` as one quoted systemd argument: % is a specifier, and ExecStart= also expands $."""
    s = '"' + str(value).replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s.replace("$", "$$") if exec_line else s


def remote_head(url, cwd):
    out = sh(["git", "-C", cwd, "ls-remote", "--symref", url, "HEAD"], timeout=60, check=False)
    return m.group(1) if (m := re.search(r"^ref: refs/heads/(\S+)\s+HEAD", out, re.M)) else None


def unattended_access(remote, cwd):
    """Whether the remote answers without an SSH agent or prompt, as it must for timer runs."""
    env = {k: v for k, v in ENV.items() if k != "SSH_AUTH_SOCK"} | {"GIT_SSH_COMMAND": "ssh -o BatchMode=yes"}
    return subprocess.run(["git", "-C", cwd, "ls-remote", remote, "HEAD"], env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, timeout=60).returncode == 0


def suggest_fork(cfg, repo):
    """Guess the fork's mirror and integration branches from their history."""
    fetch(cfg, repo)
    remote, up, ub = cfg["remote"], upstream_ref(cfg), cfg["upstream_branch"]
    mirror = ub if (repo.git("rev-parse", "--verify", "--quiet", f"{remote}/{ub}", check=False)
                    and not count(repo, f"{up}..{remote}/{ub}")) else ""
    cfg["sync"] = cfg["sync"] | {"mirror": mirror, "integration": ""}
    features = fork_features(cfg, repo)
    # An integration branch already contains most other feature branches.
    best, contained = "", 1
    for c in features:
        n = sum(ancestor(repo, f"{remote}/{f}", f"{remote}/{c}") for f in features if f != c)
        if n > contained:
            best, contained = c, n
    return mirror, best


def cmd_add(args):
    top = sh(["git", "-C", args.checkout, "rev-parse", "--show-toplevel"], check=False)
    if not top:
        die(f"{args.checkout} isn't inside a git checkout")
    # As configured, before any url.*.insteadOf rewriting, since that is what identifies the repo.
    remotes = {n: sh(["git", "-C", top, "config", "--get", f"remote.{n}.url"]) for n in sh(["git", "-C", top, "remote"]).split()}
    remote = args.remote or ("origin" if "origin" in remotes else next(iter(remotes), None) if len(remotes) == 1 else None)
    if remote not in remotes:
        die(f"pick the remote to maintain with --remote; remotes: {', '.join(remotes) or 'none'}")
    url, upstream, upstream_branch = remotes[remote], None, None
    if full := github_repo(url):
        info = gh_json("repo", "view", full, "--json",
                       "nameWithOwner,isFork,parent,defaultBranchRef,viewerPermission,isArchived")
        if info["isArchived"]:
            die(f"{full} is archived")
        if info["viewerPermission"] not in WRITE_ACCESS:
            die(f"your GitHub account can't push to {full} (permission: {info['viewerPermission']})")
        forge, name, branch = "github", info["nameWithOwner"], info["defaultBranchRef"]["name"]
        if info["isFork"] and info.get("parent"):
            parent = f"{info['parent']['owner']['login']}/{info['parent']['name']}"
            upstream = f"https://github.com/{parent}.git"
            upstream_branch = gh_json("repo", "view", parent, "--json", "defaultBranchRef")["defaultBranchRef"]["name"]
    else:
        parsed = parse_remote(url)
        if not parsed:
            die(f"can't read a host and repo from {remote} ({url})")
        host, _, path = parsed
        fj_host = forgejo_host(host)
        forge, name = ("forgejo", f"{fj_host}/{path}") if fj_host else ("git", f"{host}/{path}")
        branch = remote_head(remote, top) or die(f"can't tell the default branch of {remote}")
    if args.upstream or ("upstream" in remotes and remote != "upstream" and not upstream):
        upstream = args.upstream or remotes["upstream"]
        upstream_branch = remote_head(upstream, top) or die(f"can't tell the default branch of {upstream}")
    slug = slugify(name)
    repo = Repo(slug)
    if repo.config.exists() and not args.force:
        die(f"{repo.config} already exists; pass --force to overwrite it")
    fmt = dict(skill=SKILL, repo=toml_str(name), slug=slug, forge=forge, branch=toml_str(branch),
               checkout=toml_str(top), remote=toml_str(remote), agent=args.agent,
               defaults=SCRIPT.parent / "defaults.toml", script=SCRIPT, sync="",
               upstream=UPSTREAM.format(url=toml_str(upstream), branch=toml_str(upstream_branch)) if upstream else "")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    repo.config.write_text(CONFIG.format(**fmt))
    cfg, repo = load_config(slug)
    if upstream:
        mirror, integration = suggest_fork(cfg, repo)
        repo.config.write_text(CONFIG.format(**fmt | {
            "sync": SYNC.format(mirror=toml_str(mirror), integration=toml_str(integration))}))
        cfg, repo = load_config(slug)
    print(f"wrote {repo.config} for {name} ({forge}), using remote {remote} in {top}")
    if upstream:
        s = cfg["sync"]
        print(f"fork of {upstream} ({upstream_branch}); mirror: {s['mirror'] or 'none'}, "
              f"integration: {s['integration'] or 'none'}\nfeature branches: {', '.join(fork_features(cfg, repo)) or 'none'}")
    if not unattended_access(remote, top):
        print(f"warning: {remote} ({url}) didn't answer without an SSH agent or a prompt. Timer runs have neither, so "
              f"fetches and pushes will fail; use an HTTPS URL with a credential helper, or a key without a passphrase.")
    print(f"next: review the config, try `maintainer.py run {slug} <task> --dry-run`, then `maintainer.py install {slug}`")
    return 0


def unit_name(slug, task):
    return f"{SKILL}-{slug}-{task}"


def cmd_install(args):
    cfg, repo = load_config(find_slug(args.slug))
    slug, units = cfg["slug"], {}
    for task in TASKS:
        if not enabled(cfg, task):
            continue
        schedule = f"*-*-* {cfg['report']['time']}:00" if task == "report" else cfg[task]["schedule"]
        sh(["systemd-analyze", "calendar", schedule])  # fails on a bad expression
        fmt = dict(skill=SKILL, task=task, repo=cfg["repo"], slug=slug, checkout=str(repo.checkout).replace("%", "%%"),
                   path=unit_str("PATH=" + os.environ["PATH"]), python=unit_str(sys.executable, exec_line=True),
                   script=unit_str(SCRIPT, exec_line=True), schedule=schedule,
                   timeout=LOCK_WAIT_MINUTES + cfg[task]["timeout_minutes"] + 30,
                   delay="0" if task == "report" else "5min")
        units[f"{unit_name(slug, task)}.service"] = SERVICE.format(**fmt)
        units[f"{unit_name(slug, task)}.timer"] = TIMER.format(**fmt)
    if args.dry_run:
        for name, text in units.items():
            print(f"# {UNIT_DIR / name}\n{text}")
        return 0
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    for task in TASKS:
        name = unit_name(slug, task)
        if f"{name}.timer" not in units and (UNIT_DIR / f"{name}.timer").exists():
            sh(["systemctl", "--user", "disable", "--now", f"{name}.timer"], check=False)
            for ext in ("timer", "service"):
                (UNIT_DIR / f"{name}.{ext}").unlink(missing_ok=True)
    for name, text in units.items():
        (UNIT_DIR / name).write_text(text)
    timers = [n for n in units if n.endswith(".timer")]
    sh(["systemctl", "--user", "daemon-reload"])
    if not timers:
        print(f"no tasks are enabled for {slug}; its timers are removed")
        return 0
    sh(["systemctl", "--user", "enable", *timers])
    sh(["systemctl", "--user", "restart", *timers])
    print(sh(["systemctl", "--user", "list-timers", "--all", "--no-pager", *timers]))
    return 0


def cmd_uninstall(args):
    slug = find_slug(args.slug)
    names = [unit_name(slug, t) for t in TASKS]
    timers = [f"{n}.timer" for n in names if (UNIT_DIR / f"{n}.timer").exists()]
    if timers:
        sh(["systemctl", "--user", "disable", "--now", *timers], check=False)
    for n in names:
        for ext in ("timer", "service"):
            (UNIT_DIR / f"{n}.{ext}").unlink(missing_ok=True)
    sh(["systemctl", "--user", "daemon-reload"])
    print(f"removed {len(timers)} timers for {slug}; {CONFIG_DIR / (slug + '.toml')} and {STATE_DIR / slug} are kept")
    return 0


def cmd_status(args):
    slugs = [find_slug(args.slug)] if args.slug else sorted(p.stem for p in CONFIG_DIR.glob("*.toml"))
    if not slugs:
        print("no repos configured; add one with: maintainer.py add (run it inside the checkout)")
        return 0
    print(sh(["systemctl", "--user", "list-timers", "--all", "--no-pager",
              *[f"{unit_name(s, '*')}.timer" for s in slugs]], check=False))
    for slug in slugs:
        repo = Repo(slug)
        print(f"\n{slug}")
        for t in paused_tasks(repo.state()):
            print(f"  {t['task']} is backing off after {t['failures_in_a_row']} failures: {t['last_error']}")
        for r in repo.runs()[-args.last:]:
            session = f"  [session {r['session']}]" if r.get("session") else ""
            print(f"  {r['start'][:16].replace('T', ' ')}  {r['task']:<6} {r['status']:<8} {r.get('note', '')}{session}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    target = "slug, owner/repo, or a path in the checkout"
    p = sub.add_parser("add", help="write the config for the checkout you're in")
    p.add_argument("--checkout", default=".", help="a path inside the checkout (default: the current directory)")
    p.add_argument("--remote", help="the checkout's remote for the repo to maintain (default: origin)")
    p.add_argument("--upstream", help="the fork's upstream URL (default: the GitHub parent, or a remote named upstream)")
    p.add_argument("--agent", choices=("claude", "codex"), default="claude")
    p.add_argument("--force", action="store_true", help="overwrite an existing config")
    p = sub.add_parser("install", help="write and start the repo's systemd user timers")
    p.add_argument("slug", help=target)
    p.add_argument("--dry-run", action="store_true", help="print the unit files instead")
    p = sub.add_parser("uninstall", help="stop and remove the repo's timers; keeps config and state")
    p.add_argument("slug", help=target)
    p = sub.add_parser("run", help="run one task now; this is what the timers call")
    p.add_argument("slug", help=target)
    p.add_argument("task", choices=TASKS)
    p.add_argument("--dry-run", action="store_true",
                   help="show what the task would do; no agent, no pushes, no branch switches, nothing recorded")
    p.add_argument("--force", action="store_true", help="run even while the task backs off after failures")
    p.add_argument("--date", help="report only: the day to report on (YYYY-MM-DD)")
    p = sub.add_parser("resume", help="continue an agent run's session interactively, in the checkout")
    p.add_argument("slug", help=target)
    p.add_argument("task", nargs="?", choices=TASKS, help="resume this task's latest run (default: the latest run)")
    p = sub.add_parser("status", help="timers and recent runs")
    p.add_argument("slug", nargs="?", help=f"{target}; all repos if omitted")
    p.add_argument("--last", type=int, default=10, help="how many recent runs to show")
    args = ap.parse_args()
    commands = {"add": cmd_add, "install": cmd_install, "uninstall": cmd_uninstall, "run": cmd_run,
                "resume": cmd_resume, "status": cmd_status}
    try:
        return commands[args.cmd](args)
    except Failure as e:
        die(str(e))


if __name__ == "__main__":
    sys.exit(main())
