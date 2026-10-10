"""On-device Chinese text recognition and friend-list navigation helpers."""
from dataclasses import dataclass
import io
import re


def clean(text):
    return re.sub(r'\s+', '', text)


@dataclass(frozen=True)
class TextBox:
    text: str
    x: float
    y: float
    w: float
    h: float
    confidence: float = 1.0

    @property
    def cx(self): return self.x + self.w / 2

    @property
    def cy(self): return self.y + self.h / 2


@dataclass(frozen=True)
class FriendRow:
    name: str
    button: TextBox
    label: TextBox


@dataclass(frozen=True)
class IconRow:
    button: TextBox
    icon: tuple


def icon_rows(boxes, height, icons):
    """Associate stealable icons with explicit visit buttons; names are unused."""
    tabs = exact(boxes, '同玩好友')
    if len(tabs) != 1:
        raise RuntimeError('未识别到唯一同玩好友列表')
    tab = tabs[0]
    buttons = sorted([b for b in exact(boxes, '拜访')
                      if tab.y + tab.h + height * .05 < b.cy < height * .88], key=lambda b: b.cy)
    viewport_top = tab.y + tab.h + height * .07
    viewport_bottom = height * .86
    rows = []
    for icon in sorted(icons, key=lambda item: item[1]):
        x, y, score = icon
        if not (tab.x < x and viewport_top + height * .015 < y < viewport_bottom - height * .015):
            continue
        if not buttons:
            raise RuntimeError('发现可偷菜标识，但未识别到拜访按钮')
        closest = min(buttons, key=lambda b: abs(b.cy - y))
        gaps = [abs(closest.cy - b.cy) for b in buttons if b != closest]
        tolerance = min(height * .03, min(gaps or [height * .12]) * .28)
        if abs(closest.cy - y) > tolerance or x >= closest.x - height * .02:
            raise RuntimeError('可偷菜标识无法对应到同一行拜访按钮')
        if not any(row.button == closest for row in rows):
            rows.append(IconRow(closest, icon))
    return rows


def recognize(frame):
    import objc
    import Vision
    from Foundation import NSData
    from PIL import Image
    stream = io.BytesIO()
    Image.fromarray(frame).save(stream, format='PNG')
    data = stream.getvalue()
    height, width = frame.shape[:2]
    with objc.autorelease_pool():
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setUsesCPUOnly_(True)
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setRecognitionLanguages_(['zh-Hans', 'en-US'])
        request.setUsesLanguageCorrection_(False)
        request.setCustomWords_(['好友', '同玩好友', '微信好友', '拜访', '仓库', '回家', '可摘'])
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
            NSData.dataWithBytes_length_(data, len(data)), {})
        ok, error = handler.performRequests_error_([request], None)
        if not ok:
            raise RuntimeError('本机文字识别失败：' + str(error))
        boxes = []
        for observation in request.results() or []:
            candidates = observation.topCandidates_(1)
            if not candidates:
                continue
            candidate = candidates[0]
            box = observation.boundingBox()
            boxes.append(TextBox(str(candidate.string()), box.origin.x * width,
                                 (1 - box.origin.y - box.size.height) * height,
                                 box.size.width * width, box.size.height * height,
                                 float(candidate.confidence())))
        return boxes


def exact(boxes, label):
    return [box for box in boxes if clean(box.text) == label and box.confidence >= .45]


def page_kind(boxes, height, farm=False):
    # List tabs are specific to the actual in-game friend dialog.
    if exact(boxes, '同玩好友') and exact(boxes, '微信好友'):
        return 'friends'
    if farm:
        return 'farm'
    if any(b.cy > .72 * height for b in exact(boxes, '仓库')) and any(
            b.cy > .72 * height for b in exact(boxes, '好友')):
        return 'home'
    return 'unknown'


def friend_rows(boxes, height):
    tabs = exact(boxes, '同玩好友')
    if not tabs:
        raise RuntimeError('未识别到同玩好友列表')
    tab = min(tabs, key=lambda b: b.y)
    buttons = sorted([b for b in exact(boxes, '拜访') if b.cy > tab.y + tab.h], key=lambda b: b.cy)
    rows = []
    for i, button in enumerate(buttons):
        gap = min([abs(button.cy - other.cy) for other in buttons if other != button] or [height * .12])
        upper = button.cy - min(gap * .48, height * .065)
        lower = button.cy + height * .006
        if button.cy > height * .88:
            # Bottom row may be clipped behind the player's footer; scroll to expose it.
            continue
        span = button.x - tab.x
        candidates = [b for b in boxes if b.confidence >= .45
                      and tab.x + span * .12 < b.x < button.x - span * .15
                      and upper < b.cy < lower
                      and (not re.fullmatch(r'\d[\d.,]*[万亿]?(?:/.*)?', clean(b.text))
                           or (tab.x + span * .2 < b.x < tab.x + span * .44
                               and abs(b.cy - (button.cy - height * .02)) < height * .025))]
        candidates = [b for b in candidates if clean(b.text) not in ('拜访', '互动', '昵称')]
        if not candidates:
            raise RuntimeError('有拜访按钮但无法读取对应好友昵称，停止避免遗漏')
        label = min(candidates, key=lambda b: abs(b.cy - (button.cy - height * .02)))
        rows.append(FriendRow(clean(label.text), button, label))
    return rows


class BottomTracker:
    """Require two unchanged nonempty row sequences after scroll to mark bottom."""
    def __init__(self):
        self.previous = None
        self.same = 0

    def update(self, signature):
        signature = tuple(signature)
        if not signature:
            raise RuntimeError('好友列表为空或识别失败，不能确认已巡查到底')
        self.same = self.same + 1 if signature == self.previous else 0
        self.previous = signature
        return self.same >= 2
