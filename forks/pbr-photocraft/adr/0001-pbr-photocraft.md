# pbr-photocraft 0001: PhotoCraft's 3D/UV workspace on the shared engine

- **Project:** storytold/photocraft, on branch `fork/pbr-photocraft`
- **Status:** draft. Members can change this record by vote until the fork opens (its first patch is admitted). After that it is frozen.

## Context

PhotoCraft #8805 adds an opt-in 3D/UV workspace with texture painting and editable masks. It brings its own mesh loading and its own metallic–roughness renderer.

## Decision

PhotoCraft gets a 3D/UV workspace whose mesh import, textures and rendering come from the shared craft-libs PBR crates. PhotoCraft keeps what is its own: the UV view, painting and masks, and painting into ordinary PhotoCraft layers.

## Out of scope

Sculpting and procedural materials. Dedicated texturing apps are new projects, not forks.

## Done when

- [ ] The workspace works on the shared crates, and PhotoCraft has no renderer or importer of its own.
- [ ] Painting a layer updates the 3D view, and the result exports as a texture set.
- [ ] The workspace stays opt-in, and upstream's tests pass.

## Open questions

The members decide each of these by vote, as the fork's next records (PROCESS.md, Decisions):

- How much of #8805 to keep as it is, and in what order to move its parts onto the shared crates.
- How painted layers map to the material's maps.
