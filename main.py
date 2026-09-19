"""
매주 월요일, 지정된 경쟁사들의 대형 플랫폼(오늘의집/쿠팡/네이버) 판매 현황을
Claude API 웹 검색으로 조사하고, 구글시트에 기록 + Slack에 요약을 전송하는 스크립트.

- 홈페이지 동향은 조사하지 않음
- 특정 카테고리(CATEGORY)의 상품만 추적
- 각 플랫폼의 상품별 가격/리뷰수/평점/이미지를 조사하고, 지난주 대비 변화(신규 상품, 가격/리뷰 변동)를 추적
- "진짜 자료"는 구글시트(경쟁사 제품 스냅샷)에 상세 기록하고, Slack에는 회사별 요약 + 시트 링크만 전송

필요한 환경변수:
- ANTHROPIC_API_KEY        : Anthropic API 키
- SLACK_WEBHOOK_URL        : Slack Incoming Webhook URL
- GOOGLE_SERVICE_ACCOUNT_JSON : 구글 서비스 계정 키(JSON) 전체 내용을 문자열로
- GOOGLE_SHEET_ID          : "경쟁사 제품 스냅샷" 구글시트의 ID (URL의 /d/ 뒤 부분)
- GOOGLE_SHEET_TAB_NAME    : (선택) 기록할 탭 이름, 기본값 "스냅샷"
"""

import os
import sys
import json
import re
import datetime
import urllib.parse
import requests
import gspread
from google.oauth2.service_account import Credentials

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_SHEET_TAB_NAME = os.environ.get("GOOGLE_SHEET_TAB_NAME", "스냅샷")

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-5"
SNAPSHOT_PATH = "data/last_snapshot.json"

# 추적할 상품 카테고리 (여기를 수정하면 범위를 바꿀 수 있어요)
CATEGORY = "식탁, 테이블, 의자"

# 각 회사가 입점한 플랫폼 목록 (플랫폼 이름 + 검색에 참고할 스토어 주소)
# ⚠️ 테스트용 임시 버전: 비용을 아끼기 위해 "영가구" 1곳만 조사하도록 나머지 3곳을 잠시 빼둔 상태입니다.
# 정상적으로 확인되면 원래의 4개 회사가 모두 들어있는 main.py로 다시 덮어써야 합니다.
COMPANY_SOURCES = {
    "영가구": {
        "오늘의집": "store.ohou.se/brands/3554",
        "쿠팡": "shop.coupang.com/younggagu",
        "네이버": "brand.naver.com/younggagu",
    },
}


def check_env():
    missing = []
    for name in (
        "ANTHROPIC_API_KEY",
        "SLACK_WEBHOOK_URL",
        "GOOGLE_SERVICE_ACCOUNT_JSON",
        "GOOGLE_SHEET_ID",
    ):
        if not os.environ.get(name):
            missing.append(name)
    if missing:
        print(f"[오류] 다음 환경변수가 설정되지 않았습니다: {', '.join(missing)}")
        sys.exit(1)


def _full_url(store_hint: str) -> str:
    """store_hint에 스킴이 없으면 https:// 를 붙여 완전한 URL로 만든다.
    (web_fetch 도구는 대화(user 메시지) 안에 이미 등장한 URL만 가져올 수 있으므로,
    프롬프트에 반드시 완전한 형태의 URL을 넣어줘야 한다.)"""
    if store_hint.startswith("http://") or store_hint.startswith("https://"):
        return store_hint
    return f"https://{store_hint}"


def _search_url(platform: str, company: str) -> str:
    """플랫폼별 공개 검색결과 페이지 URL을 만든다.
    브랜드 스토어 페이지 하나만으로는 목록이 비어있거나 차단되는 경우가 많아서,
    실제 검색 결과 페이지도 함께 열어보게 해서 데이터가 잡힐 확률을 높인다."""
    q = urllib.parse.quote(company)
    if platform == "오늘의집":
        return f"https://ohou.se/productions/feed?query={q}&search_affect_type=Recommend"
    if platform == "쿠팡":
        return f"https://www.coupang.com/np/search?q={q}"
    if platform == "네이버":
        return f"https://search.shopping.naver.com/search/all?query={q}"
    return ""


def get_platform_products(company: str, platform: str, store_hint: str) -> list:
    """플랫폼별로 브랜드 상품(카테고리 한정)을 조사해 JSON으로 추출.

    web_fetch로 (1) 브랜드 스토어 페이지, (2) 플랫폼 검색결과 페이지를 순서대로 직접
    열어보게 하고, 그걸로도 부족할 때만 web_search로 보완하도록 지시한다.
    스토어 페이지 하나만 보는 이전 버전은 쿠팡/네이버처럼 스토어 페이지 자체가
    차단되거나 목록이 비어 보이는 플랫폼에서 계속 빈 결과가 나왔는데, 검색결과
    페이지를 추가로 시도하면 잡히는 경우가 있다.
    이번 버전은 상품별 상세 링크(url)와 대표 이미지(image_url)도 함께 요청해서
    구글시트에 사진과 함께 기록할 수 있게 한다."""
    store_url = _full_url(store_hint)
    search_url = _search_url(platform, company)
    prompt = f"""
'{platform}' 쇼핑 플랫폼에서 판매되는 가구 브랜드 '{company}'의 상품 현황을 조사해줘.

아래 순서로 직접 페이지를 열어봐(web_fetch 사용):
1) 브랜드 스토어 페이지: {store_url}
2) 위에서 상품 정보가 부족하거나 페이지가 차단/오류나면, 플랫폼 검색결과 페이지: {search_url}
- 두 페이지 중 하나에서라도 상품 목록/가격/리뷰수/평점/상품 상세 링크/대표 이미지 URL을 확인할 수 있으면 그 값을 사용해.
- 검색결과 페이지를 볼 때는 '{company}' 브랜드가 맞는 상품만 골라야 해 (다른 브랜드 상품 섞이지 않게 주의).
- 그래도 정보가 불충분하면 web_search로 보완 조사해.
- 그래도 확인이 안 되면 억지로 지어내지 말고 해당 필드는 null로 남겨.

조건:
- '{CATEGORY}' 및 이와 밀접히 관련된 상품(다이닝 체어, 스툴, 벤치, 다이닝 세트 등)만 포함
- 반드시 '{platform}' 플랫폼에 올라온 '{company}' 상품이어야 함 (다른 플랫폼/다른 브랜드 제외)
- 브랜드명은 띄어쓰기나 '처/쳐' 등 표기가 다를 수 있으니 유사 표기도 함께 확인
- 확인 가능한 상위 3~5개만
- image_url은 상품 사진의 실제 이미지 파일 주소(og:image, 썸네일 src 등)를 우선 사용

다른 설명이나 인사말 없이, 순수 JSON 한 개만 응답해 (마크다운 코드블록 표시도 쓰지 마):

{{"products": [{{"name": "상품명", "price": 숫자또는null, "review_count": 숫자또는null, "rating": 숫자또는null, "url": "상품상세링크또는null", "image_url": "이미지주소또는null"}}]}}

- price는 원 단위 숫자만 (콤마/원 제외), review_count는 숫자만, 확인 불가한 값은 null
- 관련 상품을 찾을 수 없으면 {{"products": []}}
"""
    text = ""
    try:
        response = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                # 이전 버전은 2000으로 낮게 잡아서, 페이지 내용을 읽고 나면 최종 JSON을
                # 다 쓰기 전에 토큰이 바닥나 "빈 응답(stop_reason=max_tokens)"이 되는
                # 경우가 있었다. 출력 여유를 넉넉히 늘렸다.
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
                "tools": [
                    {
                        "type": "web_fetch_20250910",
                        "name": "web_fetch",
                        # 스토어 페이지 + 검색결과 페이지, 최대 2번 정도 더 시도할 여유
                        "max_uses": 4,
                        # 페이지 하나당 상한을 낮춰서, 최종 답변 쓸 토큰이 모자라지 않게 함
                        "max_content_tokens": 15000,
                    },
                    {"type": "web_search_20250305", "name": "web_search", "max_uses": 5},
                ],
            },
            timeout=180,
        )
        response.raise_for_status()
        data = response.json()
        texts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        text = "\n".join(texts).strip()
        stop_reason = data.get("stop_reason")

        if not text:
            print(f"[경고] '{company}' / '{platform}': 빈 응답 (stop_reason={stop_reason})")
            return []

        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        json_str = match.group(0) if match else text
        parsed = json.loads(json_str)
        products = parsed.get("products", [])
        print(f"    ({platform}: {len(products)}개 상품 확인)")
        return products

    except requests.exceptions.HTTPError as e:
        body = e.response.text[:500] if e.response is not None else ""
        print(f"[경고] '{company}' / '{platform}' HTTP 오류: {e} | 서버 응답: {body}")
        return []
    except Exception as e:
        print(f"[경고] '{company}' / '{platform}' 조사 오류: {e} | 응답 일부: {text[:300]}")
        return []


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
    """각 상품에 '_status'(신규/가격변동/리뷰증가/없음)와 '_note'(사람이 읽을 상세 설명)를
    붙이고, 전체 변화 요약 리스트도 함께 반환한다. (구글시트의 상태/비고 열에 사용)"""
    changes = []
    for platform, curr_products in curr_by_platform.items():
        prev_products = prev_by_platform.get(platform, [])
        prev_by_name = {p.get("name"): p for p in prev_products if p.get("name")}

        for p in curr_products:
            name = p.get("name")
            p["_status"] = ""
            p["_note"] = ""
            if not name:
                continue
            prev = prev_by_name.get(name)

            if prev is None:
                p["_status"] = "신규"
                changes.append(f"🆕 신규 상품 등장 [{platform}]: {name}")
                continue

            notes = []
            prev_reviews, curr_reviews = prev.get("review_count"), p.get("review_count")
            if isinstance(prev_reviews, (int, float)) and isinstance(curr_reviews, (int, float)):
                delta = curr_reviews - prev_reviews
                if delta > 0:
                    p["_status"] = "리뷰증가"
                    notes.append(f"리뷰 {prev_reviews}→{curr_reviews} (+{delta})")
                    changes.append(f"📈 리뷰 증가 [{platform}]: {name} ({prev_reviews} → {curr_reviews}, +{delta})")

            prev_price, curr_price = prev.get("price"), p.get("price")
            if isinstance(prev_price, (int, float)) and isinstance(curr_price, (int, float)) and prev_price != curr_price:
                arrow = "⬇️" if curr_price < prev_price else "⬆️"
                p["_status"] = "가격변동"
                notes.append(f"{prev_price:,.0f}원→{curr_price:,.0f}원")
                changes.append(f"{arrow} 가격 변동 [{platform}]: {name} ({prev_price:,.0f}원 → {curr_price:,.0f}원)")

            p["_note"] = " / ".join(notes)

    return curr_by_platform, changes


def get_sheet_worksheet():
    """GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_SHEET_ID 환경변수로 시트에 접근한다."""
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    return spreadsheet.worksheet(GOOGLE_SHEET_TAB_NAME)


def append_products_to_sheet(worksheet, company: str, by_platform: dict, today: str):
    """상품별로 한 행씩 시트에 추가한다.
    열 순서: 날짜 | 플랫폼 | 브랜드 | 제품명 | 판매가 | 리뷰수 | URL | 상태 | 비고 | 평점 | 이미지"""
    rows = []
    for platform, products in by_platform.items():
        if not products:
            rows.append([
                today, platform, company, "(상품 없음)", "", "", "", "조사 실패", "", "", "",
            ])
            continue
        for p in products:
            image_formula = f'=IMAGE("{p["image_url"]}", 4, 60, 60)' if p.get("image_url") else ""
            rows.append([
                today,
                platform,
                company,
                p.get("name", ""),
                p.get("price") if p.get("price") is not None else "",
                p.get("review_count") if p.get("review_count") is not None else "",
                p.get("url") or "",
                p.get("_status", ""),
                p.get("_note", ""),
                p.get("rating") if p.get("rating") is not None else "",
                image_formula,
            ])
    if rows:
        worksheet.append_rows(rows, value_input_option="USER_ENTERED")


def build_company_summary_text(company: str, by_platform: dict, changes: list, sheet_url: str) -> str:
    """Slack에는 회사별 요약(플랫폼별 발견 개수 + 변화 유무)과 시트 링크만 전송한다."""
    lines = [f"*:mag: {company} 주간 판매 동향 요약 - {CATEGORY}*"]
    for platform, products in by_platform.items():
        lines.append(f"  • {platform}: {len(products)}개 상품 확인" if products else f"  • {platform}: 상품 없음")
    if changes:
        lines.append(f"  ⚡ 변화 {len(changes)}건 감지 (신규/가격변동/리뷰증가)")
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
    new_snapshot = {}
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    sheet_url = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/edit"

    worksheet = None
    try:
        worksheet = get_sheet_worksheet()
    except Exception as e:
        print(f"[오류] 구글시트 연결 실패 (시트 기록은 건너뜁니다): {e}")

    for company, platforms in COMPANY_SOURCES.items():
        print(f"조사 중: {company}")
        by_platform = {}
        for platform, store_hint in platforms.items():
            print(f"  - {platform} 확인 중...")
            by_platform[platform] = get_platform_products(company, platform, store_hint)

        prev_by_platform = prev_snapshot.get(company, {})
        if not isinstance(prev_by_platform, dict):
            prev_by_platform = {}

        by_platform, changes = annotate_products(prev_by_platform, by_platform)
        new_snapshot[company] = by_platform

        if worksheet is not None:
            try:
                append_products_to_sheet(worksheet, company, by_platform, today)
            except Exception as e:
                print(f"[오류] '{company}' 시트 기록 실패: {e}")

        summary_text = build_company_summary_text(company, by_platform, changes, sheet_url)
        send_text_to_slack(summary_text)
        print(f"  → '{company}' Slack 요약 전송 완료")

    save_snapshot(new_snapshot)


if __name__ == "__main__":
    main()
