"""Tests for pare.scenarios.generator.assets."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pare.scenarios.multimodal_benchmark.lib.jpeg_for_sandbox import jpeg_bytes_for_sandbox

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch


def test_local_asset_provider_resolves_manifest_and_visual_qa(tmp_path: Path) -> None:
    """Local assets should resolve from manifest into stable scenario asset paths."""
    from pare.scenarios.generator.assets import LocalAssetProvider, VisualQA

    source = tmp_path / "source.jpg"
    source.write_bytes(jpeg_bytes_for_sandbox(b"not-really-a-jpeg"))
    manifest_path = tmp_path / "assets.json"
    manifest_path.write_text(
        json.dumps({
            "assets": [
                {
                    "asset_id": "rice_cooker_photo",
                    "filename": "rice_cooker.jpg",
                    "source_path": str(source),
                    "sandbox_path": "/photo.jpg",
                    "delivery": "email_attachment",
                    "ground_truth": {"object": "rice cooker"},
                }
            ]
        }),
        encoding="utf-8",
    )

    provider = LocalAssetProvider(manifest_path=manifest_path, output_dir=tmp_path / "out")
    resolved = provider.resolve_assets()

    assert len(resolved) == 1
    assert resolved[0].asset_id == "rice_cooker_photo"
    assert resolved[0].resolved_path.exists()
    assert resolved[0].sandbox_path == "/photo.jpg"

    result = VisualQA().check(resolved)
    assert result.passed is True
    assert result.errors == []


def test_openai_image_provider_uses_default_model_and_fake_client(tmp_path: Path) -> None:
    """OpenAI image generation should be selectable without network calls in tests."""
    from pare.scenarios.generator.assets import (
        DEFAULT_IMAGE_GENERATION_MODEL,
        OpenAIImageAssetProvider,
        VisualAssetSpec,
        VisualQA,
    )

    calls: list[tuple[str, str]] = []

    def fake_image_client(*, prompt: str, model: str) -> bytes:
        calls.append((prompt, model))
        return b"\xff\xd8generated rice cooker\xff\xd9"

    spec = VisualAssetSpec.from_dict(
        {
            "asset_id": "rice_cooker_photo",
            "filename": "rice_cooker.jpg",
            "sandbox_path": "/photo.jpg",
            "delivery": "email_attachment",
            "kind": "photo_like",
            "generation_prompt": "A compact white rice cooker on a kitchen counter.",
            "visual_requirements": ["compact white rice cooker"],
            "ground_truth": {"object": "rice cooker"},
        },
        require_source_path=False,
    )
    provider = OpenAIImageAssetProvider(output_dir=tmp_path / "generated", image_client=fake_image_client)

    resolved = provider.resolve_assets([spec])

    assert calls == [("A compact white rice cooker on a kitchen counter.", DEFAULT_IMAGE_GENERATION_MODEL)]
    assert resolved[0].resolved_path.read_bytes() == b"\xff\xd8generated rice cooker\xff\xd9"
    assert resolved[0].source_path == resolved[0].resolved_path
    assert resolved[0].provider_metadata["provider"] == "openai-image"
    assert resolved[0].provider_metadata["model"] == DEFAULT_IMAGE_GENERATION_MODEL
    metadata_path = tmp_path / "generated" / "rice_cooker.jpg.metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["asset_id"] == "rice_cooker_photo"
    assert metadata["provider_metadata"]["model"] == DEFAULT_IMAGE_GENERATION_MODEL
    assert VisualQA().check(resolved).passed is True


def test_openai_image_provider_rejects_exact_text_assets(tmp_path: Path) -> None:
    """Free-form image generation should not be used for exact text assets."""
    from pare.scenarios.generator.assets import OpenAIImageAssetProvider, VisualAssetSpec

    spec = VisualAssetSpec.from_dict(
        {
            "asset_id": "bill_screenshot",
            "filename": "bill.jpg",
            "sandbox_path": "/bill.jpg",
            "delivery": "email_attachment",
            "kind": "document_like",
            "generation_prompt": "A utility bill showing exact amount $49.90.",
            "requires_exact_text": True,
            "ground_truth": {"amount": "$49.90"},
        },
        require_source_path=False,
    )
    provider = OpenAIImageAssetProvider(
        output_dir=tmp_path / "generated",
        image_client=lambda *, prompt, model: b"\xff\xd8bad\xff\xd9",
    )

    try:
        provider.resolve_assets([spec])
    except ValueError as exc:
        assert "exact text" in str(exc)
    else:
        raise AssertionError("Expected exact-text assets to be rejected")


def test_asset_provider_args_validate_openai_key(monkeypatch: MonkeyPatch) -> None:
    """OpenAI image mode should fail early without OPENAI_API_KEY."""
    from argparse import Namespace

    from pare.scenarios.generator.scenario_generator import validate_asset_provider_args

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    try:
        validate_asset_provider_args(
            Namespace(asset_provider="openai-image", asset_manifest_path=None, image_model="gpt-image-2")
        )
    except ValueError as exc:
        assert "OPENAI_API_KEY" in str(exc)
    else:
        raise AssertionError("Expected missing OPENAI_API_KEY to fail validation")


def test_fireworks_image_provider_uses_default_model_and_fake_client(tmp_path: Path) -> None:
    """Fireworks FLUX generation should be selectable without network calls in tests."""
    from pare.scenarios.generator.assets import (
        DEFAULT_FIREWORKS_IMAGE_MODEL,
        FireworksImageAssetProvider,
        VisualAssetSpec,
        VisualQA,
    )

    calls: list[tuple[str, str]] = []

    def fake_image_client(*, prompt: str, model: str) -> bytes:
        calls.append((prompt, model))
        return b"\xff\xd8generated garden terrace\xff\xd9"

    spec = VisualAssetSpec.from_dict(
        {
            "asset_id": "garden_terrace_reference",
            "filename": "garden_terrace_reference.jpg",
            "sandbox_path": "/reference/garden_terrace_reference.jpg",
            "delivery": "album_photo",
            "kind": "photo_like",
            "generation_prompt": "A sunny garden terrace venue with outdoor seating.",
            "visual_requirements": ["garden terrace"],
            "ground_truth": {"scene": "garden terrace"},
        },
        require_source_path=False,
    )
    provider = FireworksImageAssetProvider(output_dir=tmp_path / "generated", image_client=fake_image_client)

    resolved = provider.resolve_assets([spec])

    assert calls == [("A sunny garden terrace venue with outdoor seating.", DEFAULT_FIREWORKS_IMAGE_MODEL)]
    assert resolved[0].provider_metadata["provider"] == "fireworks-image"
    assert resolved[0].provider_metadata["model"] == DEFAULT_FIREWORKS_IMAGE_MODEL
    assert resolved[0].resolved_path.read_bytes().startswith(b"\xff\xd8")
    assert VisualQA().check(resolved).passed is True


def test_asset_provider_args_validate_fireworks_key(monkeypatch: MonkeyPatch) -> None:
    """Fireworks image mode should fail early without a resolvable Fireworks API key."""
    from argparse import Namespace

    from pare.scenarios.generator import scenario_generator as generator_module

    monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
    monkeypatch.setattr(generator_module, "resolve_fireworks_api_key", lambda: None)

    try:
        generator_module.validate_asset_provider_args(
            Namespace(
                asset_provider="fireworks-image",
                asset_manifest_path=None,
                image_model="accounts/fireworks/models/flux-1-schnell-fp8",
            )
        )
    except ValueError as exc:
        assert "FIREWORKS_API_KEY" in str(exc)
    else:
        raise AssertionError("Expected missing Fireworks API key to fail validation")


def test_description_placeholder_provider_writes_txt_and_manifests(tmp_path: Path) -> None:
    """Placeholder mode should emit ChatGPT-ready .txt files and resume manifests."""
    from pare.scenarios.generator.assets import DescriptionPlaceholderAssetProvider, VisualAssetSpec

    specs = [
        VisualAssetSpec.from_dict(
            {
                "asset_id": f"photo_{idx}",
                "filename": f"IMG_{idx}.jpg",
                "sandbox_path": f"/photos/IMG_{idx}.jpg",
                "delivery": "album_photo",
                "kind": "photo_like",
                "generation_prompt": f"Indoor dining photo number {idx}.",
                "visual_requirements": ["indoor dining tables"],
                "ground_truth": {"indoor": True, "index": idx},
            },
            require_source_path=False,
        )
        for idx in range(6)
    ]
    provider = DescriptionPlaceholderAssetProvider(
        description_dir=tmp_path / "description",
        image_dir=tmp_path / "image_assets",
        max_assets=5,
        scenario_id="bob_indoor_test",
    )
    record = provider.write_placeholders(specs)

    assert record["provider"] == "description-placeholder"
    assert len(record["assets"]) == 5
    assert (tmp_path / "description" / "bob_indoor_test" / "IMG_0.txt").exists()
    assert "Indoor dining photo number 0." in (
        tmp_path / "description" / "bob_indoor_test" / "IMG_0.txt"
    ).read_text(encoding="utf-8")
    assert not (tmp_path / "description" / "bob_indoor_test" / "IMG_5.txt").exists()

    image_scenario_dir = tmp_path / "image_assets" / "bob_indoor_test"
    assert image_scenario_dir.is_dir()
    drop_guide = image_scenario_dir / "DROP_IMAGES_HERE.txt"
    assert drop_guide.exists()
    assert "IMG_0.jpg" in drop_guide.read_text(encoding="utf-8")
    assert not (image_scenario_dir / "local_assets.manifest.json").exists()

    pending_path = tmp_path / "description" / "bob_indoor_test" / "pending_images.manifest.json"
    local_path = tmp_path / "description" / "bob_indoor_test" / "local_assets.manifest.json"
    assert pending_path.exists()
    assert local_path.exists()
    local_manifest = json.loads(local_path.read_text(encoding="utf-8"))
    assert len(local_manifest["assets"]) == 5
    assert local_manifest["assets"][0]["source_path"].endswith("IMG_0.jpg")
    assert "/image_assets/bob_indoor_test/" in local_manifest["assets"][0]["source_path"].replace("\\", "/")


def test_description_placeholder_provider_args_do_not_require_api_key() -> None:
    """description-placeholder should validate without image API credentials."""
    from argparse import Namespace

    from pare.scenarios.generator.scenario_generator import validate_asset_provider_args

    validate_asset_provider_args(
        Namespace(
            asset_provider="description-placeholder",
            asset_manifest_path=None,
            image_model=None,
        )
    )
