"""
매주 월요일, 지정된 경쟁사들의 최근 동향과 인기 상품/리뷰 변화를
Claude API(도메인 제한 웹 검색)로 조사하고, Slack으로 전송하는 스크립트.

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

# 각 회사의 실제 공식몰 / 오늘의집 페이지
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


def call_claude(prompt: str, allowed_domains: list, max_tokens: int = 700) -> str:
    """도메인 제한 웹 검색(web_search) + 보조로 web_fetch를 함께 사용."""
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
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 4,
                    "allowed_domains": allowed_domains,
                },
                {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 3},
            ],
        },
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()
    texts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    return "\n".join(texts).strip()


def get_trend_summary(company: str, sources: dict) -> str:
    """공식 홈페이지 관련 검색으로 신제품/프로모션 등 동향을 텍스트로 요약."""
    official = sources["official"]
    domain = official.split("//")[-1].split("/")[0]
    prompt = f"""
가구 브랜드 '{company}'(공식 홈페이지: {official})에 대해
{domain} 도메인을 중심으로 웹 검색해서, 최근 신제품, 프로모션/할인, 공지사항 등
눈에 띄는 소식을 조사해줘. 직접 접속(fetch)이 안 되면 검색 결과만으로 판단해도 돼.

아래 형식을 지켜서 한국어로 간결하게 정리해줘:
- 불필요한 서론 없이 바로 항목만 작성
- 특별한 변화가 없으면 "이번 주 특이 동향 없음" 한 줄만 작성
- 최대 3줄
"""
    try:
        text = call_claude(prompt, allowed_domains=[domain], max_tokens=400)
        return text if text else "이번 주 특이 동향 없음"
    except Exception as e:
        print(f"[경고] '{company}' 동향 조사 오류: {e}")
        return "⚠️ 동향 조사 중 오류가 발생했습니다."


def get_product_data(company: str, sources: dict) -> list:
    """오늘의집 관련 검색에서 상품별 가격/리뷰수/평점을 JSON으로 추출."""
    ohouse = sources["ohouse"]
    prompt = f"""
가구 브랜드 '{company}'의 오늘의집 페이지({ohouse})를 웹 검색으로 조사해줘
(store.ohou.se, m.ohou.se 도메인 결과를 활용). 직접 접속(fetch)이 되면 그 결과도 활용해줘.

이 브랜드의 상품 중 확인 가능한 상위 5~8개의 정보를 정리해줘.
다른 설명 없이, 순수 JSON으로만 응답해 (마크다운 코드블록도 쓰지 마):

{{"products": [{{"name": "상품명", "price": 숫자또는null, "review_count": 숫자또는null, "rating": 숫자또는null}}]}}

- price는 원 단위 숫자만 입력 (콤마/원 표시 제외)
- review_count는 숫자만 입력
- 확인할 수 없는 값은 null로 입력
- 상품을 하나도 찾을 수 없으면 {{"products": []}}로 응답
"""
    try:
