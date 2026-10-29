# pbr-deckcraft 0001: 3D models in DeckCraft on the shared engine

- **Project:** storytold/deckcraft, on branch `fork/pbr-deckcraft`
- **Status:** draft. Members can change this record by vote until the fork opens (its first patch is admitted). After that it is frozen.

## Context

Two open DeckCraft PRs insert and turn 3D models: #743 (GLB, glTF, OBJ, STL and PLY, with animation) and #1380 (STL, OBJ, FBX and glTF). Each brings its own importer and renderer. ArtCraft #1210 asks for the same formats.

## Decision

DeckCraft gets Insert › 3D Models, with import and drawing done by the shared craft-libs PBR crates. DeckCraft keeps only what is its own: the insert UI, the slide object, turning and animating it, and PowerPoint import and export of models. Format support DeckCraft needs that the shared crates lack belongs in the crates, not in DeckCraft.

## Out of scope

Editing meshes, painting textures, and any 3D feature PowerPoint doesn't have.

## Done when

- [ ] A GLB, glTF, OBJ, STL or FBX model can be inserted, turned and resized on a slide. It is drawn by the shared crates, and saves and loads with the presentation.
- [ ] DeckCraft has no importer or rasterizer of its own.
- [ ] Upstream's tests pass.

## Open questions

The members decide each of these by vote, as the fork's next records (PROCESS.md, Decisions):

- Which PR's DeckCraft side to build on, #743 or #1380, and what to take from the other.
- Whether animation is part of this fork, or a fork of its own on top of it.
- How a model is stored in a .pptx so that PowerPoint opens it.
