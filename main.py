"""
매주 월요일, 지정된 경쟁사들의 대형 플랫폼(오늘의집/쿠팡/네이버) 판매 현황을
Claude API(플랫폼별 실제 스토어 URL 기반 웹 검색)로 조사하고, Slack으로 전송하는 스크립트.

- 홈페이지 동향은 조사하지 않음
- 각 회사의 실제 스토어 URL을 지정해서 검색 정확도를 높임
- 각 플랫폼의 상품별 가격/리뷰수/평점을 조사하고, 지난주 대비 변화(신규 상품, 리뷰 증가폭)를 추적함
- 회사별로 Slack 메시지를 따로 전송함

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
from urllib.parse import urlparse

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-5"
SNAPSHOT_PATH = "data/last_snapshot.json"

# 각 회사의 실제 스토어 URL (오늘의집 / 쿠팡 / 네이버)
COMPANY_SOURCES = {
    "폴인퍼니": {
        "오늘의집": "https://store.ohou.se/brands/13004",
        "쿠팡": "https://shop.coupang.com/fallinfuni",
        "네이버": "https://brand.naver.com/fallinfuni",
    },
    "영가구": {
        "오늘의집": "https://store.ohou.se/brands/3554",
        "쿠팡": "https://shop.coupang.com/younggagu",
        "네이버": "https://brand.naver.com/younggagu",
    },
    "에이비퍼니처": {
        "오늘의집": "https://store.ohou.se/brands/6360",
        "쿠팡": "https://shop.coupang.com/abfurniture",
        "네이버": "https://brand.naver.com/abfurniture",
    },
    "위드퍼니처": {
        "오늘의집": "https://store.ohou.se/brands/1061",
        "쿠팡": "https://shop.coupang.com/A00061777",
        "네이버": "https://smartstore.naver.com/withfurniture",
    },
}


def check_env():
    missing = []
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if not SLACK_WEBHOOK_URL:
        missing.append("SLACK_WEBHOOK_URL")
    if missing:
        print(f"[오류] 다음 환경변수가 설정되지 않았습니다: {', '.join(missing)}")
        sys.exit(1)


def get_platform_products(company: str, platform: str, url: str) -> list:
    """지정된 플랫폼의 실제 스토어 URL을 기준으로 상품별 가격/리뷰수/평점을 JSON으로 추출."""
    domain = urlparse(url).netloc
    prompt = f"""
가구 브랜드 '{company}'의 '{platform}' 스토어 주소는 아래와 같아:
{url}

이 스토어({domain} 도메인 내)를 웹 검색으로 조사해서, 확인 가능한 상위 3~5개 상품의 정보를 정리해줘.
다른 설명이나 인사말 없이, 순수 JSON 한 개만 응답해 (마크다운 코드블록 표시도 쓰지 마):

{{"products": [{{"name": "상품명", "price": 숫자또는null, "review_count": 숫자또는null, "rating": 숫자또는null}}]}}

- price는 원 단위 숫자만 입력 (콤마/원 표시 제외)
- review_count는 숫자만 입력
- 확인할 수 없는 값은 null로 입력
- 검색해도 상품 정보를 찾을 수 없으면 {{"products": []}}로 응답
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
                "max_tokens": 1500,
                "messages": [{"role": "user", "content": prompt}],
                "tools": [
                    {
                        "type": "web_search_20250305",
                        "name": "web_search",
                        "max_uses": 3,
                        "allowed_domains": [domain],
                    },
                ],
            },
            timeout=120,
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
        return parsed.get("products", [])

    except requests.exceptions.HTTPError as e:
        body = e.response.text[:500] if e.response is not None else ""
        print(f"[경고] '{company}' / '{platform}' HTTP 오류: {e} | 서버 응답: {body}")
        return []
    except Exception as e:
        preview = text[:300]
        print(f"[경고] '{company}' / '{platform}' 조사 오류: {e} | 응답 길이: {len(text)} | 응답 일부: {preview}")
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


def compare_products(prev_by_platform: dict, curr_by_platform: dict) -> list:
    """지난 주 대비 변화(신규 상품, 리뷰 증가, 가격 변동)를 문자열 리스트로 반환."""
    changes = []

    for platform, curr_products in curr_by_platform.items():
        prev_products = prev_by_platform.get(platform, [])
        prev_by_name = {p.get("name"): p for p in prev_products if p.get("name")}

        for p in curr_products:
            name = p.get("name")
            if not name:
                continue
            prev = prev_by_name.get(name)

            if prev is None:
                changes.append(f"🆕 신규 상품 등장 [{platform}]: {name}")
                continue

            prev_reviews, curr_reviews = prev.get("review_count"), p.get("review_count")
            if isinstance(prev_reviews, (int, float)) and isinstance(curr_reviews, (int, float)):
                delta = curr_reviews - prev_reviews
                if delta > 0:
                    changes.append(f"📈 리뷰 증가 [{platform}]: {name} ({prev_reviews} → {curr_reviews}, +{delta})")

            prev_price, curr_price = prev.get("price"), p.get("price")
            if isinstance(prev_price, (int, float)) and isinstance(curr_price, (int, float)) and prev_price != curr_price:
                arrow = "⬇️" if curr_price < prev_price else "⬆️"
                changes.append(f"{arrow} 가격 변동 [{platform}]: {name} ({prev_price:,.0f}원 → {curr_price:,.0f}원)")

    return changes


def build_company_report_text(company: str, by_platform: dict, changes: list, today: str) -> str:
    lines = [f"*:mag: {company} 주간 판매 동향 ({today})*", ""]

    for platform in COMPANY_SOURCES[company]:
        products = by_platform.get(platform, [])
        lines.append(f"_{platform}:_")
        if products:
            for p in products:
                name = p.get("name", "?")
                detail = []
                if p.get("price"):
                    detail.append(f"{p['price']:,.0f}원")
                if p.get("rating"):
                    detail.append(f"⭐{p['rating']}")
                if p.get("review_count") is not None:
                    detail.append(f"리뷰 {p['review_count']}개")
                lines.append(f"  • {name}" + (f" ({' / '.join(detail)})" if detail else ""))
        else:
            lines.append("  검색 결과에서 상품을 찾지 못했습니다.")
        lines.append("")

    if changes:
        lines.append("_전주 대비 변화:_")
        for c in changes:
            lines.append(f"  • {c}")
    else:
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


def send_text_to_slack(text: str) -> int:
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

    for company, platforms in COMPANY_SOURCES.items():
        print(f"조사 중: {company}")
        by_platform = {}
        for platform, url in platforms.items():
            print(f"  - {platform} 확인 중... ({url})")
            by_platform[platform] = get_platform_products(company, platform, url)

        prev_by_platform = prev_snapshot.get(company, {})
        if not isinstance(prev_by_platform, dict):
            prev_by_platform = {}

        changes = compare_products(prev_by_platform, by_platform)
        new_snapshot[company] = by_platform

        report_text = build_company_report_text(company, by_platform, changes, today)
        sent = send_text_to_slack(report_text)
        print(f"  → '{company}' Slack 전송 완료 ({sent}개 메시지)")

    save_snapshot(new_snapshot)


if __name__ == "__main__":
    main()
