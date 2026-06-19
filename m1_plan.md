# M1 Plan: MVP Multimodal Scenario Generator

## Goal
Build the first runnable milestone of the multimodal scenario generator by extending the existing PARE scenario generator format. M1 keeps the current Step 1-4 pipeline, adds multimodal prompt support, uses local/provided image assets, and generates scenarios patterned after `pare/scenarios/multimodal_benchmark/`.

M1 should prove that the generator can produce benchmark-style multimodal scenarios without requiring automatic image generation yet.

## Scope
- Keep the current generator structure:
  - Step 1: scenario narrative.
  - Step 1.5: asset planning / `VisualAssetSpec`.
  - Step 2: apps and data setup.
  - Step 3: event flow.
  - Step 4: validation.
- Add full multimodal prompt changes in M1. This is required because the existing prompt stack assumes text-only scenarios.
- Use `pare/scenarios/multimodal_benchmark/` as the reference scenario directory for multimodal mode.
- Use local/provided image assets first via an asset manifest.
- Include `AssetProvider` in M1, but only as a local/manifest-backed implementation.
- Include `VisualQA` in M1, but keep it lightweight and deterministic by default.

## Out Of Scope
- Full automatic image generation with OpenAI `gpt-image-2`.
- Regenerate-on-QA-failure loops.
- Large image library curation UI.
- Complex multi-image distractor tasks with many candidates.
- Deterministic rendering templates for bills, receipts, posters, screenshots, and whiteboards.

## Prompt Changes

### Step 1: Scenario Narrative
Update `pare/scenarios/generator/prompt/scenario_generating_agent_prompts.py` so multimodal narratives must specify:

- what visual evidence exists,
- how the user/agent can access it,
- what inference requires vision,
- what action depends on that visual inference,
- what user acceptance gates the write action.

The prompt should explicitly refer to multimodal examples under `pare/scenarios/multimodal_benchmark/`, such as:

- `rice_cooker_photo_cart_suggestion.py`
- `friend_bird_photos_album_share.py`
- `bill_screenshot_payment_reminder_suggestion.py`
- `movie_poster_showtime_booking_suggestion.py`

### Step 1.5: Asset Planning
Add a new asset-planning prompt after Step 1 and before Step 2.

It should convert the narrative into a structured asset request:

```json
{
  "asset_id": "rice_cooker_photo",
  "filename": "rice_cooker.jpg",
  "source_path": "path/from/asset/library",
  "sandbox_path": "/photo.jpg",
  "delivery": "email_attachment",
  "visual_requirements": ["compact white rice cooker"],
  "ground_truth": {
    "object": "rice cooker"
  }
}
```

The output should be consumed by the local asset provider and passed into later generator steps.

### Step 2: Apps And Data Setup
Add guardrails so generated setup code:

- initializes `SandboxLocalFileSystem` when images are needed,
- connects Email or Album apps to that filesystem,
- loads assets from manifest paths,
- avoids embedding image bytes directly in scenario code,
- registers all apps needed for multimodal access.

### Step 3: Events Flow
Add guardrails so generated event flows:

- begin with a concrete non-oracle environment trigger,
- include a read/display/view oracle step before proposal,
- make visual evidence agent-visible before visually grounded action,
- keep write actions after `PAREAgentUserInterface.accept_proposal(...)`,
- use Email attachment or Album viewing patterns from `multimodal_benchmark`.

### Step 4: Validation
Add guardrails so generated validation checks:

- image inspection happened in the event log,
- final task side effect happened,
- proposal happened before write actions,
- visually grounded action uses evidence from the image rather than magic constants.

## Code Changes

### Generator CLI And Mode
- Add a generator flag such as `--multimodal`.
- Add optional asset inputs:
  - `--asset-manifest`
  - `--asset-dir`
- Ensure text-only behavior is unchanged when `--multimodal` is not enabled.

### App Inventory
Update `pare/scenarios/generator/utils/apps_init_instructions.py` and `pare/scenarios/generator/scenario_generator.py` to include:

- `StatefulAlbumApp`
- `SandboxLocalFileSystem`
- Email image-attachment patterns
- `Files.display`
- `StatefulAlbumApp.view_photo`

### Reference Selection
When multimodal mode is enabled, generator prompts should read 1-2 reference scenarios from `pare/scenarios/multimodal_benchmark/` instead of only using `pare/scenarios/benchmark/`.

### Local AssetProvider
Add a local asset provider that:

- resolves requested assets from an asset manifest or asset directory,
- copies or references them into the generated scenario asset directory,
- exposes stable paths to Step 2 and Step 3 prompts,
- records final asset paths and ground truth in the trajectory output.

### VisualQA
Add a lightweight visual QA gate that:

- verifies required image files exist,
- verifies supported image extensions/MIME types,
- verifies manifest ground-truth fields are present,
- optionally runs a VLM check when configured.

M1 visual QA should not require a VLM by default.

### Multimodal Helpers
Restore or reimplement helper utilities currently referenced by handwritten multimodal scenarios:

- `jpeg_for_sandbox`
- `log_has_agent_image_view`

## Acceptance Criteria
- Running the generator with `--multimodal` can produce at least one runnable email-attachment scenario similar to `rice_cooker_photo_cart_suggestion`.
- Running the generator with `--multimodal` can produce at least one runnable Album scenario similar to `friend_bird_photos_album_share`.
- Generated scenarios use real image assets from a manifest and route them through `MMObservation`-capable tools.
- Validation fails if the final action succeeds without an image inspection event.
- Existing text-only generator behavior still works without `--multimodal`.

## Suggested Build Order
1. Restore or implement `jpeg_for_sandbox` and `log_has_agent_image_view`.
2. Add multimodal app/tool discovery for Album, Files, Email attachments, and image-view tools.
3. Add `--multimodal`, `--asset-manifest`, and `--asset-dir` CLI flags.
4. Add `VisualAssetSpec` / asset manifest parsing.
5. Add local `AssetProvider`.
6. Add lightweight `VisualQA`.
7. Update Step 1-4 prompts and add Step 1.5 asset-planning prompt.
8. Add multimodal reference selection from `pare/scenarios/multimodal_benchmark/`.
9. Add focused tests for manifest parsing, provider resolution, prompt context, and generated scenario dry-run behavior.
