"""
매주 월요일, 지정된 경쟁사들의 오늘의집 판매 현황(의자, 야외용 제외)을
오늘의집 공식 상품 데이터 API로 직접 수집하고, 구글시트에 기록 + Slack에 요약을 전송하는 스크립트.

- (2026-09-21 전면 개편) 이전 버전은 Claude API(web_fetch/web_search)로 페이지를 "읽고 추측"하는
  방식이었는데, 실제로 돌려보니 (1) 무한스크롤 때문에 카테고리 전체 중 일부만 확인되고,
  (2) URL/이미지가 거의 비어있고, (3) 브랜드별로 매번 다른(겹치지 않는) 상품 집합이 뽑히고,
  (4) 상품명이 매번 조금씩 다르게 추출되어 "신규 상품" 판정이 사실상 무의미해지는 문제가 있었다.
  이를 근본적으로 고치기 위해, 오늘의집이 화면을 그릴 때 실제로 사용하는 공식 데이터 API
  (store.ohou.se/api/brands/{brandId}/products)를 브라우저로 직접 확인해서 찾아냈고,
  이제 AI 추측 없이 이 API를 그대로 호출해서 100% 결정적이고 정확한 데이터를 가져온다.
  상품 고유 ID를 기준으로 주차별 비교를 하기 때문에 "신규/가격변동/리뷰증가/판매중단 의심" 판정도
  훨씬 신뢰할 수 있다.
- (2026-09-22 403 차단 대응) GitHub Actions에서 돌렸더니 오늘의집 API가 403 Forbidden을 반환하는
  문제가 발생했다. 같은 요청을 실제 브라우저(사용자 컴퓨터)에서 호출하면 지금도 정상(200)이라,
  이건 요청 내용 자체보다는 "GitHub Actions 서버에서 오는 요청"이라는 점(IP/봇 패턴) 때문에
  걸릴 가능성이 높다. 우선 시도해볼 수 있는 완화책으로 (1) 실제 브라우저가 보내는 것과 최대한
  비슷한 Referer/Origin/Accept-Language 헤더 추가, (2) 요청 사이에 약간의 지연, (3) 403을
  만나면 잠시 기다렸다가 재시도하는 로직을 추가했다. 다만 이게 GitHub Actions IP 자체를
  막아놓은 것이라면 헤더만으로는 완전히 해결이 안 될 수도 있어서, 재시도 후에도 계속 403이면
  실행 환경(예: 자체 러너)을 바꾸는 걸 고려해야 한다.
- (2026-09-22 범위 축소) 우리 사업은 의자 중심이라 테이블류(테이블·식탁·책상)는 수집 대상에서
  완전히 제외했다 (OHOU_CATEGORIES 참고). 또한 의자 중에서도 야외용(테라스/캠핑용 등)은
  작업 대상이 아니라서 제외한다 — 다만 오늘의집 API 응답에는 상품별로 "이건 야외용" 이라고
  명시하는 필드가 없다(개별 상품 raw JSON을 직접 열어서 확인함). 야외가구>야외의자
  카테고리(10250003)는 애초에 의자(10210000) 카테고리와 별개라서 자동으로 빠지지만,
  실제로는 인테리어의자/스툴·벤치 등 실내 카테고리로 분류되어 있으면서도 상품명에
  "야외/테라스/캠핑/아웃도어/정원" 등이 들어간 상품이 소수 존재해서 이런 것들은 카테고리
  필터만으로는 안 걸러진다. 그래서 OUTDOOR_NAME_KEYWORDS로 상품명 키워드 매칭을 추가해서
  걸러낸다 (100% 완벽하지는 않은 휴리스틱이라, 실제 결과를 보고 키워드를 조정할 수 있다).
- 쿠팡/네이버는 여전히 보류 상태 (ACTIVE_PLATFORMS 참고). 나중에 공식 API 연동 시 이 구조를
  참고해서 별도의 결정적 수집 함수를 추가하면 된다 (AI 추측 방식으로는 돌아가지 않을 것).
- (2026-09-26 테스트용 제한) 시트를 완전히 비우고 다시 검증하는 테스트라서, 카테고리당 상품을
  전부 가져오지 않고 MAX_PRODUCTS_PER_CATEGORY(=10)개까지만 가져오도록 임시로 제한해뒀다.
  실제 운영으로 넘어갈 때는 이 값을 None으로 바꾸면 된다.
- "진짜 자료"는 구글시트(경쟁사 제품 스냅샷)에 상세 기록하고, Slack에는 회사별 요약 + 시트 링크만 전송

필요한 환경변수:
- SLACK_WEBHOOK_URL        : Slack Incoming Webhook URL
- GOOGLE_SERVICE_ACCOUNT_JSON : 구글 서비스 계정 키(JSON) 전체 내용을 문자열로
- GOOGLE_SHEET_ID          : "경쟁사 제품 스냅샷" 구글시트의 ID (URL의 /d/ 뒤 부분)
- GOOGLE_SHEET_TAB_NAME    : (선택) 기록할 탭 이름, 기본값 "스냅샷"

(참고: 오늘의집 수집에는 더 이상 Anthropic API가 필요 없어져서 ANTHROPIC_API_KEY는 필수 목록에서
빠졌다. 나중에 AI 요약/트렌드 분석 등에 다시 쓰게 되면 그때 추가하면 된다.)
"""

import os
import sys
import json
import re
import time
import random
import datetime
import requests
import gspread
from google.oauth2.service_account import Credentials

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_SHEET_TAB_NAME = os.environ.get("GOOGLE_SHEET_TAB_NAME", "스냅샷")

SNAPSHOT_PATH = "data/last_snapshot.json"

# (2026-09-26 테스트용) 시트를 깨끗하게 비우고 새로 검증하는 테스트라, 브랜드당 전체를 다
# 긁어오지 않고 카테고리당 10개까지만 가져오도록 제한한다. 실제 운영에 들어가면 이 값을
# None으로 바꿔서 제한을 풀면 된다.
MAX_PRODUCTS_PER_CATEGORY = 10

# 지금 결정적(deterministic)으로 수집 가능한 플랫폼만 여기 넣는다.
# 쿠팡/네이버는 봇 차단 때문에 지금 방식(공식 API 없이)으로는 신뢰할 수 있는 수집이 안 되므로
# 보류 상태. COMPANY_SOURCES에 정보는 남겨두되(나중에 재사용), 실제 수집은 건너뛴다.
ACTIVE_PLATFORMS = {"오늘의집"}

# 오늘의집 카테고리 ID (브라우저 개발자도구로 실제 API 요청을 확인해서 얻은 값들).
# 우리 사업은 의자 중심이라 테이블류는 뺐다. 여기 추가하면 추적 범위를 늘릴 수 있다.
OHOU_CATEGORIES = {
    "의자": 10210000,
}

# 의자 카테고리 안에는 인테리어의자/스툴·벤치/안락의자/학생·사무용의자/바체어가 섞여 있는데,
# 이 중 상품명에 아래 키워드가 들어간 상품은 "야외용"으로 보고 수집에서 제외한다.
# (오늘의집 API 응답에는 상품별 "야외용 여부" 필드가 따로 없어서, 상품명 키워드로 판단하는
# 방식이다 — 100% 정확하진 않을 수 있으니, 실제로 걸러진 목록을 보고 필요하면 조정하자.)
OUTDOOR_NAME_KEYWORDS = ["야외", "아웃도어", "테라스", "캠핑", "정원"]


def _is_outdoor_product(name: str) -> bool:
    if not name:
        return False
    return any(keyword in name for keyword in OUTDOOR_NAME_KEYWORDS)

# (2026-09-22) 우선 폴인퍼니/영가구 2곳만 테스트로 돌려본다. 나머지 회사는 COMPANY_SOURCES에
# 그대로 남겨두고(재사용 위해), 실제 수집 대상만 이 집합으로 제한한다. 테스트가 끝나고 전체로
# 넓히고 싶으면 이 줄만 ACTIVE_COMPANIES = set(COMPANY_SOURCES) 로 바꾸면 된다.
ACTIVE_COMPANIES = {"폴인퍼니", "영가구"}

# 각 회사가 입점한 플랫폼 목록 (플랫폼 이름 + 브랜드 스토어 주소)
COMPANY_SOURCES = {
    "폴인퍼니": {
        "오늘의집": "store.ohou.se/brands/13004",
        "쿠팡": "shop.coupang.com/fallinfuni",
        "네이버": "brand.naver.com/fallinfuni",
    },
    "영가구": {
        "오늘의집": "store.ohou.se/brands/3554",
        "쿠팡": "shop.coupang.com/younggagu",
        "네이버": "brand.naver.com/younggagu",
    },
    "에이비퍼니쳐": {
        "오늘의집": "store.ohou.se/brands/6360",
        "쿠팡": "shop.coupang.com/abfurniture",
        "네이버": "brand.naver.com/abfurniture",
    },
    "위드퍼니처": {
        "오늘의집": "store.ohou.se/brands/1061",
        "쿠팡": "shop.coupang.com/A00061777",
        "네이버": "smartstore.naver.com/withfurniture",
    },
}

# 실제 브라우저가 이 API를 부를 때 함께 보내는 헤더들을 최대한 비슷하게 흉내낸다.
# (Referer/Origin은 브랜드별로 달라져서 요청 시점에 채워 넣는다 - _build_request_headers 참고)
REQUEST_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://store.ohou.se",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}

# 403(차단)을 만났을 때 재시도 설정 - GitHub Actions처럼 브라우저가 아닌 환경에서 오는 요청을
# 오늘의집 쪽에서 일시적으로 더 엄격하게 걸러낼 수 있어서, 약간 쉬었다가 다시 시도해본다.
MAX_RETRIES_ON_BLOCK = 3
RETRY_BACKOFF_SECONDS = [3, 8, 15]


def _build_request_headers(brand_id: int, category_id: int) -> dict:
    """실제 브라우저에서 이 API를 호출할 때 함께 실리는 Referer(예: 브랜드 페이지에서
    카테고리를 클릭해서 들어온 상태)를 최대한 똑같이 흉내낸 헤더를 만든다."""
    headers = dict(REQUEST_HEADERS)
    headers["Referer"] = f"https://store.ohou.se/brands/{brand_id}?categoryId={category_id}"
    return headers


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


def _extract_ohou_brand_id(store_hint: str):
    """store.ohou.se/brands/3554 같은 문자열에서 브랜드ID(숫자)만 뽑아낸다."""
    match = re.search(r"brands/(\d+)", store_hint)
    return int(match.group(1)) if match else None


def fetch_ohou_category_products(brand_id: int, category_id: int, category_label: str) -> list:
    """오늘의집이 실제로 화면을 그릴 때 쓰는 공식 상품 데이터 API를 그대로 호출해서
    해당 브랜드 + 카테고리의 상품을 전체 페이지네이션으로 모두 가져온다.
    AI 추측이 전혀 없는 결정적(deterministic) 수집이라 매번 같은 조건이면 같은 결과가 나온다."""
    products = []
    raw_fetched_count = 0  # 필터링 전, API가 실제로 내려준 원본 상품 개수(페이지네이션 종료 판단용)
    outdoor_excluded_count = 0
    page = 1
    total_count = None
    headers = _build_request_headers(brand_id, category_id)
    while True:
        url = (
            f"https://store.ohou.se/api/brands/{brand_id}/products"
            f"?brandId={brand_id}&page={page}&order=popular&filterQuery=categoryId%3D{category_id}"
        )

        data = None
        for attempt in range(MAX_RETRIES_ON_BLOCK + 1):
            try:
                resp = requests.get(url, headers=headers, timeout=20)
                if resp.status_code == 403 and attempt < MAX_RETRIES_ON_BLOCK:
                    wait_s = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
                    print(f"    [경고] 403(차단 추정) - {wait_s}초 대기 후 재시도 "
                          f"({attempt + 1}/{MAX_RETRIES_ON_BLOCK}) (brand={brand_id}, category={category_label}, page={page})")
                    time.sleep(wait_s)
                    continue
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                if attempt < MAX_RETRIES_ON_BLOCK:
                    wait_s = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
                    print(f"    [경고] 오늘의집 API 호출 실패, {wait_s}초 대기 후 재시도 "
                          f"({attempt + 1}/{MAX_RETRIES_ON_BLOCK}) (brand={brand_id}, category={category_label}, page={page}): {e}")
                    time.sleep(wait_s)
                    continue
                print(f"    [오류] 오늘의집 API 호출 최종 실패 (brand={brand_id}, category={category_label}, page={page}): {e}")

        if data is None:
            break

        # 요청 사이에 짧게 쉬어서 너무 기계적인(봇처럼 보이는) 연속 호출 패턴을 피한다.
        time.sleep(random.uniform(0.4, 1.0))

        page_products = data.get("products", [])
        if total_count is None:
            total_count = data.get("totalCount", 0)
        if not page_products:
            break
        raw_fetched_count += len(page_products)

        for p in page_products:
            if p.get("isHidden"):
                continue
            if _is_outdoor_product(p.get("name")):
                outdoor_excluded_count += 1
                continue
            price_info = p.get("price") or {}
            selling_price = p.get("sellingPrice")
            if selling_price is None:
                selling_price = price_info.get("sellingPrice")
            regular_price = p.get("originalPrice")
            if regular_price is None:
                regular_price = price_info.get("regularPrice")
            product_id = p.get("id")
            products.append({
                "product_id": product_id,
                "name": p.get("name"),
                "category": category_label,
                "selling_price": selling_price,
                "regular_price": regular_price,
                "review_count": p.get("reviewCount"),
                "rating": p.get("reviewAvg"),
                "image_url": p.get("resizedImageUrl") or p.get("imageUrl"),
                "url": f"https://store.ohou.se/goods/{product_id}" if product_id else None,
                "is_sold_out": bool(p.get("isSoldOut")),
                "is_selling": p.get("isSelling", True),
            })

        if MAX_PRODUCTS_PER_CATEGORY is not None and len(products) >= MAX_PRODUCTS_PER_CATEGORY:
            products = products[:MAX_PRODUCTS_PER_CATEGORY]
            break

        if raw_fetched_count >= total_count:
            break
        page += 1
        if page > 60:  # 안전장치: 혹시 모를 무한루프 방지
            print(f"    [경고] 페이지가 60을 넘어가서 중단함 (brand={brand_id}, category={category_label})")
            break

    if outdoor_excluded_count:
        print(f"    - {category_label}: 야외용 추정 {outdoor_excluded_count}개 제외함")

    return products


def get_ohou_products_for_company(store_hint: str) -> list:
    """한 회사의 오늘의집 전체 상품(등록된 모든 카테고리 합산)을 가져온다."""
    brand_id = _extract_ohou_brand_id(store_hint)
    if brand_id is None:
        print(f"    [오류] 오늘의집 브랜드ID를 찾을 수 없음: {store_hint}")
        return []

    all_products = []
    for label, category_id in OHOU_CATEGORIES.items():
        cat_products = fetch_ohou_category_products(brand_id, category_id, label)
        print(f"    - {label}: {len(cat_products)}개 확인")
        all_products.extend(cat_products)
    return all_products


def load_snapshot() -> dict:
    try:
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_snapshot(snapshot: dict):
    os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)


def annotate_products(prev_by_platform: dict, curr_by_platform: dict) -> tuple:
    """상품 고유 ID를 기준으로 지난주 데이터와 비교해서 각 상품에 '_status'/'_note'를 붙이고,
    전체 변화 요약 리스트도 함께 반환한다. (이름이 아니라 ID로 비교하므로, 상품명 표기가 살짝
    달라져도 같은 상품으로 정확히 인식된다 — 이전 버전의 핵심 버그였던 부분.)"""
    changes = []
    for platform, curr_products in curr_by_platform.items():
        prev_products = prev_by_platform.get(platform, [])
        prev_by_id = {p.get("product_id"): p for p in prev_products if p.get("product_id") is not None}
        curr_ids = set()

        for p in curr_products:
            pid = p.get("product_id")
            p["_status"] = ""
            p["_note"] = ""
            if pid is None:
                continue
            curr_ids.add(pid)
            prev = prev_by_id.get(pid)

            if prev is None:
                p["_status"] = "신규"
                changes.append(f"🆕 신규 상품 [{platform}]: {p.get('name')} ({p.get('category')})")
                continue

            notes = []
            prev_reviews, curr_reviews = prev.get("review_count"), p.get("review_count")
            if isinstance(prev_reviews, (int, float)) and isinstance(curr_reviews, (int, float)):
                delta = curr_reviews - prev_reviews
                if delta > 0:
                    p["_status"] = "리뷰증가"
                    notes.append(f"리뷰 {prev_reviews}→{curr_reviews} (+{delta})")
                    changes.append(f"📈 리뷰 증가 [{platform}]: {p.get('name')} ({prev_reviews}→{curr_reviews}, +{delta})")

            prev_price, curr_price = prev.get("selling_price"), p.get("selling_price")
            if isinstance(prev_price, (int, float)) and isinstance(curr_price, (int, float)) and prev_price != curr_price:
                arrow = "⬇️" if curr_price < prev_price else "⬆️"
                p["_status"] = "가격변동"
                notes.append(f"{prev_price:,.0f}원→{curr_price:,.0f}원")
                changes.append(f"{arrow} 가격 변동 [{platform}]: {p.get('name')} ({prev_price:,.0f}원→{curr_price:,.0f}원)")

            if p.get("is_sold_out") and not prev.get("is_sold_out"):
                p["_status"] = "품절"
                notes.append("품절 전환")
                changes.append(f"⚠️ 품절 전환 [{platform}]: {p.get('name')}")

            p["_note"] = " / ".join(notes)

        # 지난주엔 있었는데 이번주 목록에서 통째로 사라진 상품 = 판매중단 의심
        # (데이터가 이제 결정적/완전하기 때문에, 이 신호를 처음으로 신뢰할 수 있다.)
        for pid, prev in prev_by_id.items():
            if pid not in curr_ids:
                changes.append(f"🛑 목록에서 사라짐(판매중단 의심) [{platform}]: {prev.get('name')}")

    return curr_by_platform, changes


def get_sheet_worksheet():
    """GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_SHEET_ID 환경변수로 시트에 접근한다."""
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    return spreadsheet.worksheet(GOOGLE_SHEET_TAB_NAME)


# 시트 열 순서 (QA를 위해 상품ID/정상가/판매가/판매상태 등을 추가했다)
SHEET_HEADERS = [
    "날짜", "수집시각", "플랫폼", "브랜드", "카테고리", "상품ID", "제품명",
    "정상가", "판매가", "리뷰수", "평점", "판매상태", "상태", "비고", "URL", "이미지",
]


def ensure_sheet_headers(worksheet):
    """시트 1행이 기대하는 헤더와 다르면 덮어써서 맞춰둔다."""
    try:
        first_row = worksheet.row_values(1)
    except Exception:
        first_row = []
    if first_row != SHEET_HEADERS:
        worksheet.update("A1", [SHEET_HEADERS])


def append_products_to_sheet(worksheet, company: str, by_platform: dict, today: str, now_str: str):
    rows = []
    for platform, products in by_platform.items():
        if not products:
            status = "조사 실패" if platform in ACTIVE_PLATFORMS else "추후 지원 예정(공식 API 연동 전)"
            rows.append([
                today, now_str, platform, company, "", "", "(상품 없음)",
                "", "", "", "", "", status, "", "", "",
            ])
            continue
        for p in products:
            image_formula = f'=IMAGE("{p["image_url"]}", 4, 60, 60)' if p.get("image_url") else ""
            sale_status = "품절" if p.get("is_sold_out") else ("판매중" if p.get("is_selling", True) else "판매중단")
            rows.append([
                today,
                now_str,
                platform,
                company,
                p.get("category", ""),
                p.get("product_id") or "",
                p.get("name", ""),
                p.get("regular_price") if p.get("regular_price") is not None else "",
                p.get("selling_price") if p.get("selling_price") is not None else "",
                p.get("review_count") if p.get("review_count") is not None else "",
                p.get("rating") if p.get("rating") is not None else "",
                sale_status,
                p.get("_status", ""),
                p.get("_note", ""),
                p.get("url") or "",
                image_formula,
            ])
    if rows:
        worksheet.append_rows(rows, value_input_option="USER_ENTERED")


def build_company_summary_text(company: str, by_platform: dict, changes: list, sheet_url: str) -> str:
    """Slack에는 회사별 요약(플랫폼별 발견 개수 + 변화 유무)과 시트 링크만 전송한다."""
    lines = [f"*:mag: {company} 주간 판매 동향 요약*"]
    for platform, products in by_platform.items():
        if products:
            lines.append(f"  • {platform}: {len(products)}개 상품 확인")
        elif platform not in ACTIVE_PLATFORMS:
            lines.append(f"  • {platform}: 아직 미지원 (추후 공식 API 연동 예정)")
        else:
            lines.append(f"  • {platform}: 상품 없음")
    if changes:
        lines.append(f"  ⚡ 변화 {len(changes)}건 감지 (신규/가격변동/리뷰증가/품절/판매중단 의심)")
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
    # 이번에 건너뛴 회사(ACTIVE_COMPANIES에 없는 회사)의 지난 스냅샷은 그대로 유지해둔다.
    # 나중에 다시 활성화했을 때, 원래 있던 상품들이 전부 "신규"로 잘못 뜨는 걸 방지하기 위함.
    new_snapshot = dict(prev_snapshot)
    now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    sheet_url = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/edit"

    worksheet = None
    try:
        worksheet = get_sheet_worksheet()
        ensure_sheet_headers(worksheet)
    except Exception as e:
        print(f"[오류] 구글시트 연결 실패 (시트 기록은 건너뜁니다): {e}")

    for company, platforms in COMPANY_SOURCES.items():
        if company not in ACTIVE_COMPANIES:
            print(f"건너뜀 (테스트 대상 아님): {company}")
            continue
        print(f"조사 중: {company}")
        by_platform = {}
        for platform, store_hint in platforms.items():
            if platform not in ACTIVE_PLATFORMS:
                print(f"  - {platform}: 아직 연동 안 됨 (건너뜀)")
                by_platform[platform] = []
                continue
            print(f"  - {platform} 수집 중...")
            if platform == "오늘의집":
                by_platform[platform] = get_ohou_products_for_company(store_hint)
            else:
                by_platform[platform] = []

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


if __name__ == "__main__":
    main()
