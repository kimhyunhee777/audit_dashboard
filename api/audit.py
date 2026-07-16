# -*- coding: utf-8 -*-
"""
Vercel Python 서버리스 함수: GET /api/audit
쿼리: corp_code, corp_name, start(연도), end(연도)

DART Open API로 지정 회사의 start~end 연도 재무제표를 조회하여
1) 재무비율(회전율, 발생액비율 등) 이상징후 스크리닝에 필요한 지표를 계산하고
2) Altman Z''-Score(비상장·이머징마켓용 부실위험 예측모형)를 계산하며
3) 같은 기간 정정공시(사업·반기·분기보고서 정정) 이력을 조회한다.

주의: 본 도구는 학습·포트폴리오 목적의 1차 스크리닝 참고자료이며,
실제 감사 절차나 부정 판단, 부도예측의 근거로 사용할 수 없다.
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import os
import requests

BASE_URL = "https://opendart.fss.or.kr/api"
REPORT_CODE_ANNUAL = "11011"
MAX_YEAR_SPAN = 7

# 계정과목명 매칭용 별칭 (재무상태표/손익계산서/현금흐름표 통합 스캔)
# 주의: 당기 손실(적자)인 회사는 DART 공시에서 "OO이익" 대신 "OO손익"/"OO손실"로
# 계정명이 바뀌는 경우가 있어 별칭에 함께 포함한다. (예: 삼성SDI 2025 영업손익)
TARGET_ACCOUNTS = {
    "매출액": ["매출액", "매출", "수익(매출액)", "영업수익"],
    "매출원가": ["매출원가"],
    "영업이익": ["영업이익", "영업이익(손실)", "영업손익", "영업손실"],
    "법인세비용차감전순이익": [
        "법인세비용차감전순이익", "법인세비용차감전순이익(손실)",
        "법인세비용차감전계속사업이익", "법인세비용차감전계속사업이익(손실)",
        "법인세비용차감전순손익", "법인세비용차감전순손실",
        "법인세비용차감전계속사업손익", "법인세비용차감전계속사업손실",
    ],
    "당기순이익": [
        "당기순이익", "당기순이익(손실)", "분기순이익", "분기순이익(손실)",
        "당기순손익", "당기순손실", "분기순손익", "분기순손실",
    ],
    "매출채권": ["매출채권", "매출채권및기타채권", "매출채권및기타유동채권"],
    "재고자산": ["재고자산"],
    "유동자산": ["유동자산"],
    "유동부채": ["유동부채"],
    "이익잉여금": ["이익잉여금", "이익잉여금(결손금)", "미처리결손금"],
    "자산총계": ["자산총계", "자산 합계", "자산합계"],
    "부채총계": ["부채총계", "부채 합계", "부채합계"],
    "자본총계": ["자본총계", "자본 합계", "자본합계"],
    "영업활동현금흐름": [
        "영업활동으로인한현금흐름", "영업활동현금흐름",
        "영업활동으로인한순현금흐름", "영업활동으로인한현금흐름(유출)",
    ],
}


def _normalize(name):
    """계정과목명 비교용 정규화: 공백만 제거(같은 계정을 회사마다 "OO 합계"/"OO합계"처럼
    띄어쓰기만 다르게 공시하는 경우가 많아, 정확히 일치하는 이름을 못 찾는 문제를 줄인다)."""
    return (name or "").replace(" ", "").replace("　", "").strip()


TARGET_ACCOUNTS_NORMALIZED = {
    metric: {_normalize(alias) for alias in aliases}
    for metric, aliases in TARGET_ACCOUNTS.items()
}


def fetch_financial_statement(api_key, corp_code, year):
    """연결(CFS) 우선, 없으면 개별(OFS) 재시도. (rows, fs_div) 반환."""
    for fs_div in ("CFS", "OFS"):
        params = {
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": REPORT_CODE_ANNUAL,
            "fs_div": fs_div,
        }
        resp = requests.get(f"{BASE_URL}/fnlttSinglAcntAll.json", params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "000":
            return data.get("list", []), fs_div
    return [], None


def extract_metrics(rows):
    result = {k: None for k in TARGET_ACCOUNTS}
    for row in rows:
        account_nm = _normalize(row.get("account_nm"))
        amount_str = (row.get("thstrm_amount") or "").replace(",", "").strip()
        if not amount_str:
            continue
        try:
            amount = float(amount_str)
        except ValueError:
            continue
        for metric, aliases in TARGET_ACCOUNTS_NORMALIZED.items():
            if result[metric] is not None:
                continue
            if account_nm in aliases:
                result[metric] = amount
    return result


# Altman Z''-Score (Emerging Markets Score, Altman·Hartzell·Peck 1995) 존 판정 기준
def zscore_zone(z):
    if z > 2.6:
        return "안전"
    if z >= 1.1:
        return "회색지대"
    return "위험"


def compute_zscore(metrics):
    """Altman Z''-Score: 시가총액 대신 자본총계(장부가치)를 쓰는 비상장·이머징마켓용 변형식.
    Z'' = 6.56*X1 + 3.26*X2 + 6.72*X3 + 1.05*X4 + 3.25
      X1 = (유동자산-유동부채)/자산총계, X2 = 이익잉여금/자산총계,
      X3 = 영업이익(EBIT 근사)/자산총계, X4 = 자본총계(장부가)/부채총계
    """
    assets = metrics.get("자산총계")
    current_assets = metrics.get("유동자산")
    current_liabilities = metrics.get("유동부채")
    retained_earnings = metrics.get("이익잉여금")
    ebit = metrics.get("영업이익")
    equity = metrics.get("자본총계")
    liabilities = metrics.get("부채총계")

    required = [assets, current_assets, current_liabilities, retained_earnings, ebit, equity, liabilities]
    if any(v is None for v in required) or not assets or not liabilities:
        return None

    x1 = (current_assets - current_liabilities) / assets
    x2 = retained_earnings / assets
    x3 = ebit / assets
    x4 = equity / liabilities

    z = 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4 + 3.25
    return {"score": round(z, 2), "zone": zscore_zone(z)}


def build_record(corp_name, corp_code, year, metrics, fs_div, prior_metrics):
    revenue = metrics.get("매출액")
    cogs = metrics.get("매출원가")
    receivables = metrics.get("매출채권")
    inventory = metrics.get("재고자산")
    net_income = metrics.get("당기순이익")
    assets = metrics.get("자산총계")
    liabilities = metrics.get("부채총계")
    equity = metrics.get("자본총계")
    cfo = metrics.get("영업활동현금흐름")

    record = {
        "회사명": corp_name,
        "corp_code": corp_code,
        "연도": year,
        "재무제표구분": "연결" if fs_div == "CFS" else "개별",
    }
    record.update(metrics)

    record["영업이익률(%)"] = (
        round(metrics.get("영업이익") / revenue * 100, 2)
        if revenue and metrics.get("영업이익") is not None else None
    )
    record["순이익률(%)"] = (
        round(net_income / revenue * 100, 2) if revenue and net_income is not None else None
    )
    record["부채비율(%)"] = (
        round(liabilities / equity * 100, 2) if equity and liabilities is not None else None
    )

    # 회전율: 기말잔액 기준 단순화(평잔 미사용) - 스크리닝용 근사치
    record["매출채권회전율"] = (
        round(revenue / receivables, 2) if revenue and receivables else None
    )
    record["재고자산회전율"] = (
        round(cogs / inventory, 2) if cogs and inventory else None
    )

    # 발생액비율(Accruals Ratio) = (당기순이익 - 영업활동현금흐름) / 자산총계
    record["발생액비율(%)"] = (
        round((net_income - cfo) / assets * 100, 2)
        if assets and net_income is not None and cfo is not None else None
    )

    record["zscore"] = compute_zscore(metrics)

    # 전기 대비 회전율 급변 플래그
    flags = []
    if prior_metrics:
        def pct_change(cur, prev):
            if cur is None or prev in (None, 0):
                return None
            return (cur - prev) / abs(prev) * 100

        prior_rec_ar_turn = prior_metrics.get("매출채권회전율")
        prior_rec_inv_turn = prior_metrics.get("재고자산회전율")
        ar_chg = pct_change(record["매출채권회전율"], prior_rec_ar_turn)
        inv_chg = pct_change(record["재고자산회전율"], prior_rec_inv_turn)

        if ar_chg is not None and abs(ar_chg) >= 30:
            flags.append({
                "type": "매출채권회전율 급변",
                "detail": f"전기 대비 {ar_chg:+.1f}% 변동",
                "severity": "high" if abs(ar_chg) >= 50 else "medium",
            })
        if inv_chg is not None and abs(inv_chg) >= 30:
            flags.append({
                "type": "재고자산회전율 급변",
                "detail": f"전기 대비 {inv_chg:+.1f}% 변동",
                "severity": "high" if abs(inv_chg) >= 50 else "medium",
            })

    if record["발생액비율(%)"] is not None and record["발생액비율(%)"] >= 10:
        flags.append({
            "type": "발생액비율 과다",
            "detail": f"자산총계 대비 {record['발생액비율(%)']:.1f}% (순이익이 영업현금흐름을 상회)",
            "severity": "high" if record["발생액비율(%)"] >= 15 else "medium",
        })

    if record["zscore"] is not None and record["zscore"]["zone"] == "위험":
        flags.append({
            "type": "부실위험(Z-Score)",
            "detail": f"Altman Z''-Score {record['zscore']['score']} (위험 구간, 1.1 미만)",
            "severity": "high",
        })

    record["flags"] = flags
    return record


def fetch_correction_history(api_key, corp_code, start_year, end_year):
    """정기공시(사업·반기·분기보고서) 중 '정정' 보고서 이력을 조회한다."""
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bgn_de": f"{start_year}0101",
        "end_de": f"{end_year}1231",
        "pblntf_ty": "A",  # 정기공시
        "page_count": "100",
    }
    resp = requests.get(f"{BASE_URL}/list.json", params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") not in ("000", "013"):  # 013 = 조회된 데이터 없음
        return []

    items = []
    for row in data.get("list", []):
        report_nm = row.get("report_nm") or ""
        if "정정" not in report_nm:
            continue
        rcept_no = row.get("rcept_no")
        items.append({
            "report_nm": report_nm,
            "rcept_dt": row.get("rcept_dt"),
            "flr_nm": row.get("flr_nm"),
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
        })
    items.sort(key=lambda x: x.get("rcept_dt") or "", reverse=True)
    return items


def handle_request(query, api_key):
    """쿼리 파라미터(dict[str, list[str]])를 받아 (status, payload)를 반환. 로컬 dev 서버와 공유."""
    corp_code = (query.get("corp_code") or [""])[0].strip()
    corp_name = (query.get("corp_name") or [""])[0].strip() or corp_code

    try:
        start_year = int((query.get("start") or [""])[0])
        end_year = int((query.get("end") or [""])[0])
    except ValueError:
        return 400, {"error": "start/end 연도가 필요합니다."}

    if not corp_code:
        return 400, {"error": "corp_code가 필요합니다."}
    if not api_key:
        return 500, {"error": "서버에 DART_API_KEY 환경변수가 설정되어 있지 않습니다."}
    if end_year < start_year or end_year - start_year > MAX_YEAR_SPAN:
        return 400, {"error": f"조회 기간은 최대 {MAX_YEAR_SPAN + 1}개년까지 가능합니다."}

    records = []
    prior_metrics = None
    try:
        for year in range(start_year, end_year + 1):
            rows, fs_div = fetch_financial_statement(api_key, corp_code, year)
            if not rows:
                continue
            metrics = extract_metrics(rows)
            record = build_record(corp_name, corp_code, year, metrics, fs_div, prior_metrics)
            records.append(record)
            prior_metrics = record
    except requests.RequestException as e:
        return 502, {"error": f"DART 조회 중 오류가 발생했습니다: {e}"}

    if not records:
        return 200, {"corp_code": corp_code, "corp_name": corp_name, "records": [], "corrections": []}

    try:
        corrections = fetch_correction_history(api_key, corp_code, start_year, end_year)
    except requests.RequestException:
        corrections = []

    return 200, {
        "corp_code": corp_code,
        "corp_name": corp_name,
        "records": records,
        "corrections": corrections,
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        status, payload = handle_request(query, os.environ.get("DART_API_KEY"))
        self._send_json(payload, status)

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
