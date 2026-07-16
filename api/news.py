# -*- coding: utf-8 -*-
"""
Vercel Python 서버리스 함수: GET /api/news
쿼리: corp_name
네이버 뉴스 검색 API로 회사명 관련 최근 뉴스를 가져온다.
감사 착수 전 클라이언트 관련 이슈(소송, 실적, 오너 리스크 등)를
빠르게 스캔하는 용도의 참고 자료이며, 보도 내용의 사실관계를 보증하지 않는다.
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote
import html
import json
import os
import re
import requests

NAVER_URL = "https://openapi.naver.com/v1/search/news.json"
DISPLAY_COUNT = 6


def strip_html(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def fetch_news(client_id, client_secret, query):
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    params = {"query": query, "display": DISPLAY_COUNT, "sort": "date"}
    resp = requests.get(NAVER_URL, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def handle_request(query, client_id, client_secret):
    """쿼리 파라미터(dict[str, list[str]])를 받아 (status, payload)를 반환. 로컬 dev 서버와 공유."""
    corp_name = (query.get("corp_name") or [""])[0].strip()
    if not corp_name:
        return 400, {"error": "corp_name이 필요합니다."}
    if not client_id or not client_secret:
        return 500, {"error": "서버에 NAVER_CLIENT_ID/NAVER_CLIENT_SECRET 환경변수가 설정되어 있지 않습니다."}

    try:
        data = fetch_news(client_id, client_secret, corp_name)
    except requests.RequestException as e:
        return 502, {"error": f"네이버 뉴스 검색 중 오류가 발생했습니다: {e}"}

    items = []
    for item in data.get("items", []):
        items.append({
            "title": strip_html(item.get("title")),
            "summary": strip_html(item.get("description")),
            "link": item.get("originallink") or item.get("link"),
            "pubDate": item.get("pubDate"),
        })

    return 200, {"corp_name": corp_name, "items": items}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        status, payload = handle_request(
            query,
            os.environ.get("NAVER_CLIENT_ID"),
            os.environ.get("NAVER_CLIENT_SECRET"),
        )
        self._send_json(payload, status)

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
