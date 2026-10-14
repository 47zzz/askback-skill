"""把句子裡殘留的阿拉伯數字與算式符號改成繁體中文讀法，送進 TTS 之前的最後一道保險。
SKILL.md 要求 agent 在 say 裡直接寫讀法（「一千五百九十八減二百六十」），這裡只處理漏網之魚。

    >>> verbalize("1598 - 260 + 302 = 1640")
    '一千五百九十八減二百六十加三百零二等於一千六百四十'
    >>> verbalize("3/4 杯、0.5 公升、25%")
    '四分之三杯、零點五公升、百分之二十五'
"""
import re

DIG = "零一二三四五六七八九"
UNIT = ["", "十", "百", "千"]
BIG = ["", "萬", "億", "兆"]


def int_zh(n: int) -> str:
    if n == 0:
        return "零"
    if n < 0:
        return "負" + int_zh(-n)
    parts, i = [], 0
    while n > 0:
        seg = n % 10000
        if seg:
            parts.append(_seg(seg, pad=(n >= 10000 and seg < 1000)) + BIG[i])
        elif parts and parts[-1] and not parts[-1].startswith("零"):
            parts.append("零")
        n //= 10000
        i += 1
    s = "".join(reversed(parts))
    s = re.sub(r"零+", "零", s).strip("零")
    if s.startswith("一十"):
        s = s[1:]
    return s


def _seg(n: int, pad: bool) -> str:
    out, zero = "", pad
    for p in range(3, -1, -1):
        d = (n // 10 ** p) % 10
        if d:
            out += ("零" if zero and out else "") + DIG[d] + UNIT[p]
            zero = False
        elif out:
            zero = True
    return out


def num_zh(s: str) -> str:
    """'1598' → 一千五百九十八；'0.5' → 零點五；'2024'(年份般的四位數) 仍讀數值。"""
    if "." in s:
        a, b = s.split(".", 1)
        return int_zh(int(a or 0)) + "點" + "".join(DIG[int(c)] for c in b)
    return int_zh(int(s))


OPS = {"+": "加", "＋": "加", "-": "減", "－": "減", "−": "減", "×": "乘", "*": "乘", "÷": "除以", "/": "除以", "=": "等於", "＝": "等於",
       "≠": "不等於", "<": "小於", ">": "大於", "≤": "小於等於", "≥": "大於等於", "%": "百分之", "√": "根號"}


def verbalize(text: str) -> str:
    t = text
    # 分數 a/b（都是整數）→ b 分之 a
    t = re.sub(r"(?<![\d.])(\d+)\s*/\s*(\d+)(?![\d.])", lambda m: f"{num_zh(m[2])}分之{num_zh(m[1])}", t)
    # 百分比
    t = re.sub(r"(\d+(?:\.\d+)?)\s*%", lambda m: "百分之" + num_zh(m[1]), t)
    # 根號（先換，數字留著給下一步判斷運算符號）
    t = re.sub(r"√\s*(\d+(?:\.\d+)?)", lambda m: "根號" + m[1], t)
    # 數字之間的運算符號
    t = re.sub(r"(?<=[\d)）])\s*([+\-−＋－×*÷=＝≠<>≤≥])\s*(?=[\d(（根])", lambda m: OPS[m[1]], t)
    t = re.sub(r"(?<![A-Za-z])(\d+(?:\.\d+)?)", lambda m: num_zh(m[1]), t)
    t = re.sub(r"\s+", "", t)
    return t


if __name__ == "__main__":
    import doctest
    doctest.testmod(verbose=False)
    for s in ["1598 - 260 + 302 = 1640", "3/4 杯、0.5 公升、25%", "10000 和 100000 和 1001", "第 2 步", "√18 ＋ √450", "2024 年", "0"]:
        print(s, "→", verbalize(s))
