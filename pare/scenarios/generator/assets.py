"""Asset helpers for multimodal scenario generation."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from base64 import b64decode
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SUPPORTED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp", "heic", "bmp"}
DEFAULT_OPENAI_IMAGE_MODEL = "gpt-image-2"
DEFAULT_FIREWORKS_IMAGE_MODEL = "accounts/fireworks/models/flux-1-schnell-fp8"
DEFAULT_IMAGE_GENERATION_MODEL = DEFAULT_OPENAI_IMAGE_MODEL
DEFAULT_IMAGE_GENERATION_MAX_ASSETS = 5
_FIREWORKS_IMAGE_BASE_URL = "https://api.fireworks.ai/inference/v1/workflows"

# Default layout under multimodal_benchmark for description-placeholder + exports.
_MULTIMODAL_BENCHMARK_DIR = Path(__file__).resolve().parents[1] / "multimodal_benchmark"
DEFAULT_DESCRIPTION_DIR = _MULTIMODAL_BENCHMARK_DIR / "assets" / "description"
DEFAULT_IMAGE_ASSETS_DIR = _MULTIMODAL_BENCHMARK_DIR / "assets" / "image_assets"
DEFAULT_GENERATED_SCENARIOS_DIR = _MULTIMODAL_BENCHMARK_DIR / "generated_scenarios"


def resolve_fireworks_api_key() -> str | None:
    """Resolve a Fireworks API key from the environment or FireConnect keychain."""
    env_key = os.environ.get("FIREWORKS_API_KEY")
    if env_key:
        return env_key
    try:
        fireconnect = shutil.which("fireconnect")
        if fireconnect is None:
            home_bin = Path.home() / ".local" / "bin" / "fireconnect"
            fireconnect = str(home_bin) if home_bin.exists() else None
        if fireconnect is None:
            return None
        result = subprocess.run(  # noqa: S603
            [fireconnect, "key", "export", "--stored-only"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except Exception:
        return None
    key = result.stdout.strip()
    return key or None


@dataclass(frozen=True)
class VisualAssetSpec:
    """Structured description of an image asset required by a scenario."""

    asset_id: str
    filename: str
    source_path: Path
    sandbox_path: str
    delivery: str
    ground_truth: dict[str, Any] = field(default_factory=dict)
    visual_requirements: list[str] = field(default_factory=list)
    kind: str = "photo_like"
    generation_prompt: str = ""
    requires_exact_text: bool = False

    @classmethod
    def from_dict(
        cls,
        raw: dict[str, Any],
        *,
        base_dir: Path | None = None,
        require_source_path: bool = True,
    ) -> VisualAssetSpec:
        """Create a spec from a manifest object."""
        asset_id = str(raw.get("asset_id") or "").strip()
        if not asset_id:
            raise ValueError("Asset is missing required field: asset_id")

        source_raw = raw.get("source_path")
        if not source_raw:
            if require_source_path:
                raise ValueError("Asset is missing required field: source_path")
            source_path = Path()
        else:
            source_path = Path(str(source_raw))
        if base_dir is not None and source_raw and not source_path.is_absolute():
            source_path = base_dir / source_path

        filename = str(raw.get("filename") or source_path.name or f"{asset_id}.jpg").strip()
        sandbox_path = str(raw.get("sandbox_path") or f"/{filename}").strip()
        delivery = str(raw.get("delivery") or "files_display").strip()
        if not filename:
            raise ValueError(f"Asset {asset_id} is missing a filename")
        if not sandbox_path.startswith("/"):
            sandbox_path = f"/{sandbox_path}"

        visual_requirements = raw.get("visual_requirements") or []
        if not isinstance(visual_requirements, list):
            visual_requirements = [str(visual_requirements)]
        ground_truth = raw.get("ground_truth") or {}
        if not isinstance(ground_truth, dict):
            raise TypeError(f"Asset {asset_id} ground_truth must be an object")

        return cls(
            asset_id=asset_id,
            filename=filename,
            source_path=source_path,
            sandbox_path=sandbox_path,
            delivery=delivery,
            ground_truth=ground_truth,
            visual_requirements=[str(item) for item in visual_requirements],
            kind=str(raw.get("kind") or "photo_like").strip(),
            generation_prompt=str(raw.get("generation_prompt") or "").strip(),
            requires_exact_text=bool(raw.get("requires_exact_text", False)),
        )


@dataclass(frozen=True)
class ResolvedVisualAsset(VisualAssetSpec):
    """A visual asset after it has been resolved to a concrete local file."""

    resolved_path: Path = Path()
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VisualQAResult:
    """Result of lightweight asset validation."""

    passed: bool
    errors: list[str]


class LocalAssetProvider:
    """Resolve visual assets from a local manifest into a scenario asset directory."""

    def __init__(self, *, manifest_path: str | Path, output_dir: str | Path) -> None:
        """Configure manifest and output paths."""
        self.manifest_path = Path(manifest_path)
        self.output_dir = Path(output_dir)

    def load_specs(self) -> list[VisualAssetSpec]:
        """Load visual asset specs from the configured manifest."""
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        raw_assets = data.get("assets") if isinstance(data, dict) else data
        if not isinstance(raw_assets, list):
            raise TypeError("Asset manifest must be a list or an object with an 'assets' list")
        return [
            VisualAssetSpec.from_dict(raw, base_dir=self.manifest_path.parent)
            for raw in raw_assets
            if isinstance(raw, dict)
        ]

    def resolve_assets(self) -> list[ResolvedVisualAsset]:
        """Copy all manifest assets into the output directory and return resolved specs."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        resolved: list[ResolvedVisualAsset] = []
        for spec in self.load_specs():
            if not spec.source_path.exists():
                raise FileNotFoundError(f"Asset source not found for {spec.asset_id}: {spec.source_path}")
            target = self.output_dir / spec.filename
            if spec.source_path.resolve() != target.resolve():
                shutil.copy2(spec.source_path, target)
            resolved.append(
                ResolvedVisualAsset(
                    asset_id=spec.asset_id,
                    filename=spec.filename,
                    source_path=spec.source_path,
                    sandbox_path=spec.sandbox_path,
                    delivery=spec.delivery,
                    ground_truth=spec.ground_truth,
                    visual_requirements=spec.visual_requirements,
                    kind=spec.kind,
                    generation_prompt=spec.generation_prompt,
                    requires_exact_text=spec.requires_exact_text,
                    resolved_path=target,
                    provider_metadata={"provider": "local", "source_path": str(spec.source_path)},
                )
            )
        return resolved


class DescriptionPlaceholderAssetProvider:
    """Write ChatGPT-ready photo description .txt files instead of generating images.

    Use this when image APIs are unavailable. After Step 1.5, the generator stops so
    you can paste each description into ChatGPT, save the resulting image under
    ``image_dir``, then resume with ``--asset-provider local`` and the emitted
    local manifest.
    """

    def __init__(
        self,
        *,
        description_dir: str | Path,
        image_dir: str | Path,
        max_assets: int = DEFAULT_IMAGE_GENERATION_MAX_ASSETS,
        scenario_id: str | None = None,
    ) -> None:
        """Configure description/image directories and max number of placeholder assets."""
        self.description_dir = Path(description_dir)
        self.image_dir = Path(image_dir)
        self.max_assets = max(1, max_assets)
        self.scenario_id = (scenario_id or "pending_scenario").strip() or "pending_scenario"

    def write_placeholders(self, specs: list[VisualAssetSpec]) -> dict[str, Any]:
        """Write description .txt files plus pending/local manifests; return the pending record."""
        desc_dir = self.description_dir / self.scenario_id
        image_dir = self.image_dir / self.scenario_id
        desc_dir.mkdir(parents=True, exist_ok=True)
        image_dir.mkdir(parents=True, exist_ok=True)

        selected = specs[: self.max_assets]
        if len(specs) > self.max_assets:
            skipped = [spec.asset_id for spec in specs[self.max_assets :]]
            logger.warning(
                "description-placeholder capped assets at %s; skipped: %s",
                self.max_assets,
                ", ".join(skipped),
            )

        pending_assets: list[dict[str, Any]] = []
        local_assets: list[dict[str, Any]] = []
        for spec in selected:
            image_filename = spec.filename or f"{spec.asset_id}.jpg"
            stem = Path(image_filename).stem
            description_filename = f"{stem}.txt"
            description_path = desc_dir / description_filename
            target_path = image_dir / image_filename
            chatgpt_prompt = self._chatgpt_prompt_for_spec(spec)
            description_path.write_text(chatgpt_prompt, encoding="utf-8")

            pending_assets.append({
                "asset_id": spec.asset_id,
                "description_filename": description_filename,
                "description_path": str(description_path),
                "target_filename": image_filename,
                "target_path": str(target_path),
                "sandbox_path": spec.sandbox_path,
                "delivery": spec.delivery,
                "kind": spec.kind,
                "generation_prompt": spec.generation_prompt,
                "visual_requirements": list(spec.visual_requirements),
                "ground_truth": dict(spec.ground_truth),
                "requires_exact_text": spec.requires_exact_text,
                "chatgpt_prompt": chatgpt_prompt,
                "status": "awaiting_image",
            })
            local_assets.append({
                "asset_id": spec.asset_id,
                "filename": image_filename,
                "source_path": str(target_path),
                "sandbox_path": spec.sandbox_path,
                "delivery": spec.delivery,
                "kind": spec.kind,
                "generation_prompt": spec.generation_prompt,
                "requires_exact_text": spec.requires_exact_text,
                "visual_requirements": list(spec.visual_requirements),
                "ground_truth": dict(spec.ground_truth),
            })

        pending_path = desc_dir / "pending_images.manifest.json"
        # Keep resume tooling next to descriptions; image_dir stays paste-only photos.
        local_manifest_path = desc_dir / "local_assets.manifest.json"
        drop_guide_path = image_dir / "DROP_IMAGES_HERE.txt"
        expected_filenames = [asset["target_filename"] for asset in pending_assets]
        drop_guide_path.write_text(
            (
                f"Scenario: {self.scenario_id}\n"
                "Paste ChatGPT-generated images into this folder using these exact filenames:\n"
                + "".join(f"- {name}\n" for name in expected_filenames)
                + "\n"
                "Then resume with:\n"
                f"  --asset-provider local --asset-manifest {local_manifest_path} "
                f"--asset-dir {image_dir} --resume-from-step step2\n"
            ),
            encoding="utf-8",
        )
        record = {
            "provider": "description-placeholder",
            "scenario_id": self.scenario_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "max_assets": self.max_assets,
            "description_dir": str(desc_dir),
            "image_dir": str(image_dir),
            "instructions": (
                "1) Open each *.txt description file under description_dir.\n"
                "2) Paste the ChatGPT prompt into ChatGPT image generation.\n"
                "3) Save the generated image as the matching target_filename under image_dir "
                f"(see {drop_guide_path.name}).\n"
                "4) Resume the generator with:\n"
                "   --asset-provider local "
                f"--asset-manifest {local_manifest_path} "
                f"--asset-dir {image_dir} "
                "--resume-from-step step2"
            ),
            "pending_manifest_path": str(pending_path),
            "local_manifest_path": str(local_manifest_path),
            "drop_guide_path": str(drop_guide_path),
            "assets": pending_assets,
        }
        pending_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        local_manifest_path.write_text(json.dumps({"assets": local_assets}, indent=2), encoding="utf-8")
        return record

    @staticmethod
    def _chatgpt_prompt_for_spec(spec: VisualAssetSpec) -> str:
        requirements = ", ".join(str(item) for item in spec.visual_requirements if str(item).strip())
        prompt = (spec.generation_prompt or "").strip() or (
            requirements or f"Photorealistic image for asset {spec.asset_id}"
        )
        ground_truth = json.dumps(spec.ground_truth, indent=2, default=str) if spec.ground_truth else "{}"
        return (
            f"Asset ID: {spec.asset_id}\n"
            f"Target filename: {spec.filename or f'{spec.asset_id}.jpg'}\n"
            f"Kind: {spec.kind}\n"
            f"Delivery: {spec.delivery}\n"
            f"Sandbox path: {spec.sandbox_path}\n"
            "\n"
            "=== ChatGPT image prompt (copy below) ===\n"
            f"{prompt}\n"
            "\n"
            "Constraints:\n"
            "- Photorealistic natural photograph; prefer objects/scenes over documents or forms.\n"
            "- Little or no readable text. If any text is required, keep only the essential short fields from the prompt.\n"
            "- Natural lighting, no watermarks, no brand logos unless the prompt explicitly requires them.\n"
            "- No people unless the prompt explicitly requires them.\n"
            f"- Visual requirements: {requirements or '(none)'}\n"
            "\n"
            "Ground truth (for scenario validation; do not render as on-image text):\n"
            f"{ground_truth}\n"
        )


ImageGenerationClient = Callable[..., bytes]


class OpenAIImageAssetProvider:
    """Generate photo-like visual assets with OpenAI Images API."""

    def __init__(
        self,
        *,
        output_dir: str | Path,
        image_model: str = DEFAULT_IMAGE_GENERATION_MODEL,
        image_client: ImageGenerationClient | None = None,
        max_retries: int = 1,
    ) -> None:
        """Configure output path, model, and optional fakeable image client."""
        self.output_dir = Path(output_dir)
        self.image_model = image_model
        self.image_client = image_client
        self.max_retries = max(1, max_retries)

    def resolve_assets(self, specs: list[VisualAssetSpec]) -> list[ResolvedVisualAsset]:
        """Generate all requested assets and return resolved specs."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        resolved: list[ResolvedVisualAsset] = []
        for spec in specs:
            self._validate_generatable(spec)
            target = self.output_dir / spec.filename
            image_bytes = self._generate_image_bytes(spec)
            target.write_bytes(image_bytes)
            asset = ResolvedVisualAsset(
                asset_id=spec.asset_id,
                filename=spec.filename,
                source_path=target,
                sandbox_path=spec.sandbox_path,
                delivery=spec.delivery,
                ground_truth=spec.ground_truth,
                visual_requirements=spec.visual_requirements,
                kind=spec.kind,
                generation_prompt=spec.generation_prompt,
                requires_exact_text=spec.requires_exact_text,
                resolved_path=target,
                provider_metadata={
                    "provider": "openai-image",
                    "model": self.image_model,
                    "generation_prompt": self._prompt_for_spec(spec),
                    "generated_at": datetime.now(UTC).isoformat(),
                },
            )
            self._write_asset_metadata(asset)
            resolved.append(asset)
        return resolved

    @staticmethod
    def _write_asset_metadata(asset: ResolvedVisualAsset) -> None:
        metadata_path = asset.resolved_path.with_name(f"{asset.resolved_path.name}.metadata.json")
        metadata_path.write_text(json.dumps(asdict(asset), indent=2, default=str), encoding="utf-8")

    @staticmethod
    def _validate_generatable(spec: VisualAssetSpec) -> None:
        if spec.requires_exact_text:
            raise ValueError(f"{spec.asset_id}: exact text assets should use local/deterministic assets")
        if spec.kind not in {"photo_like", "product_photo", "object_photo"}:
            raise ValueError(f"{spec.asset_id}: openai-image supports photo-like assets only, got {spec.kind!r}")

    def _generate_image_bytes(self, spec: VisualAssetSpec) -> bytes:
        prompt = self._prompt_for_spec(spec)
        last_error: Exception | None = None
        for _attempt in range(self.max_retries):
            try:
                if self.image_client is not None:
                    return self.image_client(prompt=prompt, model=self.image_model)
                return self._call_openai_images_api(prompt=prompt)
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"{spec.asset_id}: image generation failed without an exception")

    @staticmethod
    def _prompt_for_spec(spec: VisualAssetSpec) -> str:
        if spec.generation_prompt:
            return spec.generation_prompt
        requirements = ", ".join(spec.visual_requirements)
        return requirements or f"Photo-like image for asset {spec.asset_id}"

    def _call_openai_images_api(self, *, prompt: str) -> bytes:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - dependency exists in normal env
            raise RuntimeError("OpenAI image generation requires the openai package") from exc

        client = OpenAI()
        response = client.images.generate(model=self.image_model, prompt=prompt, size="1024x1024")
        first = response.data[0]
        b64_json = getattr(first, "b64_json", None)
        if b64_json:
            return b64decode(b64_json)
        url = getattr(first, "url", None)
        if url:
            raise RuntimeError("OpenAI image response returned a URL; configure the client for base64 image data")
        raise RuntimeError("OpenAI image response did not include image bytes")


class FireworksImageAssetProvider:
    """Generate photo-like visual assets with Fireworks FLUX workflows."""

    def __init__(
        self,
        *,
        output_dir: str | Path,
        image_model: str = DEFAULT_FIREWORKS_IMAGE_MODEL,
        image_client: ImageGenerationClient | None = None,
        max_retries: int = 1,
        api_key: str | None = None,
    ) -> None:
        """Configure output path, model, and optional fakeable image client."""
        self.output_dir = Path(output_dir)
        self.image_model = self._normalize_model_id(image_model)
        self.image_client = image_client
        self.max_retries = max(1, max_retries)
        self.api_key = api_key

    def resolve_assets(self, specs: list[VisualAssetSpec]) -> list[ResolvedVisualAsset]:
        """Generate all requested assets and return resolved specs."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        resolved: list[ResolvedVisualAsset] = []
        for spec in specs:
            self._validate_generatable(spec)
            target = self.output_dir / spec.filename
            image_bytes = self._generate_image_bytes(spec)
            target.write_bytes(image_bytes)
            asset = ResolvedVisualAsset(
                asset_id=spec.asset_id,
                filename=spec.filename,
                source_path=target,
                sandbox_path=spec.sandbox_path,
                delivery=spec.delivery,
                ground_truth=spec.ground_truth,
                visual_requirements=spec.visual_requirements,
                kind=spec.kind,
                generation_prompt=spec.generation_prompt,
                requires_exact_text=spec.requires_exact_text,
                resolved_path=target,
                provider_metadata={
                    "provider": "fireworks-image",
                    "model": self.image_model,
                    "generation_prompt": self._prompt_for_spec(spec),
                    "generated_at": datetime.now(UTC).isoformat(),
                },
            )
            self._write_asset_metadata(asset)
            resolved.append(asset)
        return resolved

    @staticmethod
    def _write_asset_metadata(asset: ResolvedVisualAsset) -> None:
        metadata_path = asset.resolved_path.with_name(f"{asset.resolved_path.name}.metadata.json")
        metadata_path.write_text(json.dumps(asdict(asset), indent=2, default=str), encoding="utf-8")

    @staticmethod
    def _validate_generatable(spec: VisualAssetSpec) -> None:
        if spec.requires_exact_text:
            raise ValueError(f"{spec.asset_id}: exact text assets should use local/deterministic assets")
        if spec.kind not in {"photo_like", "product_photo", "object_photo"}:
            raise ValueError(f"{spec.asset_id}: fireworks-image supports photo-like assets only, got {spec.kind!r}")

    @staticmethod
    def _normalize_model_id(image_model: str) -> str:
        model = image_model.strip()
        if model.startswith("accounts/"):
            return model
        return f"accounts/fireworks/models/{model}"

    def _generate_image_bytes(self, spec: VisualAssetSpec) -> bytes:
        prompt = self._prompt_for_spec(spec)
        last_error: Exception | None = None
        for _attempt in range(self.max_retries):
            try:
                if self.image_client is not None:
                    return self.image_client(prompt=prompt, model=self.image_model)
                return self._call_fireworks_flux_api(prompt=prompt)
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"{spec.asset_id}: image generation failed without an exception")

    @staticmethod
    def _prompt_for_spec(spec: VisualAssetSpec) -> str:
        if spec.generation_prompt:
            return spec.generation_prompt
        requirements = ", ".join(spec.visual_requirements)
        return requirements or f"Photo-like image for asset {spec.asset_id}"

    def _call_fireworks_flux_api(self, *, prompt: str) -> bytes:
        import httpx

        api_key = self.api_key or resolve_fireworks_api_key()
        if not api_key:
            raise RuntimeError("Fireworks image generation requires FIREWORKS_API_KEY")

        url = f"{_FIREWORKS_IMAGE_BASE_URL}/{self.image_model}/text_to_image"
        response = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "image/jpeg",
            },
            json={"prompt": prompt, "aspect_ratio": "1:1"},
            timeout=120.0,
        )
        if response.status_code == 401:
            raise RuntimeError(
                "Fireworks rejected image generation with 401 Unauthorized. "
                "This Fireworks key can authenticate, but the account may not have "
                "access to FLUX image models. Confirm flux-1-schnell-fp8 is enabled "
                "for the account at https://fireworks.ai, or set FIREWORKS_API_KEY "
                "to an API key that can call the text_to_image workflow."
            )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            payload = response.json()
            b64_json = payload.get("image") or payload.get("b64_json")
            if b64_json:
                return b64decode(b64_json)
            raise RuntimeError("Fireworks image response JSON did not include image bytes")
        if not response.content:
            raise RuntimeError("Fireworks image response did not include image bytes")
        return response.content


class VisualQA:
    """Lightweight deterministic checks for resolved visual assets."""

    def check(self, assets: list[ResolvedVisualAsset]) -> VisualQAResult:
        """Validate files, supported extensions, and ground-truth metadata."""
        errors: list[str] = []
        for asset in assets:
            if not asset.resolved_path.exists():
                errors.append(f"{asset.asset_id}: resolved file does not exist: {asset.resolved_path}")
                continue
            ext = asset.resolved_path.suffix.lower().lstrip(".")
            if ext not in _SUPPORTED_IMAGE_EXTENSIONS:
                errors.append(f"{asset.asset_id}: unsupported image extension: {asset.resolved_path.suffix}")
            if not asset.ground_truth:
                errors.append(f"{asset.asset_id}: ground_truth is required")
            if not asset.sandbox_path.startswith("/"):
                errors.append(f"{asset.asset_id}: sandbox_path must be absolute")
        return VisualQAResult(passed=not errors, errors=errors)
