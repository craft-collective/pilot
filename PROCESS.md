# How the pilot works

Each fork is one feature that upstream hasn't merged, kept current and usable by the people who want it. A fork has a set of decision records, a set of members, and a branch in each repository it changes. Its members decide together, review patches into it, and take turns rebasing it onto upstream every week.

**Nobody is a gatekeeper.** Every rule here is enforced by the bot (`scripts/pilot.py`), and every change to the rules, a fork or its decisions is a PR that the people it binds vote on. The bot merges what passes. No person, including the pilot's founder, approves anything alone. What is still centralized is listed at the end.

## Forks

- A fork lives in `forks/<id>/`. Its `fork.json` lists its branches, its parents and its members.
- Its decisions are in `forks/<id>/adr/`, **numbered within the fork**: `0001-<slug>.md`, `0002-<slug>.md` and so on.
  - `0001` is the founding record: the goal, the requirements, what is out of scope, when the fork is done, and its open questions.
  - Later records settle the open questions and other implementation details.
- **A fork's records never refer to another fork.** A fork that depends on another names it as a parent in `fork.json`. Anything that only matters when two forks are used together belongs in a **combination fork**: a fork whose parents are both of them, carrying the glue patches and its own records.
- Lifecycle: `draft` → `open` → `done` → `merged`, `superseded` or `stale`.
  - `0001` can be changed by vote while the fork is a `draft`.
  - The fork **opens**, and `0001` is frozen, when its first patch is admitted.
  - A fork is `done` when its criteria are met, and after that it takes bug fixes only. It is `merged` when upstream takes it, `superseded` when upstream does the same thing another way, and `stale` after three missed syncs.
  - Records after `0001` are frozen as soon as they are adopted. To change one, adopt a new record that supersedes it.
  - Changing a frozen `0001` means a new child fork.

## Decisions

Every change to this repository is a pull request, and every PR is voted on. That covers a new fork, a decision record, and a change to the process or to the bot.

1. **Propose:** open a PR. It changes **one** scope: one fork's directory, or the process (everything outside `forks/`). The bot refuses PRs that mix scopes. It also refuses PRs that edit an adopted record or change members (members change by `/join` and `/leave`).
2. **Discuss:** use the PR's comments and line comments. Questions about implementation details belong here, before any code is written. Change the PR as the discussion settles.
3. **Vote:** comment `/vote yes`, or `/vote no` followed by a reason. A no without a reason isn't counted, because the point of a no is to say what would change it.
   - **Who votes:** on a fork, its members. On a new fork, its proposer and everyone who commented `/join` on the PR. On the process, every member of every fork.
   - **Changing the PR resets the votes:** each vote is on the commit it was cast against.
   - Accounts younger than `votes.min_account_age_days` can't vote or join.
4. **Pass:** the discussion has been open `votes.hours` since the last change, at least `votes.quorum` eligible people have voted (or all of them, if there are fewer), and `votes.majority` of the votes cast are yes. The bot then merges the PR. For a new fork, the proposer and the joiners become its founding members.

The bot keeps the tally in a comment on the PR and updates it on every vote and every change.

## Membership

Membership is open. Comment `/join <fork>` or `/leave <fork>` on any issue in this repository, and the bot updates the fork's `fork.json`. It also adds you to the fork's GitHub team, which can push to the fork's branches.

Joining gives you a vote on the fork's decisions. It also puts you in the fork's rotation and among its reviewers: membership is how the work gets shared, and a vote comes with a turn.

## Getting a patch in

1. Open a PR against the fork's branch (`fork/<id>`) in this org's copy of the repository. If your patch is already an upstream PR, rebase it onto the fork branch and link the upstream PR.
2. Within the hour, the bot assigns two reviewers from the fork's members. Neither is the author, and they are chosen at random but repeatably, seeded by the PR. It also posts the review checklist.
3. Reviewers judge two things:
   - **Conformance:** does the patch follow the fork's records, and is it within scope? Quote the record line it serves, or the line it breaks.
   - **Safety and quality:** look for network calls, process spawning, `unsafe`, build scripts, CI changes and new dependencies. The project's own `CLAUDE.md` and `AGENTS.md` conventions apply.
4. A patch needs two approvals, and at least one must come from a member (GitHub enforces this through the CODEOWNERS file on the fork branch). Patches are squash-merged, so one PR is one commit in the fork's stack. Credit co-authors with `Co-authored-by:`.
5. **Duplicates:** when members have written the same thing, decide which one to build on as a record (the open questions in `0001` say where this applies). The other author reviews it and ports across anything theirs did better.
6. **Out of scope** doesn't mean bad: the patch may belong in a child fork or a combination fork.

## The weekly sync

Every Monday at 06:00 UTC, the bot rebases each fork onto the **sync point**, which is upstream's `main` at that moment. The exception is a branch whose base is `pinned-by:`, such as `pbr-engine`'s craft-libs branch, which rebases onto the revision the named apps pin.

- **Clean (`sync:clean`).** Every patch re-applied. The bot opens an issue assigned to **this week's rebaser**, who runs the repository's checks on the sync branch and comments `/promote` if they pass.
- **Conflict (`sync:conflict`).** The bot pushes what applied and stops at the first conflicting patch. The rebaser resolves it on the sync branch, following the issue's instructions, and comments `/ready`. The bot posts the `git range-diff` and assigns **this week's reviewer**, who reviews only the resolution and comments `/promote`. A conflict resolution is the easiest place to hide something, so whoever resolved a sync can't promote it.
- **Already upstream.** A patch that becomes empty when re-applied has been merged upstream. It is dropped and listed in the issue.
- **Promote.** The bot tags the old head `archive/<fork>/<date>` and force-pushes the sync branch to `fork/<id>` with a lease.
- **Don't merge into a fork while its sync issue is open.** The bot refuses to promote over commits it didn't rebase. If you do merge, close the issue and run the sync again.
- **The cascade.** A child fork syncs after its parent is promoted, onto the parent's new head.
- **The metadata commit.** Each fork branch's top commit is `pilot: fork metadata`. It carries the CODEOWNERS file, and, for a fork that pins a parent's craft-libs, the redirected dependencies. The bot drops it and writes it again on every sync, so upstream bumping its own pin never conflicts. Don't put anything else in it.

We sync weekly because the storytold case study found that a fork weeks behind isn't usable, while rebasing more often costs more: weekly costs about 1.5× a single rebase at the end, and daily 4.3×.

## The rotation

Each fork's members take turns, in the order `fork.json` lists them, counted from the week number. The week's **rebaser** is member *w* mod *n*, and its **reviewer** is member *w*+1 mod *n*. `pilot.py rota` prints this week's and next week's turns.

- **Swap** by saying so in the sync issue and reassigning it.
- If a sync issue has had no movement after three days, the next member in the rotation may take it.
- A fork with fewer than two members can't promote a conflicted sync.

`pilot.py ledger` counts each member's admitted patches, reviews, syncs and sync reviews, so everyone can see who is carrying what.

## Agents

Many members work through coding agents; [AGENTS.md](AGENTS.md) is written for them.
- An agent acts through its person's GitHub account, and that account is the member. One person has one account and one vote.
- An agent may draft records, patches, reviews and resolutions. A vote is cast only when its person has decided it.

## Upstream

Patches stay upstream-ready: they follow the project's own conventions, docs and tests. A member may open the upstream PR for a patch they wrote. A whole fork goes upstream by a vote of its members. When upstream merges a patch, the next sync drops it.

## The solo phase

While the pilot is being tried out by one person, `pilot.json` has `review.solo` on:
- patches need no approvals;
- a vote passes on one yes with no waiting;
- `/join` is refused.

`pilot.py check` fails as soon as solo is on and a fork has a second member. Ending the solo phase is the first process vote: it sets `review.approvals` to 2 and `votes` to at least 48 hours and a quorum of 3, which `pilot.py check` requires outside the solo phase.

## What is still centralized

- **The org has owners**, and an owner can override anything on GitHub. The founder is one. The pilot's first process vote after the solo phase should add more owners, chosen by the members.
- **The bot's token** belongs to an org owner's machine account. Whoever holds it could act as the bot.
- **Identity is a GitHub account.** Membership is open and accounts are cheap, so votes resist sock puppets only as far as the account-age rule does.

## What we're testing

The pilot is a manual version of Collective's forks. At the end, we want to know:
- **Rebase cost:** the share of syncs that were clean, and the size and time of each conflict resolution.
- **Review:** time to two approvals, and how often a reviewer quoted a record to reject a patch.
- **Decisions:** how long votes took, how often a no with a reason changed a proposal, and whether open questions were settled before the code was written.
- **Scope:** whether the wide `web-host` fork stayed judgeable, or wanted to split. Its four areas are its labels.
- **Stacking:** what the `pbr-engine` cascade cost each integration fork.
- **Use:** who builds from a fork branch, and who has stopped carrying their own patch.
