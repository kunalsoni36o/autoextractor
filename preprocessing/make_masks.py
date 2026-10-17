import cv2
import os
import glob
import shutil
import numpy as np

# =====================  SETTINGS (tune these)  =====================
MASK_ONLY = False       # True  = keep original photos untouched, only write masks (recommended)
                       # False = also inpaint the highlights into out/ images

V_MIN = 248            # pixel must be at least this bright (0-255)
S_MAX = 40             # ...and at most this colorful (0-255); sparkles are near-white
D_MIN = 60             # ...and this much brighter than its local surroundings
MIN_AREA = 4           # ignore specks smaller than this many pixels
MAX_AREA_FRAC = 0.0005 # ignore blobs bigger than this fraction of the image
GROW = 1               # expand the mask a little to cover soft edges
FEATHER = 1            # softness of the blend edge (only used when MASK_ONLY = False)

SHOW_PREVIEW = True    # show original vs selected-highlights for the first image
# ====================================================================

# Project root is one level above this script (preprocessing/)
root      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
in_dir    = os.path.join(root, "data", "raw")    # input  : data/raw/
out_dir   = os.path.join(root, "data", "out")    # output : data/out/   (processed photos)
mask_dir  = os.path.join(root, "data", "masks")  # output : data/masks/ (photogrammetry masks)
debug_dir = os.path.join(root, "data", "debug")  # output : data/debug/ (QC overlays)
for d in (out_dir, mask_dir, debug_dir):
    os.makedirs(d, exist_ok=True)

files = sorted(
    f for f in glob.glob(os.path.join(in_dir, "*"))
    if f.lower().endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff"))
)
if not files:
    raise SystemExit(f"No images found in {in_dir}")

print(f"Found {len(files)} image(s)")
preview = None

for i, path in enumerate(files, 1):
    img = cv2.imread(path)
    if img is None:
        print(f"[{i}/{len(files)}] could not read {path}, skipping")
        continue

    # 1. candidate highlights: very bright, nearly colorless,
    #    and clearly brighter than the area around them
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    s, v = hsv[..., 1], hsv[..., 2]
    local = cv2.GaussianBlur(v, (0, 0), 25)
    diff = v.astype(np.int16) - local.astype(np.int16)
    spot = ((v >= V_MIN) & (s <= S_MAX) & (diff >= D_MIN)).astype(np.uint8)

    # 2. keep only small/medium blobs (drop single-pixel noise and huge bright areas)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(spot)
    areas = stats[:, cv2.CC_STAT_AREA]
    max_area = MAX_AREA_FRAC * spot.size
    keep = np.zeros(n, bool)
    keep[1:] = (areas[1:] >= MIN_AREA) & (areas[1:] <= max_area)
    mask = (keep[labels] * 255).astype(np.uint8)

    # 3. grow the mask slightly
    if GROW > 0:
        mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=GROW)

    fname = os.path.basename(path)
    stem = os.path.splitext(fname)[0]

    # 4. output image
    if MASK_ONLY:
        # copy the original file as-is (keeps EXIF and quality)
        shutil.copy2(path, os.path.join(out_dir, fname))
    else:
        filled = cv2.inpaint(img, mask, 3, cv2.INPAINT_TELEA)
        alpha = cv2.GaussianBlur(mask.astype(np.float32) / 255, (0, 0), FEATHER)
        alpha = np.clip(alpha * 1.5, 0, 1)[..., None]
        result = (img * (1 - alpha) + filled * alpha).astype(np.uint8)
        cv2.imwrite(os.path.join(out_dir, fname), result, [cv2.IMWRITE_JPEG_QUALITY, 98])

    # 5. mask file for photogrammetry software (white = keep, black = ignore)
    #    COLMAP expects: <image_filename>.png  e.g. photo.jpg -> photo.jpg.png
    cv2.imwrite(os.path.join(mask_dir, fname + ".png"), 255 - mask)

    # 6. debug image: selected pixels painted red, so you can check the detection
    overlay = img.copy()
    overlay[mask > 0] = (0, 0, 255)
    cv2.imwrite(os.path.join(debug_dir, stem + "_debug.png"), overlay)

    percent = 100 * np.count_nonzero(mask) / mask.size
    print(f"[{i}/{len(files)}] {fname}  ({percent:.2f}% masked)")

    if preview is None:
        preview = (img, overlay)

print()
print("Output images :", out_dir)
print("Masks         :", mask_dir)
print("Debug overlays:", debug_dir)

if SHOW_PREVIEW and preview is not None:
    before, after = preview
    scale = min(1.0, 600 / before.shape[1])
    pair = np.hstack([
        cv2.resize(before, None, fx=scale, fy=scale),
        cv2.resize(after, None, fx=scale, fy=scale),
    ])
    cv2.imshow("original (left) / selected highlights in red (right) - press any key", pair)
    cv2.waitKey(0)
    cv2.destroyAllWindows()