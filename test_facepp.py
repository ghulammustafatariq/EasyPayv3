"""
Standalone Face++ API test.
Usage:
    python test_facepp.py --selfie path/to/selfie.jpg --cnic path/to/cnic_front.jpg

If you want to test against the actual Cloudinary CNIC URL stored for a user,
set --cnic-url instead of --cnic.

Runs TWO comparisons so you can see the difference:
  1. Original CNIC bytes (before fix)
  2. Cropped + upscaled CNIC face region (after fix)
"""
import argparse
import base64
import io
import httpx
import os
import sys

from PIL import Image, ExifTags
from dotenv import load_dotenv

load_dotenv()

API_KEY    = os.getenv("FACEPLUSPLUS_API_KEY", "")
API_SECRET = os.getenv("FACEPLUSPLUS_API_SECRET", "")
BASE_URL   = os.getenv("FACEPLUSPLUS_BASE_URL", "https://api-us.faceplusplus.com")


MIN_FACE_SIZE = 400
PADDING_FACTOR = 0.15


def extract_and_upscale_cnic_face(cnic_bytes: bytes) -> str:
    img = Image.open(io.BytesIO(cnic_bytes)).convert("RGB")

    # Auto-rotate via EXIF
    try:
        exif = img._getexif()
        if exif:
            for tag, val in exif.items():
                if ExifTags.TAGS.get(tag) == "Orientation":
                    rotations = {3: 180, 6: 270, 8: 90}
                    if val in rotations:
                        img = img.rotate(rotations[val], expand=True)
                    break
    except Exception:
        pass

    w, h = img.size
    # Ensure landscape
    if h > w:
        img = img.rotate(90, expand=True)
        w, h = img.size

    print(f"  CNIC size after orient: {w}×{h}")

    # Crop passport photo region (top-right quadrant of CNIC)
    pad_x = int(w * PADDING_FACTOR * 0.5)
    pad_y = int(h * PADDING_FACTOR * 0.5)
    x1 = max(0, int(w * 0.60) - pad_x)
    y1 = max(0, int(h * 0.08) - pad_y)
    x2 = min(w, int(w * 0.88) + pad_x)
    y2 = min(h, int(h * 0.85) + pad_y)
    crop = img.crop((x1, y1, x2, y2))
    cw, ch = crop.size
    print(f"  Crop region: ({x1},{y1})→({x2},{y2})  size={cw}×{ch}")

    scale = max(MIN_FACE_SIZE / cw, MIN_FACE_SIZE / ch, 1.0)
    new_w = int(cw * scale)
    new_h = int(ch * scale)
    crop = crop.resize((new_w, new_h), Image.LANCZOS)
    print(f"  Upscaled to: {new_w}×{new_h}")

    buf = io.BytesIO()
    crop.save(buf, format="JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode()


def encode_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def compare(client: httpx.Client, selfie_b64: str, cnic_b64: str, label: str) -> dict:
    print(f"\n--- {label} ---")
    print(f"  CNIC b64 length: {len(cnic_b64)} chars")
    resp = client.post(
        f"{BASE_URL}/facepp/v3/compare",
        data={
            "api_key":        API_KEY,
            "api_secret":     API_SECRET,
            "image_base64_1": selfie_b64,
            "image_base64_2": cnic_b64,
        },
    )
    print(f"  HTTP status: {resp.status_code}")
    payload = resp.json()

    import json
    print(json.dumps(payload, indent=2))

    if payload.get("faces2") == [] or "error_message" in payload:
        print("  RESULT: Face++ could NOT detect face in CNIC ✗")
    else:
        conf = float(payload.get("confidence", 0.0))
        matched = conf > 30.0
        print(f"  RESULT: confidence={conf:.2f}  matched={'YES ✓' if matched else 'NO ✗'}")
    return payload


def main():
    parser = argparse.ArgumentParser(description="Test Face++ /compare endpoint")
    parser.add_argument("--selfie", required=True, help="Path to selfie image")
    parser.add_argument("--cnic", help="Path to CNIC front image")
    parser.add_argument("--cnic-url", help="Cloudinary URL for CNIC front")
    args = parser.parse_args()

    if not API_KEY or not API_SECRET:
        print("ERROR: FACEPLUSPLUS_API_KEY or FACEPLUSPLUS_API_SECRET not set in .env")
        sys.exit(1)

    print(f"API key loaded: {API_KEY[:8]}...")
    print(f"Endpoint: {BASE_URL}/facepp/v3/compare")

    selfie_b64 = encode_image(args.selfie)
    print(f"\nSelfie: {args.selfie}  ({len(selfie_b64)} chars)")

    if args.cnic:
        with open(args.cnic, "rb") as f:
            cnic_bytes = f.read()
    elif args.cnic_url:
        print(f"Downloading CNIC from: {args.cnic_url}")
        cnic_bytes = httpx.get(args.cnic_url).content
    else:
        print("ERROR: Provide --cnic or --cnic-url")
        sys.exit(1)

    cnic_b64_original = base64.b64encode(cnic_bytes).decode()

    print("\nPreparing upscaled crop...")
    cnic_b64_cropped = extract_and_upscale_cnic_face(cnic_bytes)

    with httpx.Client(timeout=60.0) as client:
        compare(client, selfie_b64, cnic_b64_original, "BEFORE FIX — original CNIC bytes")
        compare(client, selfie_b64, cnic_b64_cropped,  "AFTER FIX  — cropped + upscaled face")


if __name__ == "__main__":
    main()
