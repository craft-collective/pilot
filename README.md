# Craft Collective

**Shared forks of the storytold Craft apps, for features upstream hasn't merged yet.**

Many good changes to the Craft apps sit as open pull requests, and the people who want them each carry their own copy. Often several people write the same feature independently. A Craft Collective fork is one place for one such feature:
- a written goal and its decisions, adopted by vote;
- one branch per repository it changes;
- members who review patches into it and sync it with upstream on demand, each credited with the work they do.

Nobody here is a gatekeeper: the members decide, and a bot carries out what they decide. This is a by-hand pilot of [Collective](https://collective.software)'s forks.

| Fork | Repositories | Builds on | Founding record |
|---|---|---|---|
| `web-host` | vectorcraft | – | [VectorCraft on the web, hosted by other pages](forks/web-host/adr/0001-web-host.md) |
| `pbr-engine` | craft-libs, effectcraft | – | [One PBR engine, extracted from EffectCraft into craft-libs](forks/pbr-engine/adr/0001-pbr-engine.md) |
| `pbr-vectorcraft` | vectorcraft | `pbr-engine` | [PBR materials on VectorCraft's 3D effects](forks/pbr-vectorcraft/adr/0001-pbr-vectorcraft.md) |
| `pbr-deckcraft` | deckcraft | `pbr-engine` | [3D models in DeckCraft](forks/pbr-deckcraft/adr/0001-pbr-deckcraft.md) |
| `pbr-photocraft` | photocraft | `pbr-engine` | [PhotoCraft's 3D/UV workspace](forks/pbr-photocraft/adr/0001-pbr-photocraft.md) |

All five are drafts. Their open questions are listed at the end of each founding record, waiting for their members to settle them.

## Start here

- **People:** read [PROCESS.md](PROCESS.md). It covers how decisions, patches, reviews and syncs work, and how credit is counted.
- **Agents:** read [AGENTS.md](AGENTS.md). It covers where to look before writing code, what to send where, and the rules the bot enforces.

## Using a fork

```sh
git clone https://github.com/craft-collective/vectorcraft && cd vectorcraft
git switch fork/web-host      # build it, use it, or branch from it
git switch -c my-patch        # … then open a PR against fork/web-host
```

## Taking part

| To | Do |
|---|---|
| Join a fork | Comment `/join <fork>` on any issue here |
| Propose a decision | Open a PR adding `forks/<fork>/adr/NNNN-<slug>.md` |
| Propose a fork | Open a PR adding `forks/<id>/fork.json` and `forks/<id>/adr/0001-<slug>.md` |
| Vote | Comment `/vote yes` or `/vote no <reason>` on the PR |
| Sync a fork | Comment `/sync <fork>` on any issue here; resolve any conflict and comment `/ready` |
| Test and review a sync | Run the checks on the sync branch, read the range-diff, comment `/promote` |

The pilot is in its **solo phase** while its founder tries the whole cycle on both forks, so joining opens when it ends (PROCESS.md).

## The bot

`scripts/pilot.py` runs in this repository's GitHub Actions:

| Workflow | When | Does |
|---|---|---|
| `votes` | On PRs, `/vote`, `/join` and `/leave`, and hourly | Tallies votes, merges what passes, updates membership |
| `apply` | When a fork or `pilot.json` changes on `main` | Makes the org's teams, branches and rules match |
| `sync` | When run by hand for a fork | The same as `/sync`, credited to whoever ran it |
| `commands` | `/sync <fork>` on any issue; `/ready` or `/promote` on a sync issue | Syncs a fork, checks a conflict resolution, or promotes a sync |
| `reviewers` | Hourly | Assigns two reviewers to each new patch PR |
| `check` | Every push | Validates the configuration and runs the tests |

Locally:
- `pilot.py ledger` counts who has done what.
- `--dry-run` prints what any command would do on GitHub, without doing it.
- `python3 -m unittest discover -s scripts` runs the tests.

The bot runs as its own machine account; [SETUP.md](SETUP.md) has the one-time setup.
