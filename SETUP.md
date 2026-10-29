# Setting up the bot

This is done once, by an org owner. After that, the `apply` workflow keeps the org in step with `main`.

1. **Make a machine account** for the bot, for example `craft-collective-bot`, and make it an owner of the org. The bot's comments, merges and pushes are then its own and not a person's. Don't use a member's personal account: its comments would be indistinguishable from the bot's, and the bot trusts only its own tally comments.
2. **Make a token** on that account: a classic token with `repo`, `workflow` and `admin:org`. Store it as the `PILOT_TOKEN` Actions secret in this repository. Until the secret exists, every workflow runs but does nothing.
3. **Look before applying:** run `python3 scripts/pilot.py --dry-run setup` and read what it would do. Then run the `apply` workflow, which does it. It:
   - forks the five repositories into the org, allowing only squash merges;
   - makes each fork's team and branch, and protects `fork/*`;
   - protects this repository's `main`, so that only voted PRs reach it;
   - makes the labels.
4. **Write each fork's metadata commit:** `/sync` each fork once, `pbr-engine` first. Once it is promoted, sync the three integration forks, then `web-host`.

Upstream's own CI workflows don't run in the org's copies. Forks have Actions off by default, and the rebaser runs each repository's checks from `pilot.json` instead.
