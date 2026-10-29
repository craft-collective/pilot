# For agents

You are working in the Craft Collective pilot: shared forks of the storytold Craft apps for features upstream hasn't merged. Read this file, then [PROCESS.md](PROCESS.md) for the full rules. The bot enforces those rules, so following them is the quickest way to get anything done.

## Before you write code, look for it

The work you are about to do may already exist.
1. Look in `forks/*/adr/0001-*.md` for a fork whose goal covers your task. Its `fork.json` names the branches.
2. Look at the fork branch (`fork/<id>`) in this org's copy of the repository, and at its open PRs.
3. Look at upstream's open PRs and issues: most features in the Craft apps have been written more than once.

If a fork covers your task, build on its branch instead of on upstream `main`, and send your patch to the fork. If one doesn't, and the task is a feature someone else would adopt on its own, propose a fork (below).

## What you can do, and how

| To | Do |
|---|---|
| Build on a fork | `git clone https://github.com/craft-collective/<repo>`, then `git switch fork/<id>` |
| Send a patch | Open a PR against `fork/<id>`. Squash-mergeable, upstream-ready, with tests. Say which record lines it serves. |
| Review a patch | Judge it against the fork's records (quote the line), then check safety and quality using the checklist the bot posts |
| Propose an implementation detail | A PR to this repository adding `forks/<id>/adr/NNNN-<slug>.md`, numbered after the fork's last record |
| Propose a fork | A PR adding `forks/<id>/fork.json` and `forks/<id>/adr/0001-<slug>.md`. Copy an existing fork's layout. |
| Resolve a conflicted sync | Follow the steps in the sync issue, push the sync branch, comment `/ready` |
| Join or leave a fork | Comment `/join <fork>` or `/leave <fork>` on any issue here |
| Vote | Comment `/vote yes` or `/vote no <reason>` on the PR |

## Rules that are easy to break

- **One PR, one scope.** A PR to this repository changes one fork's directory, or the process, never both.
- **Records don't refer to other forks.** If something only matters when two forks are combined, it belongs in a combination fork. Its parents are both forks.
- **Adopted records are frozen.** Supersede a record with a new one; never edit it. A draft fork's `0001` is the only exception.
- **Members aren't edited in PRs.** Use `/join` and `/leave`.
- **Write craft-libs dependencies the upstream way:** `git = "https://github.com/storytold/craft-libs"`, at the same `rev` as the rest. Each fork branch's top commit, `pilot: fork metadata`, redirects them to the fork's craft-libs. Leave that commit alone, and never include it in a patch.
- **Don't merge into a fork while its sync issue is open.**
- **A resolution's reviewer is someone else.** If you resolved a sync, you don't promote it.

## Votes belong to people

You act through your person's GitHub account, and the account is the member. Draft, discuss and summarize as much as is useful. Cast `/vote` only when your person has decided how to vote, and never vote from more than one account.

Say when a comment or a review was written by an agent. A line such as "Drafted by an agent for @user" is enough.

## Checks

Each repository's checks are in `pilot.json` under `repos`. Run them before you send a patch and before you promote a sync. The project's own `CLAUDE.md` or `AGENTS.md` in each Craft repository still applies inside it.
