import numpy as np
from PIL import Image, ImageDraw, ImageFont

from screenlens.appearance import apply_replacement, detect_appearance, replacement_patch
from screenlens.ocr import TextLine


def test_replacement_changes_only_glyph_neighborhood_and_new_text():
    image = Image.new("RGB",(640,180),(35,70,110))
    font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf",32)
    draw = ImageDraw.Draw(image)
    text = "HELLO SCREENLENS"
    draw.text((45,85),text,font=font,fill=(255,240,80),stroke_width=2,stroke_fill=(0,0,0))
    bounds = draw.textbbox((45,85),text,font=font,stroke_width=2)
    x,y,right,bottom = bounds
    frame = np.asarray(image)[:,:,::-1].copy()
    appearance = detect_appearance(frame,[TextLine(text,(x,y,right-x,bottom-y))])
    assert appearance.color[0] >= 220 and appearance.color[2] < 140
    box,rgba = replacement_patch(frame,appearance,"你好，视译")
    assert rgba[0,0,3] == 0
    assert np.mean(rgba[:,:,3] == 0) > .15
    result = apply_replacement(frame,appearance,"你好，视译")
    assert np.array_equal(result[:70],frame[:70])
    assert np.array_equal(result[:,500:],frame[:,500:])
    assert not np.array_equal(result,frame)
