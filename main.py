"""
매주 월요일, 지정된 경쟁사들의 최근 시장 동향(신제품/가격/리뷰 등)을
Claude API(웹 페이지 직접 확인 + 웹 검색)로 조사·요약하고, Slack으로 전송하는 스크립트.

필요한 환경변수:
- ANTHROPIC_API_KEY : Anthropic API 키
- SLACK_WEBHOOK_URL : Slack Incoming Webhook URL
"""

import os
import sys
import datetime
import requests

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-5"

# 각 회사의 실제 공식몰 / 오늘의집 페이지 (일반 이름 검색 대신 이 페이지들을 직접 확인합니다)
COMPANY_SOURCES = {
    "폴인퍼니": {
        "official": "https://fallinfuni.com/",
        "ohouse": "https://store.ohou.se/brands/13004",
    },
    "영가구": {
        "official": "https://younggagu.com/",
        "ohouse": "https://store.ohou.se/brands/3554",
    },
    "에이비퍼니처": {
        "official": "https://abfurniture.co.kr/",
        "ohouse": "https://m.ohou.se/productions/feed?query=%EC%97%90%EC%9D%B4%EB%B9%84%ED%8D%BC%EB%8B%88%EC%B2%98",
    },
    "위드퍼니처": {
        "official": "https://withfurniture.com/",
        "ohouse": "https://store.ohou.se/brands/1061",
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


def get_company_summary(company: str, sources: dict) -> str:
    """공식몰/오늘의집 페이지를 직접 확인(web_fetch)해서 최근 동향을 조사/요약."""
    official = sources["official"]
    ohouse = sources["ohouse"]
    domain = official.split("//")[-1].split("/")[0]

    prompt = f"""
아래는 가구 브랜드 '{company}'의 실제 판매 채널 URL이야:
- 공식 홈페이지/쇼핑몰: {official}
- 오늘의집 페이지: {ohouse}

이 두 페이지를 직접 열어서(web_fetch) 확인하고, 필요하면 관련 웹 검색도 추가로 활용해서
다음 내용을 조사해줘:
1. 신제품 출시나 새로운 상품 라인업
2. 가격 변동, 할인/프로모션, 쿠폰 이벤트
3. 눈에 띄는 고객 리뷰/평점 변화나 인기 상품

아래 형식을 반드시 지켜서 한국어로 간결하게 정리해줘:
- 불필요한 서론 없이 바로 항목만 작성
- 페이지 확인 결과 특별한 변화가 없으면 "이번 주 특이 동향 없음" 한 줄만 작성
- 각 항목은 1줄로, 최대 4개까지
- 확인이 안 되거나 접근할 수 없는 페이지가 있으면 그 사실도 짧게 언급
"""
    try:
        response = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "web-fetch-2025-09-10",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 700,
                "messages": [{"role": "user", "content": prompt}],
                "tools": [
                    {
                        "type": "web_fetch_20250910",
                        "name": "web_fetch",
                        "max_uses": 5,
                    },
                    {
                        "type": "web_search_20250305",
                        "name": "web_search",
                        "max_uses": 3,
                        "allowed_domains": [domain, "store.ohou.se", "m.ohou.se"],
                    },
                ],
            },
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()

        texts = [block["text"] for block in data.get("content", []) if block.get("type") == "text"]
        summary = "\n".join(texts).strip()
        return summary if summary else "이번 주 특이 동향 없음"

    except Exception as e:
        print(f"[경고] '{company}' 조사 중 오류 발생: {e}")
        return "⚠️ 조사 중 오류가 발생했습니다. (로그 확인 필요)"


def build_slack_message(summaries: dict) -> dict:
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    lines = [f"*:mag: 경쟁사 주간 동향 리포트 ({today})*", ""]
    for company, summary in summaries.items():
        lines.append(f"*[{company}]*")
        lines.append(summary)
        lines.append("")
    return {"text": "\n".join(lines)}


def send_to_slack(message: dict):
    resp = requests.post(SLACK_WEBHOOK_URL, json=message, timeout=30)
    resp.raise_for_status()
    print("Slack 전송 완료")


def main():
    check_env()
    summaries = {}
    for company, sources in COMPANY_SOURCES.items():
        print(f"조사 중: {company}")
        summaries[company] = get_company_summary(company, sources)

    message = build_slack_message(summaries)
    send_to_slack(message)


if __name__ == "__main__":
    main()
