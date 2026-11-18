"""Fixed-size, single-line subtitle pages using Qt's Unicode shaping and breaks."""
from PySide6.QtCore import QPointF
from PySide6.QtGui import QFont, QTextLayout, QTextOption


def subtitle_font(size, family="Microsoft YaHei UI"):
    font = QFont(family)
    font.setPixelSize(size)
    font.setWeight(QFont.Weight.DemiBold)
    return font


def paginate_line(text, width, font):
    text = " ".join(text.split())
    if not text:
        return []
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout = QTextLayout(text, font)
    layout.setTextOption(option)
    # Qt text offsets count UTF-16 code units; Python offsets count code points.
    encoded = text.encode("utf-16-le")
    pages = []
    layout.beginLayout()
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(max(1, width))
        start, end = line.textStart(), line.textStart()+line.textLength()
        page = encoded[start*2:end*2].decode("utf-16-le").strip()
        if page:
            pages.append(page)
    layout.endLayout()
    return pages


def single_line_layout(text, width, font):
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.NoWrap)
    layout = QTextLayout(text, font)
    layout.setTextOption(option)
    layout.beginLayout()
    line = layout.createLine()
    if line.isValid():
        line.setLineWidth(max(1, width))
        line.setPosition(QPointF(0, 0))
    layout.endLayout()
    return layout


def page_read_ms(text, minimum_ms):
    # One pass through the pages, then hold the final page until the next cue.
    return max(minimum_ms, min(8000, max(1200, 350+len(text)*100)))
