# Other hosts

The bundled `pr_comments.py` speaks **Bitbucket Server / Data Center** REST as the
reference adapter. The skill shape (list open comments, reply, post inline or
general, resolve, humanize) maps cleanly onto the other hosts. Point the same config
block at your host and swap the REST calls in the `BB` class.

The method, the humanize gate, and both paths (inbound and authoring) stay the same.
Only the endpoints and the JSON shape differ.

## GitHub (and GitHub Enterprise)

- Auth: `Authorization: Bearer <token>` (a fine-grained or classic PAT with
  pull-request read/write).
- List the review comments: `GET /repos/{owner}/{repo}/pulls/{pr}/comments` (inline) and
  `GET /repos/{owner}/{repo}/issues/{pr}/comments` (general).
- Reply to a review comment: `POST /repos/{owner}/{repo}/pulls/{pr}/comments` with
  `in_reply_to`.
- Post inline: `POST /repos/{owner}/{repo}/pulls/{pr}/comments` with `path` +
  `line` + `commit_id`. Post general: `POST .../issues/{pr}/comments`.
- Resolve: review threads resolve through the GraphQL `resolveReviewThread` mutation.

## GitLab

- Auth: `PRIVATE-TOKEN: <token>` or `Authorization: Bearer <token>`.
- Merge-request discussions: `GET /projects/{id}/merge_requests/{iid}/discussions`.
- Reply in a thread: `POST .../discussions/{discussion_id}/notes`.
- Post inline: `POST .../discussions` with a `position` object. Post general:
  `POST .../notes`.
- Resolve: `PUT .../discussions/{discussion_id}?resolved=true`.

## Bitbucket Cloud

- Auth: app password or OAuth bearer token.
- Comments: `GET/POST /2.0/repositories/{workspace}/{repo}/pullrequests/{pr}/comments`.
- Inline: include an `inline` object (`path`, `to`). Reply: include
  `parent.id`. Resolve: the `resolution` field on the comment.

## Keep the gate

On any host, keep the post path going through `finalize_text()` so the humanize and
slophound gate still runs. The gate works the same on every host. It is the point of
the skill.
