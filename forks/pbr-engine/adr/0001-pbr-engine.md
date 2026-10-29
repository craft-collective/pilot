# pbr-engine 0001: One PBR engine for the Craft apps, extracted from EffectCraft into craft-libs

- **Projects:** storytold/craft-libs (`fork/pbr-engine`), plus storytold/effectcraft (`fork/pbr-engine`), which switches EffectCraft to the extracted crates
- **Status:** draft. Members can change this record by vote until the fork opens (its first patch is admitted). After that it is frozen.

## Context

EffectCraft already has a metallic–roughness PBR renderer, with texture sampling, mesh import (glTF, OBJ, STL, FBX), a software rasterizer and a wgpu twin. Other apps keep writing it again inside their own features: DeckCraft's 3D models (#743, #1380) and PhotoCraft's 3D/UV workspace (#8805). VectorCraft has asked for it for materials on 3D effects (#2560, #1407, #3669). The OBJ and glTF fixes waiting in EffectCraft (#1324, #1504, #2460) would then apply to every app that uses the engine.

## Decision

EffectCraft's PBR engine, and the tools it stands on (textures, meshes and import, the rasterizer, the GPU twin), move into craft-libs crates any app can take. EffectCraft then uses those crates and keeps no copy of its own.

**Requirements:**
- **Move first, then improve.** Code moves with its tests, unchanged except for paths and craft-libs' own lints. **EffectCraft's tests pass unchanged on the extracted crates.** That is the proof the extraction is right. Improvements come afterwards, as separate patches.
- EffectCraft is never on two copies of the same code: each extraction patch is paired with the EffectCraft patch that switches to it.
- The crates do no UI and no file or network I/O, forbid `unsafe`, are deterministic, and build for wasm32 headless (no threads, no imports), so apps embedded in Workers or browsers can use them.

## Out of scope

- Anything app-specific: UI, document formats or any app's effect parameters.
- New rendering features beyond EffectCraft's current ones (ray tracing, procedural materials). Each of those is a fork of its own.

## Done when

- [ ] The extracted crates are on `fork/pbr-engine` in craft-libs, passing craft-libs' checks and a headless wasm32 build.
- [ ] EffectCraft on `fork/pbr-engine` renders Advanced 3D from the crates and passes `cargo xtask ci` with its tests unchanged.
- [ ] The importer fixes from #1324, #1504 and #2460 are on the crates, credited to their authors.

After it is done, the fork takes only bug fixes.

## Open questions

The members decide each of these by vote, as the fork's next records (PROCESS.md, Decisions):

- How to split the crates. One proposal is textures, meshes and import, shading, the CPU rasterizer and its GPU twin, as five crates.
- Whether mesh math stays f64 as in EffectCraft, or moves to f32.
- Whether the GPU twin and its parity tests are extracted now or later.
- The order of the extraction patches.
