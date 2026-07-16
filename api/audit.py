# -*- coding: utf-8 -*-
"""
Vercel Python 서버리스 함수: GET /api/audit
쿼리: corp_code, corp_name, start(연도), end(연도)

DART Open API로 지정 회사의 start~end 연도 재무제표를 조회하여
1) 재무비율(회전율·유동성·수익성·발생액비율 등) 스크리닝 지표를 계산하고
2) 계정과목별 전기 대비 증감률을 매출증가율과 비교해 이상 계정을 탐지하며
3) Altman Z''-Score(비상장·이머징마켓용 부실위험 예측모형)를 계산하고
4) 정정공시·유상증자·전환사채·최대주주변경·횡령배임 등 공시 타임라인을 조회하며
5) 위 신호들을 규칙기반(rule-based)으로 종합해 "이번 감사 TOP5 위험계정"과
   계정별 감사절차 추천을 생성한다.

주의: 본 도구는 학습·포트폴리오 목적의 1차 스크리닝 참고자료다. 코멘트·감사절차 추천은
모두 사전에 정의한 규칙(rule)에 따라 생성되며(생성형 AI 아님), 실제 감사 절차나
부정 판단, 부도예측의 근거로 사용할 수 없다.
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import os
import requests

BASE_URL = "https://opendart.fss.or.kr/api"
REPORT_CODE_ANNUAL = "11011"
MAX_YEAR_SPAN = 7

# DART 표준계정코드(account_id, IFRS 택소노미) 매칭 - 1순위.
# 같은 개념이라도 회사마다 계정명 표기가 "매출액"/"매출", "영업이익"/"영업손익",
# "자산총계"/"자산 합계"처럼 제각각이지만, account_id는 XBRL 표준 코드라 동일하다.
# (2015년 이후 사업보고서부터 DART가 XBRL 태깅을 지원하므로 이 API가 다루는
# 전체 구간에서 사실상 신뢰 가능하다.)
ACCOUNT_ID_ALIASES = {
    "매출액": ["ifrs-full_Revenue"],
    "매출원가": ["ifrs-full_CostOfSales"],
    "영업이익": ["dart_OperatingIncomeLoss"],
    "법인세비용차감전순이익": ["ifrs-full_ProfitLossBeforeTax"],
    "당기순이익": ["ifrs-full_ProfitLoss"],
    "매출채권": [
        "ifrs-full_TradeAndOtherCurrentReceivables", "ifrs-full_CurrentTradeReceivables",
        "dart_ShortTermTradeReceivable", "dart_ShortTermTradeReceivables",
    ],
    "재고자산": ["ifrs-full_Inventories"],
    "기타채권": ["dart_CurrentNontradeReceivables", "dart_OtherCurrentReceivables", "dart_OtherReceivables"],
    "매입채무": [
        "ifrs-full_TradeAndOtherCurrentPayables", "ifrs-full_TradeAndOtherCurrentPayablesToTradeSuppliers",
        "dart_ShortTermTradePayables", "dart_ShortTermTradePayable",
    ],
    "유동자산": ["ifrs-full_CurrentAssets"],
    "유동부채": ["ifrs-full_CurrentLiabilities"],
    "이익잉여금": ["ifrs-full_RetainedEarnings"],
    "자산총계": ["ifrs-full_Assets"],
    "부채총계": ["ifrs-full_Liabilities"],
    "자본총계": ["ifrs-full_Equity"],
    "영업활동현금흐름": ["ifrs-full_CashFlowsFromUsedInOperatingActivities"],
}
NON_STANDARD_ACCOUNT_ID = "-표준계정코드 미사용-"

# 계정과목명 매칭용 별칭 (표준코드가 없는 옛 공시·비표준 캡션을 위한 2순위 폴백)
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
    "기타채권": ["기타채권", "기타유동채권", "기타비유동채권"],
    "매입채무": ["매입채무", "매입채무및기타채무", "매입채무및기타유동채무"],
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

# 계정 증감률 스크리닝 대상 (매출 대비 이상 급증 여부를 본다)
GROWTH_ACCOUNTS = ["매출채권", "재고자산", "기타채권", "매입채무"]

# 규칙기반 감사절차 추천 · 코멘트 템플릿 (카테고리별)
RISK_PROCEDURES = {
    "매출채권": ["매출채권 조회(Confirmation) 실시", "대손충당금 적정성 검토", "수익인식 시점(Cut-off) 테스트", "주요 채무자 신용상태 확인"],
    "재고자산": ["재고실사 입회", "평가충당금(저가법) 검토", "재고 Cut-off 테스트", "재고 진부화 여부 확인"],
    "기타채권": ["특수관계자 거래 여부 확인", "채권 회수가능성 검토", "관련 계약·증빙서류 검토"],
    "매입채무": ["매입채무 조회(Confirmation) 실시", "미지급 비용 완전성 검토", "Cut-off 테스트"],
    "발생액비율": ["이익조정 가능성 검토", "발생주의 회계처리 적정성 확인", "현금흐름표-손익계산서 정합성 검토"],
    "부실위험": ["계속기업 가정(Going Concern) 평가", "자금조달계획 및 차입금 만기 스케줄 검토", "경영진 대응계획 확인"],
    "정정공시": ["과거 정정 사유 확인", "동일 오류 재발 방지 통제 검토", "관련 계정 표본 확대"],
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

    def parse_amount(row):
        amount_str = (row.get("thstrm_amount") or "").replace(",", "").strip()
        if not amount_str:
            return None
        try:
            return float(amount_str)
        except ValueError:
            return None

    # 1순위: 표준계정코드(account_id) 매칭 - 회사별 계정명 표기 차이의 영향을 받지 않는다.
    for row in rows:
        account_id = row.get("account_id") or ""
        if not account_id or account_id == NON_STANDARD_ACCOUNT_ID:
            continue
        amount = parse_amount(row)
        if amount is None:
            continue
        for metric, ids in ACCOUNT_ID_ALIASES.items():
            if result[metric] is not None:
                continue
            if account_id in ids:
                result[metric] = amount

    # 2순위: 표준코드가 없거나 매칭 실패한 항목은 계정명 텍스트로 폴백.
    for row in rows:
        remaining = [m for m in TARGET_ACCOUNTS if result[m] is None]
        if not remaining:
            break
        account_nm = _normalize(row.get("account_nm"))
        amount = parse_amount(row)
        if amount is None:
            continue
        for metric in remaining:
            if account_nm in TARGET_ACCOUNTS_NORMALIZED[metric]:
                result[metric] = amount
    return result


def pct_change(cur, prev):
    if cur is None or prev in (None, 0):
        return None
    return (cur - prev) / abs(prev) * 100


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


def build_account_growth(metrics, prior_metrics):
    """매출액 및 주요 계정의 전기 대비 증감률표. 매출 증가율보다 훨씬 크게 늘어난 계정에
    위험 플래그를 매긴다 (예: 매출 +3%인데 매출채권 +48%)."""
    if not prior_metrics:
        return []
    revenue = metrics.get("매출액")
    prior_revenue = prior_metrics.get("매출액")
    revenue_growth = pct_change(revenue, prior_revenue)

    rows = [{
        "계정": "매출액",
        "당기": revenue,
        "전기": prior_revenue,
        "증감률(%)": round(revenue_growth, 1) if revenue_growth is not None else None,
        "위험": False,
    }]

    for acct in GROWTH_ACCOUNTS:
        cur = metrics.get(acct)
        prev = prior_metrics.get(acct)
        growth = pct_change(cur, prev)
        risky = False
        if growth is not None and revenue_growth is not None:
            diff = growth - revenue_growth
            risky = growth >= 15 and diff >= 20
        rows.append({
            "계정": acct,
            "당기": cur,
            "전기": prev,
            "증감률(%)": round(growth, 1) if growth is not None else None,
            "위험": risky,
        })
    return rows


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
    current_assets = metrics.get("유동자산")
    current_liabilities = metrics.get("유동부채")

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
    record["유동비율(%)"] = (
        round(current_assets / current_liabilities * 100, 2)
        if current_assets is not None and current_liabilities else None
    )
    record["ROA(%)"] = (
        round(net_income / assets * 100, 2) if assets and net_income is not None else None
    )
    record["ROE(%)"] = (
        round(net_income / equity * 100, 2) if equity and net_income is not None else None
    )
    record["현금흐름대비순이익비율(%)"] = (
        round(cfo / net_income * 100, 2) if net_income and cfo is not None else None
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
    record["account_growth"] = build_account_growth(metrics, prior_metrics)

    flags = []
    for row in record["account_growth"]:
        if not row["위험"]:
            continue
        acct = row["계정"]
        rev_g = record["account_growth"][0]["증감률(%)"]
        flags.append({
            "type": f"{acct} 급증",
            "category": acct,
            "detail": f"매출 {rev_g:+.1f}% 대비 {acct} {row['증감률(%)']:+.1f}% 증가",
            "severity": "high" if (row["증감률(%)"] - rev_g) >= 40 else "medium",
        })

    if record["발생액비율(%)"] is not None and record["발생액비율(%)"] >= 10:
        flags.append({
            "type": "발생액비율 과다",
            "category": "발생액비율",
            "detail": f"자산총계 대비 {record['발생액비율(%)']:.1f}% (순이익이 영업현금흐름을 상회)",
            "severity": "high" if record["발생액비율(%)"] >= 15 else "medium",
        })

    if record["zscore"] is not None and record["zscore"]["zone"] == "위험":
        flags.append({
            "type": "부실위험(Z-Score)",
            "category": "부실위험",
            "detail": f"Altman Z''-Score {record['zscore']['score']} (위험 구간, 1.1 미만)",
            "severity": "high",
        })

    record["flags"] = flags
    return record


DISCLOSURE_QUERIES = [
    ("A", {"정정공시": ["정정"]}),
    ("B", {"유상증자": ["유상증자"], "전환사채/신주인수권부사채": ["전환사채", "신주인수권부사채"]}),
    ("D", {"최대주주 변경": ["최대주주"]}),
    ("F", {"횡령/배임": ["횡령", "배임"], "상장폐지 관련": ["상장폐지"]}),
]


def fetch_disclosure_timeline(api_key, corp_code, start_year, end_year):
    """정정공시·유상증자·전환사채·최대주주변경·횡령배임·상장폐지 관련 공시 이력을 조회한다."""
    items = []
    for pblntf_ty, category_map in DISCLOSURE_QUERIES:
        params = {
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bgn_de": f"{start_year}0101",
            "end_de": f"{end_year}1231",
            "pblntf_ty": pblntf_ty,
            "sort": "date",
            "sort_mth": "desc",
            "page_count": "100",
        }
        resp = requests.get(f"{BASE_URL}/list.json", params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") not in ("000", "013"):  # 013 = 조회된 데이터 없음
            continue
        for row in data.get("list", []):
            report_nm = row.get("report_nm") or ""
            for category, keywords in category_map.items():
                if any(kw in report_nm for kw in keywords):
                    items.append({
                        "category": category,
                        "report_nm": report_nm,
                        "rcept_dt": row.get("rcept_dt"),
                        "flr_nm": row.get("flr_nm"),
                        "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={row.get('rcept_no')}",
                    })
                    break
    items.sort(key=lambda x: x.get("rcept_dt") or "", reverse=True)
    return items


def build_top_risks(latest_record, timeline):
    """최신 연도 위험 신호 + 정정공시 이력을 종합해 코멘트·추천 감사절차가 붙은
    TOP5 위험계정 리스트를 만든다 (규칙기반, 생성형 AI 미사용)."""
    items = []
    severity_rank = {"high": 2, "medium": 1}

    for f in latest_record.get("flags", []):
        category = f["category"]
        if category in RISK_PROCEDURES:
            comment = {
                "발생액비율": f"당기순이익이 영업활동현금흐름을 상회합니다({f['detail']}). 이익의 질(Earnings Quality) 저하 가능성을 검토할 필요가 있습니다.",
                "부실위험": f"{f['detail']}. 계속기업 가정에 대한 감사인의 추가 검토가 필요합니다.",
            }.get(category, f"{f['detail']}. 관련 계정에 대한 추가 감사절차가 필요합니다.")
        else:
            comment = f"{f['detail']}. 관련 계정에 대한 추가 감사절차가 필요합니다."

        items.append({
            "account": category,
            "type": f["type"],
            "comment": comment,
            "evidence": [f["detail"]],
            "procedures": RISK_PROCEDURES.get(category, ["관련 계정 상세 분석 및 표본 확대"]),
            "severity": f["severity"],
            "_rank": severity_rank.get(f["severity"], 0),
        })

    correction_count = sum(1 for t in timeline if t["category"] == "정정공시")
    if correction_count > 0:
        items.append({
            "account": "정정공시",
            "type": "정정공시 이력",
            "comment": f"조회 기간 내 {correction_count}건의 정기보고서 정정 이력이 있습니다. 과거 정정 사유와 재발 방지 통제를 확인할 필요가 있습니다.",
            "evidence": [f"정정 {correction_count}건"],
            "procedures": RISK_PROCEDURES["정정공시"],
            "severity": "medium",
            "_rank": severity_rank["medium"],
        })

    items.sort(key=lambda x: x["_rank"], reverse=True)
    for i, item in enumerate(items[:5], start=1):
        item["rank"] = i
        del item["_rank"]
    return items[:5]


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
        return 200, {"corp_code": corp_code, "corp_name": corp_name, "records": [], "timeline": [], "top_risks": []}

    try:
        timeline = fetch_disclosure_timeline(api_key, corp_code, start_year, end_year)
    except requests.RequestException:
        timeline = []

    top_risks = build_top_risks(records[-1], timeline)

    return 200, {
        "corp_code": corp_code,
        "corp_name": corp_name,
        "records": records,
        "timeline": timeline,
        "top_risks": top_risks,
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
