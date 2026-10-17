"""
run_colmap.py
-------------
Automates the full COLMAP photogrammetry pipeline for turntable scans.

Pipeline stages:
  1. feature_extractor   – detect and describe SIFT keypoints
  2. sequential_matcher  – match consecutive frames (good for turntable video/sequences)
  3. mapper              – sparse SfM reconstruction
  4. image_undistorter   – prepare for dense MVS
  5. patch_match_stereo  – dense depth maps  (GPU, skipped on CPU-only machines)
  6. stereo_fusion       – fuse depths into a dense point cloud (.ply)

Usage:
  python reconstruction/run_colmap.py [--colmap PATH] [--cpu]

Options:
  --colmap PATH   Path to the colmap executable (default: 'colmap', must be on PATH)
  --cpu           Skip patch_match_stereo (no GPU required); uses delaunay_mesher instead
  --no-masks      Ignore masks even if data/masks/ exists

Directory layout expected (relative to autoextractor/):
  data/raw/       Input photos (JPEG / PNG)
  data/masks/     Per-image masks named <image>.jpg.png  (white = keep, black = ignore)
  data/sparse/    [created] Sparse reconstruction output
  data/dense/     [created] Dense reconstruction output
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (all relative to the repo root = one level above this script)
# ---------------------------------------------------------------------------
ROOT      = Path(__file__).resolve().parent.parent
IMAGE_DIR = ROOT / "data" / "raw"
MASK_DIR  = ROOT / "data" / "masks"
SPARSE    = ROOT / "data" / "sparse"
DENSE     = ROOT / "data" / "dense"
DB        = ROOT / "data" / "database.db"


def run(cmd: list[str], description: str) -> None:
    """Print and execute a command, raising on failure."""
    print(f"\n{'='*60}")
    print(f"  {description}")
    print(f"  $ {' '.join(cmd)}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"\n[ERROR] Command failed with exit code {result.returncode}. Aborting.", file=sys.stderr)
        sys.exit(result.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full COLMAP pipeline.")
    parser.add_argument("--colmap", default="colmap", help="Path to colmap binary (default: colmap)")
    parser.add_argument("--cpu",      action="store_true", help="Skip GPU patch_match_stereo")
    parser.add_argument("--no-masks", action="store_true", help="Do not pass a mask path to COLMAP")
    args = parser.parse_args()

    colmap = args.colmap

    # ----- Sanity checks -----
    if not IMAGE_DIR.exists() or not any(IMAGE_DIR.iterdir()):
        sys.exit(f"[ERROR] No images found in {IMAGE_DIR}. Populate data/raw/ first.")

    use_masks = not args.no_masks and MASK_DIR.exists() and any(MASK_DIR.iterdir())
    if use_masks:
        print(f"[INFO] Masks found in {MASK_DIR}. They will be used during feature extraction.")
    else:
        print("[INFO] Running without masks.")

    # Create output dirs
    SPARSE.mkdir(parents=True, exist_ok=True)
    DENSE.mkdir(parents=True, exist_ok=True)

    # Remove stale database so we start clean
    if DB.exists():
        print(f"[INFO] Removing existing database: {DB}")
        DB.unlink()

    # -----------------------------------------------------------------------
    # 1. Feature extraction
    # -----------------------------------------------------------------------
    feat_cmd = [
        colmap, "feature_extractor",
        "--database_path", str(DB),
        "--image_path",    str(IMAGE_DIR),
        "--ImageReader.single_camera", "1",          # one camera model for all images (turntable)
        "--SiftExtraction.use_gpu", "0" if args.cpu else "1",
    ]
    if use_masks:
        feat_cmd += ["--ImageReader.mask_path", str(MASK_DIR)]

    run(feat_cmd, "Stage 1 / 6 – Feature extraction")

    # -----------------------------------------------------------------------
    # 2. Sequential matching (ideal for ordered turntable sequences)
    # -----------------------------------------------------------------------
    run([
        colmap, "sequential_matcher",
        "--database_path", str(DB),
        "--SequentialMatching.overlap", "10",        # match each frame against its 10 nearest
        "--SiftMatching.use_gpu", "0" if args.cpu else "1",
    ], "Stage 2 / 6 – Sequential matching")

    # -----------------------------------------------------------------------
    # 3. Sparse reconstruction (SfM mapper)
    # -----------------------------------------------------------------------
    run([
        colmap, "mapper",
        "--database_path",      str(DB),
        "--image_path",         str(IMAGE_DIR),
        "--output_path",        str(SPARSE),
        "--Mapper.num_threads", str(os.cpu_count() or 4),
    ], "Stage 3 / 6 – Sparse mapping (SfM)")

    # -----------------------------------------------------------------------
    # 4. Image undistortion (required before dense MVS)
    # -----------------------------------------------------------------------
    sparse_model = SPARSE / "0"
    if not sparse_model.exists():
        # Use whichever model was produced
        models = sorted(SPARSE.iterdir())
        if not models:
            sys.exit("[ERROR] Mapper produced no models. Check the sparse/ directory.")
        sparse_model = models[0]

    run([
        colmap, "image_undistorter",
        "--image_path",       str(IMAGE_DIR),
        "--input_path",       str(sparse_model),
        "--output_path",      str(DENSE),
        "--output_type",      "COLMAP",
    ], "Stage 4 / 6 – Image undistortion")

    # -----------------------------------------------------------------------
    # 5. Dense depth estimation (GPU) or skip (CPU-only mode)
    # -----------------------------------------------------------------------
    if not args.cpu:
        run([
            colmap, "patch_match_stereo",
            "--workspace_path", str(DENSE),
            "--workspace_format", "COLMAP",
            "--PatchMatchStereo.geom_consistency", "true",
        ], "Stage 5 / 6 – Patch-match stereo (dense depths, GPU)")
    else:
        print("\n[INFO] --cpu flag set: skipping patch_match_stereo.")
        print("       Dense point cloud will not be generated unless you run it manually on a GPU machine.")

    # -----------------------------------------------------------------------
    # 6. Stereo fusion → dense point cloud
    # -----------------------------------------------------------------------
    if not args.cpu:
        run([
            colmap, "stereo_fusion",
            "--workspace_path",   str(DENSE),
            "--workspace_format", "COLMAP",
            "--input_type",       "geometric",
            "--output_path",      str(DENSE / "fused.ply"),
        ], "Stage 6 / 6 – Stereo fusion (dense point cloud)")

        print(f"\n[DONE] Dense point cloud: {DENSE / 'fused.ply'}")
        print(       "       Next step: import fused.ply into MeshLab / CloudCompare to clean and export STL.")
    else:
        print("\n[INFO] Pipeline complete (sparse only). Sparse model:", sparse_model)
        print("       Run on a GPU machine with:  python reconstruction/run_colmap.py")

    print(f"\n[DONE] Sparse model : {sparse_model}")
    print(f"       Database     : {DB}")


if __name__ == "__main__":
    main()
