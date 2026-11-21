"""Shared geography helpers: prefecture names, name normalisation, haversine."""
import math, re

PREFECTURES = {  # romaji -> kanji
 "Hokkaido": "北海道", "Aomori": "青森", "Iwate": "岩手", "Miyagi": "宮城", "Akita": "秋田", "Yamagata": "山形",
 "Fukushima": "福島", "Ibaraki": "茨城", "Tochigi": "栃木", "Gunma": "群馬", "Saitama": "埼玉", "Chiba": "千葉",
 "Tokyo": "東京", "Kanagawa": "神奈川", "Niigata": "新潟", "Toyama": "富山", "Ishikawa": "石川", "Fukui": "福井",
 "Yamanashi": "山梨", "Nagano": "長野", "Gifu": "岐阜", "Shizuoka": "静岡", "Aichi": "愛知", "Mie": "三重",
 "Shiga": "滋賀", "Kyoto": "京都", "Osaka": "大阪", "Hyogo": "兵庫", "Nara": "奈良", "Wakayama": "和歌山",
 "Tottori": "鳥取", "Shimane": "島根", "Okayama": "岡山", "Hiroshima": "広島", "Yamaguchi": "山口",
 "Tokushima": "徳島", "Kagawa": "香川", "Ehime": "愛媛", "Kochi": "高知", "Fukuoka": "福岡", "Saga": "佐賀",
 "Nagasaki": "長崎", "Kumamoto": "熊本", "Oita": "大分", "Miyazaki": "宮崎", "Kagoshima": "鹿児島", "Okinawa": "沖縄"}
_KANJI = {v: k for k, v in PREFECTURES.items()}
_ALT = {"hokkaidou": "Hokkaido", "hyougo": "Hyogo", "kouchi": "Kochi", "ooita": "Oita", "tokyo-to": "Tokyo",
        "kyouto": "Kyoto", "oosaka": "Osaka", "gumma": "Gunma"}

def norm_pref(s):
    """'Kanagawa Prefecture', 'Kanagawa Pref.', 'kanagawa-ken', '神奈川県' -> 'Kanagawa' (or None)."""
    if not s: return None
    t = s.strip()
    for k, v in _KANJI.items():
        if t.startswith(k): return v
    t = re.sub(r"(?i)[\s\-]*(prefecture|pref\.?|ken|fu|to|do)\s*$", "", t).strip()
    t = re.sub(r"(?i)^(hokkai)$", "hokkaido", t)
    low = t.lower().replace("ō", "o").replace("ū", "u")
    if low in _ALT: return _ALT[low]
    for k in PREFECTURES:
        if low == k.lower(): return k
    return None

def haversine_km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))

def neighbour_place(lat, lon):
    """Rough label for a neighbouring-country 1-degree cell."""
    if 21.5 <= lat <= 25.6 and 119.3 <= lon <= 122.3: return ("Taiwan", "台湾")
    if 33 <= lat <= 38.7 and 124 <= lon <= 131: return ("Korea", "朝鮮半島")
    if lat >= 42 and lon >= 130: return ("Russian Far East", "ロシア極東")
    return ("China", "中国")
