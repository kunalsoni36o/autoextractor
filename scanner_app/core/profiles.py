from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReconstructionProfile:
    name: str
    quality: str
    data_type: str
    mapper: str
    mesher: str
    use_gpu: bool
    num_threads: int
    feature_max_image_size: int
    feature_max_num_features: int
    patch_match_max_image_size: int
    patch_match_samples: int
    patch_match_iterations: int
    patch_match_window_step: int
    geom_consistency: bool
    fusion_max_image_size: int
    fusion_cache_size: int
    min_registered_ratio: float


SAFE_SCAN = ReconstructionProfile(
    name="Safe Scan (Low VRAM)", quality="low", data_type="video", mapper="incremental", mesher="poisson",
    use_gpu=True, num_threads=2, feature_max_image_size=1600, feature_max_num_features=4096,
    patch_match_max_image_size=1100, patch_match_samples=8, patch_match_iterations=3,
    patch_match_window_step=2, geom_consistency=False, fusion_max_image_size=1100,
    fusion_cache_size=16, min_registered_ratio=0.5,
)
