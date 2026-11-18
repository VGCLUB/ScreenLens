"""Select one coherent subtitle block and keep its typography stable per selection."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from statistics import median

from .appearance import clip_box, detect_appearance, glyph_mask, union_box


def normalized_label(text):
    return "".join(char for char in unicodedata.normalize("NFKC",text).casefold() if char.isalnum())


def is_windows_watermark(text):
    value = normalized_label(text)
    windows = r"win(?:dows|dos|dow)"
    patterns = (
        rf"(?:激活|啟用|啟動|启用){windows}",
        rf"(?:请|請)?(?:转到|轉到|前往)(?:设置|設定|设定|設定)(?:以|来|來)?(?:激活|啟用|启用){windows}",
        rf"activate{windows}",
        rf"(?:go)?to(?:settings|setting)toactivate{windows}",
        rf"{windows}のライセンス認証",
        rf"(?:激活|啟用){windows}(?:转到|轉到)(?:设置|設定)以(?:激活|啟用){windows}",
    )
    return any(re.fullmatch(pattern,value) for pattern in patterns)


class CaptionSelector:
    def __init__(self, settings):
        self.settings = settings
        self.anchor = None
        self.ignored = 0

    def select(self, lines, shape):
        height,width = shape[:2]
        rules = [normalized_label(value) for value in self.settings.subtitle_ignore_text.splitlines() if value.strip()]
        eligible = []
        for line in lines:
            label = normalized_label(line.text)
            ignored = self.settings.subtitle_ignore_watermarks and is_windows_watermark(line.text)
            ignored |= any(rule in label for rule in rules if rule)
            if ignored or not label or line.score < .5 or line.box[3] < 6:
                continue
            eligible.append(line)
        self.ignored = len(lines)-len(eligible)
        if not self.settings.subtitle_focus_main or not eligible:
            return eligible
        groups = []
        for line in sorted(eligible,key=lambda item:item.box[1]):
            x,y,w,h = line.box
            match = None
            for group in groups:
                gx,gy,gw,gh = union_box([item.box for item in group])
                typical = median(item.box[3] for item in group)
                if .65 <= h/typical <= 1.55 and abs((x+w/2)-(gx+gw/2)) <= max(width*.18,max(w,gw)*.45) and y <= gy+gh+typical*.8:
                    match = group
                    break
            if match is None:
                groups.append([line])
            else:
                match.append(line)
        ranked = []
        for group in groups:
            x,y,w,h = box = union_box([item.box for item in group])
            cx,cy = x+w/2,y+h/2
            typical = median(item.box[3] for item in group)
            if self.anchor:
                ax,ay,aw,ah,asize = self.anchor
                aligned_edge = min(abs(x-ax),abs(x+w-ax-aw)) <= asize*1.5
                if abs(cy-(ay+ah/2)) > max(asize*2,height*.18) or typical < asize*.6:
                    continue
                if not aligned_edge and abs(cx-(ax+aw/2)) > max(width*.22,aw*.45):
                    continue
            elif width/max(1,height) > 2.5 and (cx < width*.13 or cx > width*.87) and w < width*.45:
                continue
            center = 1-min(1,abs(cx-width/2)/(width/2))
            score = center*.5+min(1,w/width)*.2+min(1,typical/max(1,height*.3))*.2+sum(item.score for item in group)/len(group)*.1
            ranked.append((score,group))
        selected = max(ranked,key=lambda item:item[0])[1] if ranked else []
        self.ignored += len(eligible)-len(selected)
        return sorted(selected,key=lambda line:(line.box[1],line.box[0]))

    def commit(self,lines):
        if self.anchor is None and lines:
            self.anchor = (*union_box([line.box for line in lines]),median(line.box[3] for line in lines))


class LiveStyleLock:
    def __init__(self):
        self.reference = None

    def apply(self,frame,lines,settings,scale=1.0):
        if settings.subtitle_style_mode == "box":
            return None, None
        current = detect_appearance(frame,lines,settings.subtitle_font)
        if not settings.subtitle_lock_style:
            return current, glyph_mask(frame,current)
        if self.reference is None:
            size = min(current.font_px,max(8,round(settings.subtitle_font_size*scale)))
            x,y,w,h = union_box(current.boxes)
            bottom = min(frame.shape[0],y+h+max(3,round(size*.2)))
            target_h = min(bottom,max(h+8,round(size*3.8)))
            margin = min(10,max(2,round(frame.shape[1]*.015)))
            target = (margin,bottom-target_h,max(1,frame.shape[1]-margin*2),target_h)
            self.reference = replace(current,font_px=size,target_box=target,fixed_font=True)
        reference = self.reference
        current = replace(current,font_path=reference.font_path,font_family=reference.font_family,
                          font_px=reference.font_px,stroke=reference.stroke,target_box=reference.target_box,fixed_font=True)
        return current, glyph_mask(frame,current)
