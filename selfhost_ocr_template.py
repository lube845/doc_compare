from fastapi import FastAPI
import base64, io
from PIL import Image
import pytesseract
from pytesseract import Output

app = FastAPI()

def _line_box(d, i):
    """把 tesseract 的 4 点框换算成 left/top/width/height。"""
    xs = [d["left"][i] + p[0] for p in d["boxes"][i]]
    ys = [d["top"][i] + p[1] for p in d["boxes"][i]]
    left, top = min(xs), min(ys)
    return left, top, max(xs) - left, max(ys) - top

@app.post("/ocr")
def ocr(payload: dict):
    raw = payload.get("image") or ""
    if not raw:
        # 「测试连接」会发一张空图过来，这里必须正常返回 200
        return {"code": 0, "msg": "ok", "data": {"lines": []}}
    img = Image.open(io.BytesIO(base64.b64decode(raw))).convert("L")
    d = pytesseract.image_to_data(img, lang="chi_sim", output_type=Output.DICT)

    lines, cur, cur_idx = [], [], None
    for i, txt in enumerate(d["text"]):
        if not txt.strip():
            continue
        block, par, line = d["block_num"][i], d["par_num"][i], d["line_num"][i]
        key = (block, par, line)
        if cur_idx is not None and key != cur_idx:
            lines.append(cur); cur = []
        cur_idx = key
        left, top, w, h = _line_box(d, i)
        cur.append({"text": txt, "left": left, "top": top,
                    "width": w, "height": h, "confidence": d["conf"][i]})
    if cur:
        lines.append(cur)

    # 逐行合并成文本行；字符级坐标放进 chars
    result = []
    for line in lines:
        left = min(c["left"] for c in line); top = min(c["top"] for c in line)
        result.append({
            "text": "".join(c["text"] for c in line),
            "left": left, "top": top,
            "width": max(c["left"] + c["width"] for c in line) - left,
            "height": max(c["top"] + c["height"] for c in line) - top,
            "confidence": sum(c["confidence"] for c in line) / len(line) / 100,
            "chars": [{"ch": c["text"], "left": c["left"], "top": c["top"],
                       "width": c["width"], "height": c["height"]} for c in line],
        })
    return {"code": 0, "msg": "ok", "data": {"lines": result}}