# pbr-vectorcraft 0001: PBR materials on VectorCraft's 3D effects

- **Project:** storytold/vectorcraft, on branch `fork/pbr-vectorcraft`
- **Status:** draft. Members can change this record by vote until the fork opens (its first patch is admitted). After that it is frozen.

## Context

VectorCraft's 3D effects (Extrude & Bevel, Inflate, Rotate, Revolve) shade projected vector facets with Lambert lighting. There is no mesh, no UVs and no materials. Users want Illustrator's 3D and Materials panel (#2560, #1407, #3669 and #3672–#3675).

## Decision

VectorCraft's 3D effects get physically based materials (Roughness, Metallic and base color), drawn as vectors so that they print and export as paths. The shading comes from the shared craft-libs PBR crates, not from code of VectorCraft's own.

**Requirements:**
- Documents made before this change render exactly as they did, which a test pins.
- The output stays vector: SVG and PDF export draw paths.
- The web build still builds.

## Out of scope

Raster output (rendering 3D as an image with texture maps and shadows), HDR environments and ray tracing. Each is a fork of its own, on top of this one.

## Done when

- [ ] Metallic, rough 3D type and art export as paths to SVG and PDF.
- [ ] Existing documents render unchanged, with a test pinning it.
- [ ] VectorCraft has no shading code of its own beyond calls into the shared crates.
- [ ] Upstream's tests pass.

## Open questions

The members decide each of these by vote, as the fork's next records (PROCESS.md, Decisions):

- How a facet is shaded with a smooth result: how many sample points, and which gradient type.
- The shape of the `material` effect parameter in documents and in the engine's commands.
- What the Materials tab offers and where it lives in the menus.
- What a texture map contributes, if anything, in vector output.
