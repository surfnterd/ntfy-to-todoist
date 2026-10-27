# ntfy-to-todoist

Turns urgent [ntfy](https://ntfy.sh) alerts into [Todoist](https://todoist.com) tasks, and closes them again when a "Resolved:" message arrives.

Push notifications are easy to swipe away. If an alert needs action (a failed backup, a full disk, a container that won't start), it should end up on the to-do list you actually work from. This bridge does that, without flooding the list.

## What it does

- **Only urgent alerts cross.** Messages at ntfy priority 4 (high) or 5 (max) become tasks. Routine messages are ignored.
- **No duplicate tasks.** An alert firing every two minutes creates one task per 24 hours, not hundreds. Numbers are collapsed before comparing titles, so `CPU 88% (load 18.1)` and `CPU 63% (load 17.0)` count as the same alert.
- **Auto-close.** Send `Resolved: <name>` (any priority) and every open bridge task whose title starts with `<name>` gets a comment and is closed. A rules file handles alerts whose resolve message is worded differently (below).
- **Optional hold.** Set `HOLD_MINUTES` and an alert waits that long before it becomes a task. If its `Resolved:` arrives during the wait, no task is made at all, so alerts that flap and clear themselves stay off your list. Off by default.
- **Safe on failure.** If Todoist or ntfy errors, the run exits without saving its position, so the next run retries those messages.
- `--dry-run` shows what it would create and close, and writes nothing.

## Setup

Python 3.8+, standard library only.

```sh
export NTFY_TOPIC_URL=https://ntfy.example.com/alerts
export NTFY_TOKEN=...          # only if the topic needs auth
export TODOIST_TOKEN=...       # Todoist › Settings › Integrations › Developer
python3 ntfy_to_todoist.py --dry-run
```

Keep the tokens in an env file with 600 permissions rather than in your crontab. Example cron, every 5 minutes:

```cron
*/5 * * * * set -a; . $HOME/.config/ntfy-to-todoist.env; python3 $HOME/ntfy-to-todoist/ntfy_to_todoist.py >> $HOME/.local/state/ntfy-to-todoist.log 2>&1
```

| Variable | Default | Meaning |
|---|---|---|
| `TODOIST_PROJECT_ID` | Inbox | Where new tasks go |
| `TASK_PREFIX` | `⚠️ Alert: ` | Marks bridge-made tasks. Only tasks with this prefix are ever closed |
| `TODOIST_PRIORITY` | `3` | Todoist API scale, 4 = p1, 3 = p2 |
| `MIN_PRIORITY` | `4` | ntfy priority gate |
| `COOLDOWN_HOURS` | `24` | One task per alert title per this many hours |
| `HOLD_MINUTES` | `0` | Wait this long before creating a task; a `Resolved:` in that time drops the alert. A held alert is created on the first run after its hold ends, so keep the cron running |
| `EXCLUDE_TITLES` | none | Regex; matching alerts are never bridged (good for alerts that clear themselves) |
| `RESOLVE_RULES_FILE` | none | JSON mapping resolve names to alert titles (below) |
| `STATE_FILE` | `~/.local/state/ntfy-to-todoist.json` | Last-seen position and dedup times |

### Resolve rules (optional)

Without a rules file, `Resolved: X` closes open tasks whose title starts with `X`. When an alert and its resolve message are worded differently, map them:

```json
{
  "Disk space on (.+)": ["Disk space (?:CRITICAL|low): {0}"],
  "Container health": ["Container\\(s\\) in trouble"]
}
```

`{0}` is filled with the first capture group of the resolve name.

## Licence

MIT
