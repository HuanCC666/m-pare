"""Asset helpers for multimodal scenario generation."""

from __future__ import annotations

import json
import shutil
from base64 import b64decode
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SUPPORTED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp", "heic", "bmp"}
DEFAULT_IMAGE_GENERATION_MODEL = "gpt-image-2"


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
