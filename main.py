"""
매주 월요일, 지정된 경쟁사들의 대형 플랫폼(오늘의집/스마트스토어/쿠팡) 판매 현황을
Claude API(도메인 제한 웹 검색)로 조사하고, Slack으로 전송하는 스크립트.

- 홈페이지 동향은 조사하지 않음 (실질적인 판매는 대형 플랫폼에서 이루어지기 때문)
- 각 플랫폼의 상품별 가격/리뷰수/평점을 조사하고, 지난주 대비 변화(신규 상품, 리뷰 증가폭)를 추적함

필요한 환경변수:
- ANTHROPIC_API_KEY : Anthropic API 키
- SLACK_WEBHOOK_URL : Slack Incoming Webhook URL
"""

import os
import sys
import json
import re
import datetime
import requests

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-5"
SNAPSHOT_PATH = "data/last_snapshot.json"

# 추적하고 싶은 경쟁사 이름 (자유롭게 추가/삭제 가능)
COMPANIES = ["폴인퍼니", "영가구", "에이비퍼니처", "위드퍼니처"]

# 조사 대상 대형 플랫폼 도메인
PLATFORM_DOMAINS = [
    "store.ohou.se",
    "m.ohou.se",
    "smartstore.naver.com",
    "coupang.com",
    "shop.coupang.com",
    "m.coupang.com",
]


def check_env():
    missing = []
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if not SLACK_WEBHOOK_URL:
        missing.append("SLACK_WEBHOOK_URL")
    if missing:
        print(f"[오류] 다음 환경변수가 설정되지 않았습니다: {', '.join(missing)}")
        sys.exit(1)


def get_platform_products(company: str) -> list:
    """오늘의집/스마트스토어/쿠팡에서 상품별 가격/리뷰수/평점을 JSON으로 추출."""
    prompt = f"""
가구 브랜드 '{company}'를 아래 대형 이커머스 플랫폼 안에서만 웹 검색해줘:
- 오늘의집 (store.ohou.se, m.ohou.se)
- 네이버 스마트스토어 (smartstore.naver.com)
- 쿠팡 (coupang.com, shop.coupang.com, m.coupang.com)

각 플랫폼에서 확인 가능한 '{company}' 상품 중 상위 3~5개씩(플랫폼당) 정보를 정리해줘.
다른 설명 없이, 순수 JSON으로만 응답해 (마크다운 코드블록도 쓰지 마):

{{"products": [{{"platform": "오늘의집 또는 스마트스토어 또는 쿠팡", "name": "상품명", "price": 숫자또는null, "review_count": 숫자또는null, "rating": 숫자또는null}}]}}

- price는 원 단위 숫자만 입력 (콤마/원 표시 제외)
- review_count는 숫자만 입력
- 확인할 수 없는 값은 null로 입력
- 특정 플랫폼에서 그 브랜드를 못 찾으면 해당 플랫폼은 생략해도 됨
- 상품을 하나도 찾을 수 없으면 {{"products": []}}로 응답
"""
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
                "max_tokens": 1200,
                "temperature": 0.2,
                "messages": [{"role": "user", "content": prompt}],
                "tools": [
                    {
                        "type": "web_search_20250305",
                        "name": "web_search",
                        "max_uses": 6,
                        "allowed_domains": PLATFORM_DOMAINS,
                    },
                ],
            },
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        texts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        text = "\n".join(texts).strip()
        cleaned = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
        parsed = json.loads(cleaned)
        return parsed.get("products", [])
    except Exception as e:
        print(f"[경고] '{company}' 상품 데이터 조사 오류: {e}")
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


def product_key(p: dict) -> str:
    """플랫폼+상품명으로 고유 키 생성 (같은 이름이 여러 플랫폼에 있을 수 있어서)."""
    return f"{p.get('platform', '?')}::{p.get('name', '?')}"


def compare_products(prev_products: list, curr_products: list) -> list:
    """지난 주 대비 변화(신규 상품, 리뷰 증가, 가격 변동)를 문자열 리스트로 반환."""
    changes = []
    prev_by_key = {product_key(p): p for p in prev_products if p.get("name")}

    for p in curr_products:
        if not p.get("name"):
            continue
        key = product_key(p)
        prev = prev_by_key.get(key)
        platform = p.get("platform", "?")

        if prev is None:
            changes.append(f"🆕 신규 상품 등장 [{platform}]: {p['name']}")
            continue

        prev_reviews, curr_reviews = prev.get("review_count"), p.get("review_count")
        if isinstance(prev_reviews, (int, float)) and isinstance(curr_reviews, (int, float)):
            delta = curr_reviews - prev_reviews
            if delta > 0:
                changes.append(
                    f"📈 리뷰 증가 [{platform}]: {p['name']} ({prev_reviews} → {curr_reviews}, +{delta})"
                )

        prev_price, curr_price = prev.get("price"), p.get("price")
        if isinstance(prev_price, (int, float)) and isinstance(curr_price, (int, float)) and prev_price != curr_price:
            arrow = "⬇️" if curr_price < prev_price else "⬆️"
            changes.append(
                f"{arrow} 가격 변동 [{platform}]: {p['name']} ({prev_price:,.0f}원 → {curr_price:,.0f}원)"
            )

    return changes


def build_company_report_text(company: str, info: dict, today: str) -> str:
    lines = [f"*:mag: {company} 주간 판매 동향 ({today})*", ""]

    if info["products"]:
        lines.append("_확인된 상품:_")
        for p in info["products"]:
            name = p.get("name", "?")
            platform = p.get("platform", "?")
            detail = []
            if p.get("price"):
                detail.append(f"{p['price']:,.0f}원")
            if p.get("rating"):
                detail.append(f"⭐{p['rating']}")
            if p.get("review_count") is not None:
                detail.append(f"리뷰 {p['review_count']}개")
            lines.append(f"  • [{platform}] {name}" + (f" ({' / '.join(detail)})" if detail else ""))
    else:
        lines.append("이번 주 확인된 상품 정보 없음")

    if info["changes"]:
        lines.append("")
        lines.append("_전주 대비 변화:_")
        for c in info["changes"]:
            lines.append(f"  • {c}")
    elif info["products"]:
        lines.append("")
        lines.append("_전주 대비 변화: 특이사항 없음_")

    return "\n".join(lines)


def split_into_chunks(text: str, limit: int = 2000) -> list:
    """Slack 메시지 길이 제한에 맞춰 분할. 긴 줄 하나도 강제로 쪼갠다."""
    chunks, current = [], ""

    def flush():
        nonlocal current
        if current:
            chunks.append(current)
            current = ""

    for line in text.split("\n"):
        while len(line) > limit:
            piece, line = line[:limit], line[limit:]
            flush()
            chunks.append(piece)

        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            flush()
            current = line
        else:
            current = candidate

    flush()
    return chunks if chunks else [text]


def send_text_to_slack(text: str):
    """긴 텍스트는 자동으로 여러 메시지로 분할해서 전송."""
    chunks = split_into_chunks(text)
    total = len(chunks)

    for idx, chunk in enumerate(chunks, start=1):
        payload_text = chunk if total == 1 else f"{chunk}\n\n_({idx}/{total})_"
        resp = requests.post(SLACK_WEBHOOK_URL, json={"text": payload_text}, timeout=30)
        resp.raise_for_status()

    return total


def main():
    check_env()
    prev_snapshot = load_snapshot()
    new_snapshot = {}
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    for company in COMPANIES:
        print(f"조사 중: {company}")
        products = get_platform_products(company)
        changes = compare_products(prev_snapshot.get(company, []), products)
        new_snapshot[company] = products

        report_text = build_company_report_text(company, {"products": products, "changes": changes}, today)
        sent = send_text_to_slack(report_text)
        print(f"  → '{company}' Slack 전송 완료 ({sent}개 메시지)")

    save_snapshot(new_snapshot)


if __name__ == "__main__":
    main()
