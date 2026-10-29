# web-host 0001: VectorCraft on the web, hosted by other pages

- **Project:** storytold/vectorcraft, on branch `fork/web-host`
- **Status:** draft. Members can change this record by vote until the fork opens (its first patch is admitted). After that it is frozen.

## Context

People embed VectorCraft's web build in their own pages: hubs, CMSs, course sites and the iPad wrapper. Each of them has written their own glue, and none of it has merged.

- The `?host=parent` postMessage bridge has been written twice for VectorCraft: #1094 (CyberFox-81) and #3503 (halldave). Across the suite there are 24 open bridge PRs in 12 apps.
- Open/Save to somewhere other than the browser comes in several forms: CollabCraft in #2011, CraftHub in #2059 and WebDAV in #4586. Issue #4111 asks for the hosting page to supply the storage itself.
- Fonts on the web are limited to what the build ships (#2906, #1164). Other apps let the host supply fonts, as in DeckCraft #1040 and GridCraft #1467, and craft-fonts #20 proposes a shared web font library. Hosts will want to supply other resources the same way, such as PBR materials and texture sets (#2560).
- All 25 translation catalogs are compiled into the wasm (`include_str!` in `crates/ui-egui/src/i18n.rs`), so every visitor downloads every language. Some craft-fonts faces are already fetched beside the wasm instead (`crates/text/web-fonts.txt`, with Subresource Integrity checks).
- Audit #3606: `window.vectorcraft` gives any embedding page the whole control surface.

## Decision

The web build becomes something a host page can embed safely and cheaply. The fork has four areas, each a label on its PRs:

1. **`area:bridge`.** A host page can drive the app over one documented message protocol. The bridge is off unless the page asks for it. It accepts messages only from origins the host declares, and saves that leave the app must be confirmed. Together these close #3606 for embedded use.
2. **`area:storage`.** Open and Save go through one storage interface, and the host page can provide the storage over the bridge. Other backends are patches like any other.
3. **`area:size`.** The first download is measured, and it shrinks. Translation catalogs and other content the first frame doesn't need are fetched when they are needed, not embedded. A build that has to work offline can still embed everything.
4. **`area:resources`.** One interface lets the host supply resources the build doesn't ship, starting with fonts and PBR materials. Supplied resources appear beside the built-in ones in the app's pickers, and a document that uses one records it the way it records a linked file. This fork loads and lists resources. What an app does with a material is outside it.

**Requirements** (these are what makes it safe to embed, so they aren't up for an implementation vote):
- With the bridge off, the web build behaves exactly as upstream's does.
- Anything fetched is checked against an integrity hash before it is used. A resource whose kind the build doesn't know is refused.
- Native builds are unchanged.

## Out of scope

- Live collaboration. Storage reads and writes whole documents.
- Plug-ins (#121, #2190, #2191).

## Done when

- [ ] The bridge's protocol is documented, and tests cover the origin check and confirmed saves.
- [ ] A demo host page in the repository embeds the build, opens and saves a document through host storage, and supplies a font and a material that the app lists and uses.
- [ ] The first download is measured in CI and reported. Choosing a non-English locale fetches its catalog on demand.
- [ ] Upstream's tests pass.

## Open questions

The members decide each of these by vote, as the fork's next records (`0002`, `0003` and so on; PROCESS.md, Decisions):

- Which bridge to build on, #1094 or #3503, and what to take from the other. Whether to match the protocol other apps' bridges use.
- How the host declares its origins and turns the bridge on.
- The storage interface, and which backends besides the host page are worth carrying (#2011, #2059 and #4586 are candidates).
- How resources are supplied (a manifest, bridge messages or both), the manifest's format, and how materials' files are named.
- What else besides catalogs leaves the first download, and the target size.

## Notes for reviewers

The fork is deliberately wide: part of what the pilot is testing is whether a fork this wide stays judgeable. If patches in one area never need the others, propose splitting that area into a fork of its own.
