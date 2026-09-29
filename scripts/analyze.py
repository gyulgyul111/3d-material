"""CLI 분석 / ROI 점검 도구.

  ROI 보정 결과만 저장(모델 불필요):  python scripts/analyze.py 사진.jpg RACK-A01 --rectify-only
  분석:                                python scripts/analyze.py 사진.jpg RACK-A01
"""

import argparse
import base64
import io
import json
import sys
from pathlib import Path

from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_config  # noqa: E402
from app.pipeline import Analyzer  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("rack_id")
    ap.add_argument("--rectify-only", action="store_true", help="ROI 로 원근 보정한 이미지만 저장")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    analyzer = Analyzer(load_config())
    img = ImageOps.exif_transpose(Image.open(args.image)).convert("RGB")
    out = args.out or args.image.with_name(f"{args.image.stem}_result.jpg")

    if args.rectify_only:
        analyzer.rectify(img, args.rack_id).save(out)
        print(f"저장: {out}")
        return

    result = analyzer.analyze(img, args.rack_id)
    data = result.pop("annotated_image").split(",", 1)[1]
    Image.open(io.BytesIO(base64.b64decode(data))).save(out)
    print(json.dumps({k: result[k] for k in ("classes", "purchase")}, ensure_ascii=False, indent=2))
    print(f"결과 이미지: {out}")


if __name__ == "__main__":
    main()
