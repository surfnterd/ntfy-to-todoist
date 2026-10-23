#!/usr/bin/env python3
"""ntfy-to-todoist: turn urgent ntfy alerts into Todoist tasks, and close them again on "Resolved:".

Run it from cron every few minutes. Each run:

1. Polls an ntfy topic for messages since the last run.
2. For each message at or above MIN_PRIORITY (default 4 = high), creates a
   Todoist task "<prefix><title>" with the message body as description.
3. Dedupes repeats: one task per alert title per COOLDOWN hours. Numbers are
   collapsed before comparing, so "CPU 88% (load 18.1)" and "CPU 63% (load 17.0)"
   count as the same alert and don't create two tasks.
4. A message titled "Resolved: <name>" (any priority) closes the open tasks
   whose title starts with <name>, with a comment saying why. A rules file can
   map one resolve name to several alert titles (see RESOLVE_RULES_FILE).

`--dry-run` prints what it would do and writes nothing.

Config (environment variables):
    NTFY_TOPIC_URL      e.g. https://ntfy.example.com/alerts   (required)
    NTFY_TOKEN          ntfy access token, if the topic needs one
    TODOIST_TOKEN       Todoist API token                       (required)
    TODOIST_PROJECT_ID  project for new tasks (default: Inbox)
    TASK_PREFIX         default "⚠️ Alert: "
    TODOIST_PRIORITY    1-4, Todoist API scale (4 = p1). Default 3 (= p2)
    MIN_PRIORITY        ntfy priority gate, default 4
    COOLDOWN_HOURS      default 24
    EXCLUDE_TITLES      regex; matching alerts are never bridged
    RESOLVE_RULES_FILE  optional JSON: {"<resolve regex>": ["<alert title regex>", ...]}
                        "{0}" in an alert regex is filled with the resolve regex's
                        first capture group.
    STATE_FILE          default ~/.local/state/ntfy-to-todoist.json
"""
import json
import os
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request

API = "https://api.todoist.com/api/v1"
DRY_RUN = "--dry-run" in sys.argv


def env(name, default=None, required=False):
    v = os.environ.get(name, default)
    if required and not v:
        sys.exit(f"missing environment variable {name}")
    return v


NTFY_TOPIC_URL = env("NTFY_TOPIC_URL", required=True).rstrip("/")
NTFY_TOKEN = env("NTFY_TOKEN", "")
TODOIST_TOKEN = env("TODOIST_TOKEN", required=True)
PROJECT_ID = env("TODOIST_PROJECT_ID", "")
TASK_PREFIX = env("TASK_PREFIX", "⚠️ Alert: ")
TASK_PRIORITY = int(env("TODOIST_PRIORITY", "3"))
MIN_PRIORITY = int(env("MIN_PRIORITY", "4"))
COOLDOWN = float(env("COOLDOWN_HOURS", "24")) * 3600
EXCLUDE = re.compile(env("EXCLUDE_TITLES", "(?!x)x"), re.IGNORECASE)
STATE_FILE = pathlib.Path(env("STATE_FILE", "~/.local/state/ntfy-to-todoist.json")).expanduser()


def load_rules():
    path = env("RESOLVE_RULES_FILE", "")
    if not path:
        return []
    with open(os.path.expanduser(path)) as fh:
        return list(json.load(fh).items())


RESOLVE_RULES = load_rules()


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def normalize(title):
    """Dedup key: collapse numbers so parameterized titles match across readings."""
    return re.sub(r"[\d.]+", "#", title)


def api(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": f"Bearer {TODOIST_TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read().decode()
    return json.loads(body) if body.strip() else {}


_open = None


def open_alert_tasks():
    """Open, non-recurring tasks whose content starts with TASK_PREFIX. Fetched once per run."""
    global _open
    if _open is None:
        got, cursor = [], None
        word = TASK_PREFIX.strip(" :⚠️") or TASK_PREFIX.strip()
        while True:
            q = {"query": f"search:{word}", "limit": 200}
            if cursor:
                q["cursor"] = cursor
            d = api("GET", "/tasks/filter?" + urllib.parse.urlencode(q))
            got += d.get("results", [])
            cursor = d.get("next_cursor")
            if not cursor:
                break
        _open = [t for t in got if t.get("content", "").startswith(TASK_PREFIX)
                 and not (t.get("due") or {}).get("is_recurring")]
    return _open


def alert_patterns(resolved):
    """Regexes for the alert titles that "Resolved: <resolved>" clears."""
    pats = []
    for rule, templates in RESOLVE_RULES:
        m = re.fullmatch(rule, resolved)
        if m:
            arg = re.escape(m.group(1)) if m.groups() else ""
            pats += [t.format(arg) for t in templates]
    return pats or [re.escape(resolved)]  # default: titles starting with the name


def close_resolved(resolved):
    pats = alert_patterns(resolved)
    for task in list(open_alert_tasks()):
        title = task["content"][len(TASK_PREFIX):].strip()
        if not any(re.match(p, title) for p in pats):
            continue
        if DRY_RUN:
            log(f"WOULD CLOSE {task['id']} '{task['content']}'")
            continue
        try:
            api("POST", "/comments", {"task_id": task["id"],
                                      "content": f"auto-closed {time.strftime('%Y-%m-%d %H:%M')}: "
                                                 f"'Resolved: {resolved}' received"})
        except Exception as e:  # a missing comment must not block the close
            log(f"comment failed on {task['id']}: {e}")
        api("POST", f"/tasks/{task['id']}/close")
        _open.remove(task)
        log(f"closed {task['id']} '{task['content']}'")


def create_task(title, body):
    if DRY_RUN:
        log(f"WOULD CREATE '{TASK_PREFIX}{title}'")
        return
    task = {"content": f"{TASK_PREFIX}{title}",
            "description": body + f"\n\n(from ntfy, {time.strftime('%Y-%m-%d %H:%M')})",
            "priority": TASK_PRIORITY}
    if PROJECT_ID:
        task["project_id"] = PROJECT_ID
    api("POST", "/tasks", task)
    log(f"created '{TASK_PREFIX}{title}'")


def main():
    state = {"since": int(time.time()) - 600, "seen": [], "task_created": {}}
    if STATE_FILE.exists():
        state.update(json.loads(STATE_FILE.read_text()))

    headers = {"Authorization": f"Bearer {NTFY_TOKEN}"} if NTFY_TOKEN else {}
    req = urllib.request.Request(f"{NTFY_TOPIC_URL}/json?poll=1&since={state['since']}", headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        lines = r.read().decode().splitlines()

    seen, new_seen, latest = set(state["seen"]), [], state["since"]
    for line in lines:
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        if m.get("event") != "message":
            continue
        latest = max(latest, m.get("time", 0))
        new_seen.append(m.get("id"))
        if m.get("id") in seen:
            continue
        title = m.get("title") or (m.get("message") or "")[:60]
        try:
            # "Resolved:" notes are usually low priority, so handle them before the gate
            if title.startswith("Resolved: "):
                close_resolved(title[len("Resolved: "):].strip())
                continue
            if m.get("priority", 3) < MIN_PRIORITY or EXCLUDE.search(title):
                continue
            key = normalize(title)
            if time.time() - state["task_created"].get(key, 0) < COOLDOWN:
                continue
            create_task(title, (m.get("message") or "").strip()[:500])
            state["task_created"][key] = int(time.time())
        except Exception as e:
            log(f"Todoist call failed for '{title}': {e}")
            sys.exit(1)  # leave state unsaved so the next run retries

    if DRY_RUN:
        return
    # rewind 5 min for poll overlap; 'seen' ids dedupe the overlap window
    state["since"] = max(latest - 300, 0)
    state["seen"] = new_seen[-200:] or state["seen"]
    now = time.time()
    state["task_created"] = {k: t for k, t in state["task_created"].items() if now - t < COOLDOWN}
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))


if __name__ == "__main__":
    main()
