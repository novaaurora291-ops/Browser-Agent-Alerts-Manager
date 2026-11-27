"""Shared Repo Team: one Planner (ChatGPT) Cent profile and several worker Cent profiles that work on ONE local
Windows git repo, each in its own git worktree + branch made from ONE base commit.

Saved in Settings["repo_team"]:
    {"repo_path": "D:\\Code\\GeoFrost-App", "base_ref": "HEAD", "base_sha": "<40 hex>", "worktree_root": "",
     "planner": {"profile_id": "2223e6", "alias": "@cent1", "project_url": ""},
     "workers": [{"profile_id": "447193", "alias": "@cent2", "project_url": "https://hark.com/project/<id>",
                  "branch": "agent-cent2", "worktree": "D:\\Code\\GeoFrost-App-worktrees\\cent2"}]}

Profiles are referenced by their stable managed profile ID (the tag in settings.managed_profiles), never by name.
Every git call uses an argument list with shell=False. Nothing here deletes a worktree, a branch or a file."""
import os
import re
import subprocess

from .config import log

PROJECT_URL_RE = re.compile(r"^https://hark\.com/project/[0-9A-Za-z][0-9A-Za-z-]{7,}$")
ALIAS_RE = re.compile(r"^@[A-Za-z0-9_-]{1,32}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CREATE_NO_WINDOW = 0x08000000


class GitError(RuntimeError):
    pass


# ---------------------------------------------------------------- git
def git(args, cwd=None, timeout=60, check=True):
    """Run git with an argument list (never a shell). Returns stdout (stripped)."""
    if not isinstance(args, (list, tuple)):
        raise TypeError("git args must be a list")
    cmd = ["git"] + [str(a) for a in args]
    try:
        r = subprocess.run(cmd, cwd=cwd, shell=False, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace",
                           creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
    except FileNotFoundError:
        raise GitError("git nahi mila (Git for Windows install karein)")
    except subprocess.TimeoutExpired:
        raise GitError(f"git {' '.join(map(str, args[:2]))}: timeout")
    if check and r.returncode != 0:
        raise GitError((r.stderr or r.stdout or f"git exit {r.returncode}").strip().splitlines()[-1][:300])
    return (r.stdout or "").strip()


def git_ok(args, cwd=None):
    try:
        git(args, cwd)
        return True
    except GitError:
        return False


def npath(p):
    return os.path.normcase(os.path.normpath(os.path.abspath(p))) if p else ""


def repo_top(path):
    if not path or not os.path.isdir(path):
        raise GitError(f"Repo folder nahi mila: {path}")
    return os.path.normpath(git(["rev-parse", "--show-toplevel"], cwd=path))


def common_dir(path):
    d = git(["rev-parse", "--git-common-dir"], cwd=path)
    return npath(d if os.path.isabs(d) else os.path.join(path, d))


def repo_name(path):
    return os.path.basename(os.path.normpath(path)) if path else ""


def alias_slug(alias):
    return re.sub(r"[^A-Za-z0-9_-]", "", (alias or "").lstrip("@")).lower()


def default_branch(alias):
    return "agent-" + alias_slug(alias)


def default_worktree_root(repo):
    repo = os.path.normpath(repo)
    return os.path.join(os.path.dirname(repo), repo_name(repo) + "-worktrees")


def worktree_list(repo):
    """[{path, head, branch}] from `git worktree list --porcelain`."""
    out, cur = [], {}
    for line in git(["worktree", "list", "--porcelain"], cwd=repo).splitlines():
        if not line.strip():
            if cur:
                out.append(cur)
            cur = {}
            continue
        k, _, v = line.partition(" ")
        if k == "worktree":
            cur = {"path": os.path.normpath(v)}
        elif k == "HEAD":
            cur["head"] = v
        elif k == "branch":
            cur["branch"] = v.replace("refs/heads/", "", 1)
        elif k in ("locked", "prunable", "detached", "bare"):
            cur[k] = v or True
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------- team config
def empty_team():
    return {"repo_path": "", "base_ref": "HEAD", "base_sha": "", "worktree_root": "",
            "planner": {"profile_id": "", "alias": "@cent1", "project_url": ""}, "workers": []}


def load(settings):
    t = settings["repo_team"] if "repo_team" in settings.data else None
    base = empty_team()
    if isinstance(t, dict):
        base.update({k: v for k, v in t.items() if k in base})
        base["planner"] = dict(empty_team()["planner"], **(t.get("planner") or {}))
        base["workers"] = [dict(w) for w in (t.get("workers") or []) if isinstance(w, dict)]
    return base


def save(settings, team):
    settings["repo_team"] = team
    settings.save()


def is_configured(team):
    return bool(team and team.get("repo_path") and team.get("workers"))


def validate(team, managed_ids):
    """Static checks of a team before saving. Returns a list of problems (empty = ok)."""
    errs = []
    if not team.get("repo_path"):
        errs.append("Repo folder chunein")
    pl = team.get("planner") or {}
    seen_alias, seen_prof = set(), set()
    if pl.get("profile_id"):
        if pl["profile_id"] not in managed_ids:
            errs.append(f"Planner profile {pl['profile_id']} managed Cent profile nahi")
        seen_prof.add(pl["profile_id"])
        if pl.get("alias"):
            seen_alias.add(pl["alias"].lower())
        if pl.get("project_url") and not PROJECT_URL_RE.match(pl["project_url"]):
            errs.append("Planner project URL exact https://hark.com/project/<id> hona chahiye")
    if not team.get("workers"):
        errs.append("Kam az kam ek worker chahiye")
    for w in team.get("workers") or []:
        a = w.get("alias") or ""
        if not ALIAS_RE.match(a):
            errs.append(f"Alias '{a}' ghalat (misal: @cent2)")
        elif a.lower() in seen_alias:
            errs.append(f"Alias {a} do dafa hai")
        seen_alias.add(a.lower())
        pid = w.get("profile_id") or ""
        if pid not in managed_ids:
            errs.append(f"{a}: managed Cent profile chunein")
        elif pid in seen_prof:
            errs.append(f"{a}: yeh profile pehle se team mein hai")
        seen_prof.add(pid)
        if not PROJECT_URL_RE.match(w.get("project_url") or ""):
            errs.append(f"{a}: project URL exact https://hark.com/project/<id> hona chahiye")
    return errs


# ---------------------------------------------------------------- worktrees
def setup_worktrees(team):
    """On team save: resolve ONE base commit and create (or reuse) one worktree + branch per worker.
    Returns (team, results) where results = {alias: (ok, message)}. team gets base_sha/branch/worktree filled."""
    repo = repo_top(team["repo_path"])
    team["repo_path"] = repo
    ref = (team.get("base_ref") or "HEAD").strip() or "HEAD"
    sha = git(["rev-parse", "--verify", ref + "^{commit}"], cwd=repo)
    if not SHA_RE.match(sha):
        raise GitError(f"Base ref {ref} ka commit nahi mila")
    team["base_ref"], team["base_sha"] = ref, sha
    root = team.get("worktree_root") or default_worktree_root(repo)
    team["worktree_root"] = root
    results = {}
    for w in team["workers"]:
        w["branch"] = w.get("branch") or default_branch(w["alias"])
        w["worktree"] = w.get("worktree") or os.path.join(root, alias_slug(w["alias"]))
        try:
            results[w["alias"]] = ensure_worktree(repo, sha, w)
        except GitError as e:
            results[w["alias"]] = (False, str(e))
    log(f"repo team: base {sha[:10]} ({ref}) in {repo}; " + "; ".join(f"{a}: {'ok' if r[0] else r[1]}" for a, r in results.items()))
    return team, results


def ensure_worktree(repo, sha, w):
    path, branch = os.path.normpath(w["worktree"]), w["branch"]
    wts = worktree_list(repo)
    here = next((x for x in wts if npath(x["path"]) == npath(path)), None)
    elsewhere = next((x for x in wts if x.get("branch") == branch and npath(x["path"]) != npath(path)), None)
    if elsewhere:
        return False, f"Branch {branch} pehle se doosre worktree mein hai: {elsewhere['path']}"
    if here:
        ok, why = verify_worktree(repo, sha, w)
        return (True, "Reused clean worktree") if ok else (False, "Existing worktree mismatched: " + why)
    if os.path.exists(path) and os.listdir(path):
        return False, f"Folder pehle se hai aur git worktree nahi: {path}"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if git_ok(["show-ref", "--verify", "--quiet", "refs/heads/" + branch], cwd=repo):
        # the branch exists already: reuse it only when it is built on the same base commit
        if not git_ok(["merge-base", "--is-ancestor", sha, "refs/heads/" + branch], cwd=repo):
            return False, f"Branch {branch} pehle se hai lekin base {sha[:10]} par nahi"
        git(["worktree", "add", path, branch], cwd=repo)
    else:
        git(["worktree", "add", "-b", branch, path, sha], cwd=repo)
    ok, why = verify_worktree(repo, sha, w)
    return (True, "Created") if ok else (False, why)


def _gitdir(path):
    d = git(["rev-parse", "--git-dir"], cwd=path)
    return d if os.path.isabs(d) else os.path.join(path, d)


def verify_worktree(repo, sha, w):
    """Right before dispatch: the actual repo, worktree path, checked-out branch and clean state.
    Returns (ok, reason)."""
    path, branch = w.get("worktree") or "", w.get("branch") or ""
    if not path or not os.path.isdir(path):
        return False, f"Worktree folder nahi mila: {path or '(not set)'}"
    try:
        top = os.path.normpath(git(["rev-parse", "--show-toplevel"], cwd=path))
        if npath(top) != npath(path):
            return False, f"Worktree path mismatch ({top})"
        if common_dir(path) != common_dir(repo):
            return False, "Yeh worktree is repo ka nahi"
        if not any(npath(x["path"]) == npath(path) for x in worktree_list(repo)):
            return False, "Repo ki worktree list mein nahi"
        cur = git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=path, check=False)
        if cur != branch:
            return False, f"Branch mismatch: {cur or 'detached HEAD'} (chahiye {branch})"
        gd = _gitdir(path)
        for marker, why in (("index.lock", "busy (index.lock)"), ("MERGE_HEAD", "merge chal raha hai"),
                            ("rebase-merge", "rebase chal raha hai"), ("rebase-apply", "rebase/am chal raha hai"),
                            ("CHERRY_PICK_HEAD", "cherry-pick chal raha hai"), ("BISECT_LOG", "bisect chal raha hai")):
            if os.path.exists(os.path.join(gd, marker)):
                return False, "Busy: " + why
        if not SHA_RE.match(sha or ""):
            return False, "Base SHA set nahi (team dobara save karein)"
        if not git_ok(["merge-base", "--is-ancestor", sha, "HEAD"], cwd=path):
            return False, f"Branch base {sha[:10]} par nahi"
        dirty = git(["status", "--porcelain", "--untracked-files=normal"], cwd=path)
        if dirty:
            files = [l[3:] for l in dirty.splitlines()][:3]
            return False, f"Dirty worktree: {len(dirty.splitlines())} change(s) ({', '.join(files)})"
    except GitError as e:
        return False, "git: " + str(e)
    return True, "Worktree ready"


def verify_all(team):
    """{alias: (ok, reason)} for every worker (git only, no browser)."""
    out = {}
    try:
        repo = repo_top(team.get("repo_path"))
    except GitError as e:
        return {w.get("alias"): (False, str(e)) for w in team.get("workers") or []}
    for w in team.get("workers") or []:
        out[w.get("alias")] = verify_worktree(repo, team.get("base_sha"), w)
    return out


# ---------------------------------------------------------------- prompts
def build_prompt(team, w, text):
    """The exact text a worker receives: its worktree, branch and base commit first, then the plan block."""
    return ("[Hark Repo Team dispatch]\n"
            f"Repo: {repo_name(team.get('repo_path'))}\n"
            f"Worktree (absolute path, work only here): {os.path.normpath(w.get('worktree') or '')}\n"
            f"Branch: {w.get('branch')}\n"
            f"Base commit: {team.get('base_sha')}\n"
            "---\n" + (text or "").strip())


# ---------------------------------------------------------------- plan parsing
_START = re.compile(r"^\s*(?:[-*>#]+\s*)?(?:\(?\d+[.)]\s*)?(?:\*\*|__)?(@[A-Za-z0-9_-]+)(?:\*\*|__)?\s*(?:[:\u2013\u2014-]+\s*)?(.*)$")


def parse_plan(text, aliases):
    """Split a pasted ChatGPT plan into per-agent blocks.
    Accepts '1. @cent2 – text' lines and multi-line blocks ('@cent2:' / '### @cent2' followed by lines).
    Returns (blocks, issues): blocks = [{alias, text, known, dup, empty}], issues = [str]."""
    known = {a.lower(): a for a in aliases}
    blocks, cur = [], None
    for raw in (text or "").splitlines():
        m = _START.match(raw)
        if m:
            cur = {"alias": m.group(1), "lines": [m.group(2).rstrip()] if m.group(2).strip() else []}
            blocks.append(cur)
        elif cur is not None:
            cur["lines"].append(raw.rstrip())
    out, issues, counts = [], [], {}
    for b in blocks:
        counts[b["alias"].lower()] = counts.get(b["alias"].lower(), 0) + 1
    for b in blocks:
        body = "\n".join(b["lines"]).strip()
        a = b["alias"]
        rec = {"alias": known.get(a.lower(), a), "text": body, "known": a.lower() in known,
               "dup": counts[a.lower()] > 1, "empty": not body}
        out.append(rec)
    for a, n in counts.items():
        if n > 1:
            issues.append(f"{known.get(a, a)}: {n} blocks (duplicate) - kisi ko nahi bheja jayega")
    for r in out:
        if not r["known"]:
            issues.append(f"{r['alias']}: team mein nahi (unknown)")
        if r["empty"]:
            issues.append(f"{r['alias']}: khali block")
    if not blocks and (text or "").strip():
        issues.append("Koi @alias block nahi mila")
    return out, issues
