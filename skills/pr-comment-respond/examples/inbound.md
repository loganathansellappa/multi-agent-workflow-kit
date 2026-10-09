# Example: inbound (address comments on my PR)

Fully fictional. A reviewer left two comments on PR 1234 in `service-a`.

## 1. List the open comments

```
python pr_comments.py list --slug service-a --pr 1234
```

```json
{
  "pr": 1234,
  "slug": "service-a",
  "branch": "feature/widget-cache",
  "service": "service-a",
  "repoPath": "/work/service-a",
  "baseBranch": "main",
  "developerAgent": "backend-developer",
  "reviewerAgent": "code-reviewer",
  "comments": [
    {"id": 51, "author": "dana", "text": "This cache never expires.",
     "anchorPath": "src/cache.py", "anchorLine": 40, "state": "OPEN"},
    {"id": 52, "author": "dana", "text": "Typo in the log message.",
     "anchorPath": "src/cache.py", "anchorLine": 71, "state": "OPEN"}
  ]
}
```

## 2. Judge each against the real code

- `src/cache.py:40` - confirmed: the cache dict has no TTL. Valid.
- `src/cache.py:71` - confirmed: "recieved" is misspelled. Valid.

## 3. Delegate the fix

Both are server-side, so route to the returned `developerAgent` (`backend-developer`).
It adds a TTL and fixes the log string through its own clean gate, and does not commit.

## 4. Confirmation gate, then commit + push

Show the user the drafts and `git -C /work/service-a --no-pager diff --stat`, ask for
approval, then commit one line and push the task branch (never `main`).

## 5. Reply + resolve

```
python pr_comments.py reply --slug service-a --pr 1234 --comment 51 \
  --text "Good catch. Added a 5 minute TTL and a test for eviction."
python pr_comments.py resolve --slug service-a --pr 1234 --comment 51
```

The reply goes through the humanize gate first. A slop-flavored draft (`Great catch, I've carefully addressed this by ensuring robust expiry`) would be rejected. The plain version above passes.
