"""
매주, 사람이 오늘의집/네이버 브랜드별 "의자" 카테고리 화면을 캡처해서 구글 드라이브 "경쟁사분석_캡처"
폴더에 올리면, n8n이 그 캡처를 읽어서 구글시트 "원본입력" 탭에 자동으로 기록한다. 이 스크립트는 그
데이터를 직전 주 대비 비교(신규/가격변동/리뷰증가/판매중단 의심)한 뒤 "스냅샷" 탭에 기록하고 Slack에
회사별 요약을 전송한다.

- (2026-09-29, 최신) 스크랩수·배송비 열 추가:
  "원본입력"과 "스냅샷" 탭에 `스크랩수`, `배송비별도`(Y=고객 부담[별도·유료·착불] / N=무료배송 /
  빈칸=화면에 표시 없음), `배송비`(원, 화면에 금액이 보일 때만; 무료배송은 0) 열을 추가했다.
  "스냅샷" 탭은 예전 행이 밀리지 않도록 새 열을 맨 뒤(N~P열)에 붙인다.
  비교 신호에 "스크랩 증가"(리뷰 없는 신상품의 관심도 신호)와 "배송 조건 변경"을 추가했다.
  이전 주 데이터(시트·last_snapshot.json)에는 이 값들이 "비고" 문장 안에만 들어 있으므로, 비고에서
  "스크랩 86", "배송비 10000원", "착불 22000원", "배송비 별도", "무료배송"을 찾아 채워 넣는다
  (backfill_snapshot_sheet — 빈 칸만 채우므로 여러 번 실행해도 안전). 리뷰 없이 스크랩 수만 보이던
  예전 행은 n8n v4 규칙에 맞춰 리뷰수·평점을 0으로 채운다.

- (2026-09-27 저녁) n8n v2 파이프라인과 맞춘 수정:
  1) 쿠팡은 추적 대상에서 제외됨 → Slack 요약에서 "쿠팡: 캡처 데이터 없음"이 매주 뜨지 않도록
     추적 플랫폼을 PLATFORMS = ("오늘의집", "네이버")로 한정.
  2) 예전(오늘의집 API 시절) 형식으로 저장된 data/last_snapshot.json과 비교하면 첫 실행 때
     "사라짐" 알림이 대량으로 뜨므로, 스냅샷에 형식 버전(SNAPSHOT_SCHEMA)을 붙이고 버전이 다른
     예전 스냅샷은 무시한다 → 이번 실행이 새 기준선(baseline)이 된다.
  3) "스냅샷" 탭 1행에 예전 16열 헤더(상품ID/판매상태/이미지 등)가 남아 있어서, 새 13열 헤더로
     덮어쓸 때 뒤쪽 N~P열의 옛 헤더까지 깨끗이 지운다.
  4) 날짜/수집시각을 한국 시간(Asia/Seoul) 기준으로 기록.
  - 참고: n8n이 "원본입력" 탭에 추가하는 `_sourceFileName`(원본 캡처 파일명) 열은 이 스크립트가
    열 이름으로 읽기 때문에 영향이 없다(그냥 무시됨). 처리 후 탭을 비울 때 함께 지워진다.

- (2026-09-27 전면 개편 - 캡처+OCR 방식으로 통일)
  오늘의집은 공식(비공식) 상품 데이터 API를 직접 호출하는 방식으로 전환했었는데(9/21),
  GitHub Actions에서 실행하면 오늘의집 서버가 403 Forbidden으로 차단했다(9/22). Referer/Origin
  헤더 위장, 요청 사이 지연, 403 발생 시 재시도/백오프까지 추가했지만 재검증 결과 여전히 403이
  발생했다 — 즉 헤더 문제가 아니라 GitHub Actions IP 대역 자체를 막아놓았을 가능성이 높다는 뜻이다.
  쿠팡은 애초에 Akamai Bot Manager(5중 탐지 - HTTP/TLS 핑거프린트, JS 센서, 브라우저 핑거프린팅,
  행동 패턴 분석, IP 평판)로 막혀 있어 자동화 수집이 사실상 불가능했고, 네이버는 커머스API 팀이
  "리뷰 관련 API는 가까운 시일 내 제공 계획이 없다"고 공식적으로 밝혀 애초에 API로는 리뷰수를
  가져올 방법이 없었다.
  결국 세 플랫폼 모두 "막히면 고치고, 또 막히면 또 고치는" 유지보수 비용이 계속 반복될 가능성이
  크다고 판단해서, 세 플랫폼을 하나의 방식으로 통일하기로 했다: 사람이 매주 각 플랫폼의 브랜드별
  의자 카테고리 화면을 캡처 → Claude가 그 캡처를 읽어서(OCR) 상품명/가격/리뷰수/평점을 추출 →
  구글시트 "원본입력" 탭에 정리해서 붙여넣기 → 이 스크립트가 그 탭을 읽어서 정리/비교/기록/Slack
  요약까지 처리한다.
  이제 이 스크립트는 더 이상 오늘의집 API를 직접 호출하지 않는다. 세 플랫폼 모두 "원본입력" 탭에
  이미 정리되어 들어온 데이터를 그대로 신뢰하고 처리만 한다.
- 상품 고유 ID가 없다(캡처+OCR로는 안정적인 ID를 얻을 수 없음). 그래서 주차별 비교는
  (플랫폼, 브랜드, 제품명) 조합을 키로 사용한다 — 제품명은 절대 일부만 잘라서 비교하지 않고
  전체 문자열 그대로(공백만 다듬어서) 비교한다. "1+1 듀이 식탁 의자..."와 "듀이 식탁 의자..."처럼
  거의 동일하지만 실제로는 다른 상품(1+1 구성 vs 단품)이 실제로 존재하는 걸 확인했기 때문에,
  이름을 뭉뚱그리면 서로 다른 상품을 같은 상품으로 잘못 매칭하게 된다.
  가격은 매칭 키에 넣지 않고 비교 대상 필드로만 쓴다 (그래야 가격이 바뀌어도 같은 상품으로 계속
  추적되면서 "가격변동"으로 정확히 잡힌다).
- 야외용(테라스/캠핑용 등) 상품이 실수로 캡처에 섞여 들어올 수 있어서, 기존에 쓰던
  OUTDOOR_NAME_KEYWORDS 필터를 여기서도 안전망으로 그대로 적용한다.
- "진짜 자료"는 구글시트(경쟁사 제품 스냅샷)에 상세 기록하고, Slack에는 회사별 요약 + 시트 링크만 전송.
- 처리가 끝나면 "원본입력" 탭은 비워서, 다음 주에 새로 붙여넣을 때 지난주 데이터와 섞이지 않게 한다.

필요한 환경변수:
- SLACK_WEBHOOK_URL        : Slack Incoming Webhook URL
- GOOGLE_SERVICE_ACCOUNT_JSON : 구글 서비스 계정 키(JSON) 전체 내용을 문자열로
- GOOGLE_SHEET_ID          : "경쟁사 제품 스냅샷" 구글시트의 ID (URL의 /d/ 뒤 부분)
- GOOGLE_SHEET_TAB_NAME    : (선택) 기록할 탭 이름, 기본값 "스냅샷"
- GOOGLE_SHEET_RAW_TAB_NAME: (선택) 사람이 OCR 결과를 붙여넣는 원본 탭 이름, 기본값 "원본입력"

- (2026-09-27 추가) "원본입력" 탭에서 같은 (플랫폼, 브랜드, 제품명)이 중복으로 들어오면 첫 번째
  것만 쓰고 나머지는 버린다 — 캡처 화면이 작아 여러 장으로 나눠 찍을 때 겹치는 구간에서 같은
  상품이 두 번 잡히는 문제에 대한 대응이다.
- (2026-09-27 아키텍처 결정) "캡처 → 이미지 읽기(추출) → 원본입력 탭 기록" 단계는 n8n으로
  자동화하기로 함 (구글 드라이브 폴더에 캡처를 올리면 자동 시작 → n8n이 이미지를 읽어 데이터
  추출 → 원본입력 탭에 기록). 이 스크립트(main.py)는 그 다음 단계(원본입력 → 비교/기록/Slack
  요약)만 그대로 담당한다. 자세한 설계는 프로젝트 로드맵 문서 17번 섹션 참고.

(참고: 이제 이 스크립트 자체는 웹사이트에 직접 접속하지 않으므로 봇 차단/헤더 위장 관련 코드가
전혀 없다. "수집"은 사람(캡처) + n8n(이미지 읽기·시트 기록)이 담당하고, 이 스크립트는
"비교/기록/알림"만 담당한다.)
"""

import os
import re
import sys
import json
import datetime
from zoneinfo import ZoneInfo
import requests
import gspread
from google.oauth2.service_account import Credentials

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_SHEET_TAB_NAME = os.environ.get("GOOGLE_SHEET_TAB_NAME", "스냅샷")
GOOGLE_SHEET_RAW_TAB_NAME = os.environ.get("GOOGLE_SHEET_RAW_TAB_NAME", "원본입력")

SNAPSHOT_PATH = "data/last_snapshot.json"
# 스냅샷 형식 버전 - 캡처+n8n 방식 데이터로 저장된 스냅샷에만 붙는다. 버전이 다르거나 없는
# (예전 오늘의집 API 시절) 스냅샷은 비교에 쓰지 않고 무시한다.
SNAPSHOT_SCHEMA = "capture-v1"
SNAPSHOT_SCHEMA_KEY = "_schema"

# 추적 플랫폼 (2026-09-27: 쿠팡 제외)
PLATFORMS = ("오늘의집", "네이버")

KST = ZoneInfo("Asia/Seoul")

# "원본입력" 탭의 헤더 (사람/Claude가 캡처를 읽고 이 순서대로 채워서 붙여넣는다).
# 날짜/수집시각은 비워둬도 되고(스크립트가 오늘 날짜로 채움), URL/비고는 선택 항목이다.
RAW_INPUT_HEADERS = [
    "플랫폼", "브랜드", "카테고리", "제품명", "정상가", "판매가", "리뷰수", "평점", "URL", "비고",
    "스크랩수", "배송비별도", "배송비",
]

# 우리 사업은 의자 중심이라, 캡처에 야외용(테라스/캠핑용 등) 상품이 섞여 들어와도
# 상품명 키워드로 걸러낸다 (100% 완벽하지 않은 휴리스틱 — 필요하면 조정).
OUTDOOR_NAME_KEYWORDS = ["야외", "아웃도어", "테라스", "캠핑", "정원"]

# 각 회사가 입점한 플랫폼 목록 (참고용 - 어느 회사/플랫폼을 매주 캡처해야 하는지 확인할 때 씀).
# 실제 수집 대상 제한은 캡처를 어디까지 하느냐로 결정되고, 이 스크립트는 "원본입력" 탭에 들어온
# 것만 처리한다.
ACTIVE_COMPANIES = {"폴인퍼니", "영가구"}

COMPANY_SOURCES = {
    "폴인퍼니": {
        "오늘의집": "store.ohou.se/brands/13004",
        "네이버": "brand.naver.com/fallinfuni",
    },
    "영가구": {
        "오늘의집": "store.ohou.se/brands/3554",
        "네이버": "brand.naver.com/younggagu",
    },
    "에이비퍼니쳐": {
        "오늘의집": "store.ohou.se/brands/6360",
        "네이버": "brand.naver.com/abfurniture",
    },
    "위드퍼니처": {
        "오늘의집": "store.ohou.se/brands/1061",
        "네이버": "smartstore.naver.com/withfurniture",
    },
}


_SCRAP_RE = re.compile(r"스크랩\s*([\d,]+)")
_FEE_RE = re.compile(r"(?:배송비|착불)\s*([\d,]+)\s*원")


def _parse_shipping_flag(value) -> str:
    """배송비별도 값을 'Y' / 'N' / ''로 통일한다."""
    text = str(value if value is not None else "").strip().upper()
    if text in ("Y", "YES", "예", "별도", "유료", "착불", "TRUE"):
        return "Y"
    if text in ("N", "NO", "아니오", "무료", "무료배송", "FALSE"):
        return "N"
    return ""


def derive_extras_from_note(note: str) -> dict:
    """'비고' 문장에서 스크랩수 / 배송비별도 / 배송비를 뽑는다. 못 찾으면 빈 값.
    '7000원 쿠폰'처럼 배송비가 아닌 금액은 '배송비'/'착불' 바로 뒤의 금액만 인정해서 피한다."""
    note = note or ""
    out = {"scrap_count": None, "shipping_separate": "", "shipping_fee": None}
    m = _SCRAP_RE.search(note)
    if m:
        out["scrap_count"] = _parse_number(m.group(1))
    if "무료배송" in note or "무료 배송" in note:
        out["shipping_separate"] = "N"
        out["shipping_fee"] = 0
    else:
        m = _FEE_RE.search(note)
        if m:
            out["shipping_separate"] = "Y"
            out["shipping_fee"] = _parse_number(m.group(1))
        elif "배송비 별도" in note or "배송비별도" in note or "착불" in note:
            out["shipping_separate"] = "Y"
    return out


def fill_missing_extras(p: dict) -> dict:
    """스크랩수/배송비 필드가 비어 있으면 비고에서 채운다 (이전 주 데이터 호환용)."""
    derived = derive_extras_from_note(p.get("note", ""))
    if p.get("scrap_count") is None:
        p["scrap_count"] = derived["scrap_count"]
    if not p.get("shipping_separate"):
        p["shipping_separate"] = derived["shipping_separate"]
    if p.get("shipping_fee") is None:
        p["shipping_fee"] = derived["shipping_fee"]
    return p


def _is_outdoor_product(name: str) -> bool:
    if not name:
        return False
    return any(keyword in name for keyword in OUTDOOR_NAME_KEYWORDS)


def _clean_name(name: str) -> str:
    """제품명 비교용 키를 만든다 - 앞뒤/중복 공백만 다듬고, 나머지(특히 "1+1" 같은 접미사)는
    절대 잘라내지 않는다. 이름을 뭉뚱그리면 서로 다른 상품을 같은 상품으로 잘못 매칭한다."""
    if not name:
        return ""
    return " ".join(name.strip().split())


def _parse_number(value):
    """'23,400원', '4.9', '' 같은 문자열에서 숫자만 뽑아낸다. 못 뽑으면 None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", "").replace("원", "").replace("개", "").strip()
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return None


def check_env():
    missing = []
    for name in (
        "SLACK_WEBHOOK_URL",
        "GOOGLE_SERVICE_ACCOUNT_JSON",
        "GOOGLE_SHEET_ID",
    ):
        if not os.environ.get(name):
            missing.append(name)
    if missing:
        print(f"[오류] 다음 환경변수가 설정되지 않았습니다: {', '.join(missing)}")
        sys.exit(1)


def get_spreadsheet():
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(GOOGLE_SHEET_ID)


def read_raw_input_rows(spreadsheet) -> list:
    """'원본입력' 탭에서 사람이 캡처+OCR로 정리해 붙여넣은 데이터를 읽어온다.
    (플랫폼, 브랜드, 제품명)이 비어있는 행은 무시한다."""
    try:
        worksheet = spreadsheet.worksheet(GOOGLE_SHEET_RAW_TAB_NAME)
    except gspread.exceptions.WorksheetNotFound:
        print(f"[오류] '{GOOGLE_SHEET_RAW_TAB_NAME}' 탭을 찾을 수 없습니다. "
              f"먼저 이 탭을 만들고 헤더를 {RAW_INPUT_HEADERS} 순서로 넣어주세요.")
        return []

    records = worksheet.get_all_records()  # 1행을 헤더로 보고 딕셔너리 리스트로 반환
    rows = []
    outdoor_excluded_count = 0
    duplicate_excluded_count = 0
    seen_keys = set()  # (플랫폼, 브랜드, 제품명) - 캡처 구간이 겹쳐서 같은 상품이 여러 번 들어온 경우 제거
    for r in records:
        platform = str(r.get("플랫폼", "")).strip()
        brand = str(r.get("브랜드", "")).strip()
        name = _clean_name(str(r.get("제품명", "")))
        if not platform or not brand or not name:
            continue
        if _is_outdoor_product(name):
            outdoor_excluded_count += 1
            continue
        key = (platform, brand, name)
        if key in seen_keys:
            duplicate_excluded_count += 1
            continue
        seen_keys.add(key)
        rows.append({
            "platform": platform,
            "company": brand,
            "category": str(r.get("카테고리", "")).strip() or "의자",
            "name": name,
            "regular_price": _parse_number(r.get("정상가")),
            "selling_price": _parse_number(r.get("판매가")),
            "review_count": _parse_number(r.get("리뷰수")),
            "rating": _parse_number(r.get("평점")),
            "url": str(r.get("URL", "")).strip() or None,
            "note": str(r.get("비고", "")).strip(),
            "scrap_count": _parse_number(r.get("스크랩수")),
            "shipping_separate": _parse_shipping_flag(r.get("배송비별도")),
            "shipping_fee": _parse_number(r.get("배송비")),
        })
        fill_missing_extras(rows[-1])

    if outdoor_excluded_count:
        print(f"[안내] 원본입력에서 야외용 추정 {outdoor_excluded_count}개 제외함")
    if duplicate_excluded_count:
        print(f"[안내] 원본입력에서 중복(캡처 구간 겹침 추정) {duplicate_excluded_count}개 제외함 "
              f"(같은 플랫폼+브랜드+제품명이 여러 번 나타나면 첫 번째 것만 사용)")

    return rows


def clear_raw_input_tab(spreadsheet):
    """처리 완료 후 '원본입력' 탭을 비워서(헤더는 남김) 다음 주 붙여넣기와 안 섞이게 한다."""
    try:
        worksheet = spreadsheet.worksheet(GOOGLE_SHEET_RAW_TAB_NAME)
    except gspread.exceptions.WorksheetNotFound:
        return
    all_values = worksheet.get_all_values()
    if len(all_values) <= 1:
        return  # 헤더밖에 없으면 지울 것도 없음
    worksheet.batch_clear([f"A2:Z{len(all_values)}"])


def group_by_company_and_platform(raw_rows: list) -> dict:
    """[{platform, company, name, ...}, ...] 리스트를 {회사: {플랫폼: [상품, ...]}} 형태로 묶는다."""
    grouped = {}
    for row in raw_rows:
        company = row["company"]
        platform = row["platform"]
        grouped.setdefault(company, {}).setdefault(platform, []).append(row)
    return grouped


def load_snapshot() -> dict:
    try:
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or data.get(SNAPSHOT_SCHEMA_KEY) != SNAPSHOT_SCHEMA:
        print("[안내] 지난 스냅샷이 예전 형식이라 비교에 쓰지 않습니다. "
              "이번 실행 결과가 새 비교 기준(첫 주)이 됩니다.")
        return {}
    data = dict(data)
    data.pop(SNAPSHOT_SCHEMA_KEY, None)
    return data


def save_snapshot(snapshot: dict):
    os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
    data = {SNAPSHOT_SCHEMA_KEY: SNAPSHOT_SCHEMA}
    data.update(snapshot)
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def annotate_products(prev_by_platform: dict, curr_by_platform: dict) -> tuple:
    """(플랫폼, 제품명) 조합을 키로 지난주 데이터와 비교해서 각 상품에 '_status'/'_note'를 붙이고,
    전체 변화 요약 리스트도 함께 반환한다.
    상품 고유 ID가 없는 캡처+OCR 데이터라서, 제품명 전체 문자열을 키로 쓴다 — "1+1" 같은 접미사도
    그대로 유지해야 실제로 다른 상품끼리 잘못 합쳐지지 않는다."""
    changes = []
    for platform, curr_products in curr_by_platform.items():
        prev_products = prev_by_platform.get(platform, [])
        prev_by_name = {_clean_name(p.get("name")): p for p in prev_products if p.get("name")}
        curr_names = set()

        for p in curr_products:
            key = _clean_name(p.get("name"))
            p["_status"] = ""
            p["_note"] = ""
            if not key:
                continue
            curr_names.add(key)
            prev = prev_by_name.get(key)

            if prev is None:
                p["_status"] = "신규"
                changes.append(f"🆕 신규 상품 [{platform}]: {p.get('name')} ({p.get('category')})")
                continue

            fill_missing_extras(prev)  # 이전 주 데이터엔 스크랩/배송비가 비고 문장에만 있을 수 있음
            notes = []
            prev_reviews, curr_reviews = prev.get("review_count"), p.get("review_count")
            if isinstance(prev_reviews, (int, float)) and isinstance(curr_reviews, (int, float)):
                delta = curr_reviews - prev_reviews
                if delta > 0:
                    p["_status"] = "리뷰증가"
                    notes.append(f"리뷰 {prev_reviews:,.0f}→{curr_reviews:,.0f} (+{delta:,.0f})")
                    changes.append(f"📈 리뷰 증가 [{platform}]: {p.get('name')} ({prev_reviews:,.0f}→{curr_reviews:,.0f}, +{delta:,.0f})")

            prev_price, curr_price = prev.get("selling_price"), p.get("selling_price")
            if isinstance(prev_price, (int, float)) and isinstance(curr_price, (int, float)) and prev_price != curr_price:
                arrow = "⬇️" if curr_price < prev_price else "⬆️"
                p["_status"] = "가격변동"
                notes.append(f"{prev_price:,.0f}원→{curr_price:,.0f}원")
                changes.append(f"{arrow} 가격 변동 [{platform}]: {p.get('name')} ({prev_price:,.0f}원→{curr_price:,.0f}원)")

            # 스크랩 증가: 리뷰가 아직 없는 신상품의 관심도 신호 (주의: '스크랩수'로 적어야 비고에서
            # 스크랩 수를 다시 뽑을 때 이 비교 문구가 잘못 걸리지 않는다)
            prev_scrap, curr_scrap = prev.get("scrap_count"), p.get("scrap_count")
            if isinstance(prev_scrap, (int, float)) and isinstance(curr_scrap, (int, float)):
                delta = curr_scrap - prev_scrap
                if delta > 0:
                    if not p["_status"]:
                        p["_status"] = "스크랩증가"
                    notes.append(f"스크랩수 {prev_scrap:,.0f}→{curr_scrap:,.0f} (+{delta:,.0f})")
                    changes.append(f"🔖 스크랩 증가 [{platform}]: {p.get('name')} ({prev_scrap:,.0f}→{curr_scrap:,.0f}, +{delta:,.0f})")

            # 배송 조건 변경: 무료배송 ↔ 배송비 별도 (둘 다 값이 있을 때만)
            prev_ship, curr_ship = prev.get("shipping_separate"), p.get("shipping_separate")
            if prev_ship and curr_ship and prev_ship != curr_ship:
                label = {"Y": "배송비 별도", "N": "무료배송"}
                if not p["_status"]:
                    p["_status"] = "배송조건변경"
                notes.append(f"{label[prev_ship]}→{label[curr_ship]}")
                changes.append(f"🚚 배송 조건 변경 [{platform}]: {p.get('name')} ({label[prev_ship]}→{label[curr_ship]})")

            p["_note"] = " / ".join(notes)

        # 지난주엔 있었는데 이번 주 캡처에서 통째로 사라진 상품 = 판매중단 의심
        # (다만 캡처+OCR 특성상, 화면을 일부만 캡처했으면 실제로는 팔리고 있는데 이번엔 캡처 범위
        # 밖이라 안 잡혔을 수도 있다 — 100% 확정이 아니라 "의심" 신호로만 취급한다.)
        for key, prev in prev_by_name.items():
            if key not in curr_names:
                changes.append(f"🛑 목록에서 사라짐(판매중단 또는 미캡처 의심) [{platform}]: {prev.get('name')}")

    return curr_by_platform, changes


def get_snapshot_worksheet(spreadsheet):
    return spreadsheet.worksheet(GOOGLE_SHEET_TAB_NAME)


# 시트 열 순서 (상품ID가 없어서 뺐고, 판매상태도 OCR로는 확실히 알 수 없어서 뺐다 -
# 품절 여부를 표시하고 싶으면 '원본입력' 탭의 "비고"에 사람이 적으면 그게 그대로 비고에 실린다).
SHEET_HEADERS = [
    "날짜", "수집시각", "플랫폼", "브랜드", "카테고리", "제품명",
    "정상가", "판매가", "리뷰수", "평점", "상태", "비고", "URL",
    # (2026-09-29 추가) 예전 행이 밀리지 않도록 반드시 맨 뒤에 붙인다 (N, O, P열)
    "스크랩수", "배송비별도", "배송비",
]


def ensure_sheet_headers(worksheet):
    """시트 1행이 기대하는 헤더와 다르면 덮어써서 맞춰둔다.
    예전 16열 헤더가 남아 있으면 13열만 덮어써도 N열 이후에 옛 헤더가 남으므로, 1행 전체를
    새 헤더 + 빈 칸으로 덮어쓴다."""
    try:
        first_row = worksheet.row_values(1)
    except Exception:
        first_row = []
    if first_row != SHEET_HEADERS:
        width = max(len(SHEET_HEADERS), len(first_row))
        padded = SHEET_HEADERS + [""] * (width - len(SHEET_HEADERS))
        worksheet.update("A1", [padded])


def append_products_to_sheet(worksheet, company: str, by_platform: dict, today: str, now_str: str):
    rows = []
    for platform, products in by_platform.items():
        if not products:
            rows.append([
                today, now_str, platform, company, "", "(상품 없음 - 이번 주 캡처 안 됨)",
                "", "", "", "", "", "", "", "", "", "",
            ])
            continue
        for p in products:
            note = p.get("_note", "")
            if p.get("note"):
                note = f"{note} / {p['note']}" if note else p["note"]
            rows.append([
                today,
                now_str,
                platform,
                company,
                p.get("category", ""),
                p.get("name", ""),
                p.get("regular_price") if p.get("regular_price") is not None else "",
                p.get("selling_price") if p.get("selling_price") is not None else "",
                p.get("review_count") if p.get("review_count") is not None else "",
                p.get("rating") if p.get("rating") is not None else "",
                p.get("_status", ""),
                note,
                p.get("url") or "",
                p.get("scrap_count") if p.get("scrap_count") is not None else "",
                p.get("shipping_separate") or "",
                p.get("shipping_fee") if p.get("shipping_fee") is not None else "",
            ])
    if rows:
        worksheet.append_rows(rows, value_input_option="USER_ENTERED")


def _col_letter(index: int) -> str:
    """0부터 시작하는 열 번호 → 'A', 'B', ... 'Z', 'AA' ..."""
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def backfill_snapshot_sheet(worksheet) -> int:
    """'스냅샷' 탭의 예전 행 중 스크랩수/배송비별도/배송비가 비어 있는 행을 '비고' 문장에서 채운다.
    리뷰 없이 스크랩 수만 보이던 행은 리뷰수·평점을 0으로 채운다(n8n v4 규칙과 동일).
    이미 값이 있는 칸은 절대 건드리지 않으므로 여러 번 실행해도 안전하다. 바뀐 칸 수를 돌려준다."""
    values = worksheet.get_all_values()
    if len(values) <= 1:
        return 0
    col = {name: SHEET_HEADERS.index(name) for name in
           ("플랫폼", "제품명", "리뷰수", "평점", "비고", "스크랩수", "배송비별도", "배송비")}
    updates = []

    def cell(row, name):
        i = col[name]
        return str(row[i]).strip() if i < len(row) and row[i] is not None else ""

    for r, row in enumerate(values[1:], start=2):
        if not cell(row, "제품명") or cell(row, "제품명").startswith("(상품 없음"):
            continue
        derived = derive_extras_from_note(cell(row, "비고"))
        wanted = {
            "스크랩수": derived["scrap_count"],
            "배송비별도": derived["shipping_separate"],
            "배송비": derived["shipping_fee"],
        }
        if derived["scrap_count"] is not None and cell(row, "리뷰수") == "" and cell(row, "평점") == "":
            wanted["리뷰수"] = 0
            wanted["평점"] = 0
        for name, val in wanted.items():
            if val is None or val == "" or cell(row, name) != "":
                continue
            updates.append({"range": f"{_col_letter(col[name])}{r}", "values": [[val]]})

    if updates:
        worksheet.batch_update(updates, value_input_option="USER_ENTERED")
    return len(updates)


def build_company_summary_text(company: str, by_platform: dict, changes: list, sheet_url: str) -> str:
    """Slack에는 회사별 요약(플랫폼별 발견 개수 + 변화 유무)과 시트 링크만 전송한다."""
    lines = [f"*:mag: {company} 주간 판매 동향 요약*"]
    for platform in PLATFORMS:
        products = by_platform.get(platform, [])
        if products:
            lines.append(f"  • {platform}: {len(products)}개 상품 확인")
        else:
            lines.append(f"  • {platform}: 이번 주 캡처 데이터 없음")
    if changes:
        lines.append(f"  ⚡ 변화 {len(changes)}건 감지 (신규/가격변동/리뷰증가/판매중단 의심)")
    else:
        lines.append("  변화 없음")
    lines.append(f"상세 데이터: {sheet_url}")
    return "\n".join(lines)


def send_text_to_slack(text: str):
    resp = requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=30)
    resp.raise_for_status()


def main():
    check_env()
    prev_snapshot = load_snapshot()
    now = datetime.datetime.now(KST)
    today = now.strftime("%Y-%m-%d")
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    sheet_url = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/edit"

    spreadsheet = get_spreadsheet()

    # "스냅샷" 탭: 헤더 맞추기 + 예전 행의 스크랩수/배송비 채우기 (원본입력이 비어 있어도 실행)
    worksheet = None
    try:
        worksheet = get_snapshot_worksheet(spreadsheet)
        ensure_sheet_headers(worksheet)
    except Exception as e:
        print(f"[오류] '{GOOGLE_SHEET_TAB_NAME}' 시트 연결 실패 (시트 기록은 건너뜁니다): {e}")
        worksheet = None
    if worksheet is not None:
        try:
            filled = backfill_snapshot_sheet(worksheet)
            if filled:
                print(f"[안내] '{GOOGLE_SHEET_TAB_NAME}' 탭의 예전 행에서 스크랩수/배송비 등 {filled}칸을 비고로부터 채웠습니다.")
        except Exception as e:
            print(f"[경고] 예전 행 채우기 실패 (이번 주 기록에는 영향 없음): {e}")

    raw_rows = read_raw_input_rows(spreadsheet)
    if not raw_rows:
        print(f"[안내] '{GOOGLE_SHEET_RAW_TAB_NAME}' 탭에 처리할 데이터가 없습니다. "
              f"캡처를 드라이브 폴더에 올리면 n8n이 이 탭을 채웁니다.")
        return

    grouped = group_by_company_and_platform(raw_rows)

    # 이번 주에 데이터가 없는 회사(캡처를 안 한 회사)의 지난 스냅샷은 그대로 유지해둔다.
    new_snapshot = dict(prev_snapshot)

    for company in grouped:
        print(f"처리 중: {company}")
        by_platform = grouped[company]

        prev_by_platform = prev_snapshot.get(company, {})
        if not isinstance(prev_by_platform, dict):
            prev_by_platform = {}

        by_platform, changes = annotate_products(prev_by_platform, by_platform)
        new_snapshot[company] = by_platform

        if worksheet is not None:
            try:
                append_products_to_sheet(worksheet, company, by_platform, today, now_str)
            except Exception as e:
                print(f"[오류] '{company}' 시트 기록 실패: {e}")

        summary_text = build_company_summary_text(company, by_platform, changes, sheet_url)
        send_text_to_slack(summary_text)
        print(f"  → '{company}' Slack 요약 전송 완료")

    save_snapshot(new_snapshot)

    try:
        clear_raw_input_tab(spreadsheet)
        print(f"[안내] '{GOOGLE_SHEET_RAW_TAB_NAME}' 탭을 비웠습니다 (다음 주 붙여넣기 준비 완료).")
    except Exception as e:
        print(f"[오류] '{GOOGLE_SHEET_RAW_TAB_NAME}' 탭 비우기 실패 (수동으로 비워주세요): {e}")


if __name__ == "__main__":
    main()
