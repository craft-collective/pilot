# Setting up the bot

This is done once, by an org owner. After that, the `apply` workflow keeps the org in step with `main`.

1. **Make the bot a GitHub App** owned by the org: Org settings → Developer settings → GitHub Apps → New GitHub App.
   - Name it, for example `craft-collective-pilot`. Its comments, merges and pushes appear as `craft-collective-pilot[bot]`, which no person's account can be. The bot trusts only tally comments by that login.
   - Homepage: this repository. Turn the webhook off.
   - Repository permissions: Administration, Contents, Issues, Pull requests and Workflows, each read and write. Metadata is read-only.
   - Organization permissions: Members, read and write, so it can manage the fork teams.
   - Where it can be installed: only this organization.
2. **Install it** on the org, with access to **all repositories**, so it reaches the forks setup makes.
3. **Give the bot its identity:**
   - Put the App's id (on its settings page) in `pilot.json` as `app_id`, and commit it to `main`.
   - Generate a private key on the same page, and store the whole `.pem` as the `PILOT_APP_KEY` Actions secret in this repository: `gh secret set PILOT_APP_KEY -R craft-collective/pilot < key.pem`. Then delete the file.
   - Until the secret exists, every workflow runs but does nothing.
4. **Run setup once yourself.** The App can't fork another account's repositories, so the first run is an org owner's, by hand: logged in to `gh` as yourself, run `python3 scripts/pilot.py --dry-run setup` and read what it would do. Then run `python3 scripts/pilot.py setup`, which does it. It:
   - forks the five repositories into the org, allowing only squash merges;
   - makes each fork's team and branch, and protects `fork/*`;
   - protects this repository's `main`, so that only voted PRs reach it;
   - lets the App and org owners bypass those rules, so the bot can merge passed votes and promote syncs;
   - makes the labels.

   From then on, the `apply` workflow runs setup as the App whenever `main` changes a fork or `pilot.json`.
5. **Write each fork's metadata commit:** `/sync` each fork once, `pbr-engine` first. Once it is promoted, sync the three integration forks, then `web-host`.

Upstream's own CI workflows don't run in the org's copies. Forks have Actions off by default, and the rebaser runs each repository's checks from `pilot.json` instead.
