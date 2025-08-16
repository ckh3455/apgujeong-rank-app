# apgujeong_rank_app.py
# 실행: streamlit run apgujeong_rank_app.py

import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st

# ===== 페이지 설정 =====
st.set_page_config(
    page_title="압구정 구역별 감정가 순위",
    page_icon="🏢",
    layout="wide",
)

# ===== 사용자 안내/라벨 =====
APP_DESCRIPTION = (
    "⚠️ 데이터는 **2025년 공동주택 공시가격(공주가)** 을 바탕으로 계산한 것으로, "
    "재건축 시 **실행될 감정평가액과 차이**가 있을 수 있습니다.\n\n"
    "이 앱은 **구역 → 동 → 호**를 선택하면 같은 구역 내 **환산감정가(억)** 기준으로 "
    "**경쟁 순위**(공동이면 같은 순위, 다음 순위는 건너뜀)를 계산해 보여줍니다. "
    "하단 요약은 **현재 선택 세대가 속한 공동순위(같은 금액) 그룹**을 "
    "**동·평형별 연속 층 범위**로 간소화하여 표시합니다."
)

PROMO_TEXT_HTML = """
<div class="promo-box">
  <div class="promo-title">📞 <b>압구정 원 부동산</b></div>
  <div class="promo-line">압구정 재건축 전문 컨설팅 · <b>순위를 알고 사야하는 압구정</b></div>
  <div class="promo-line"><b>문의</b></div>
  <div class="promo-line">02-540-3334 / 최이사 Mobile 010-3065-1780</div>
  <div class="promo-small">압구정 미래가치 예측.</div>
</div>
"""

# 기본 Google Sheet (외부 공개 필요: '링크가 있는 모든 사용자 보기')
DEFAULT_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/1E_GAGLS7PgXFUvPiz2qsZYizKfi1mCrwez2u30OBCvI/"
    "export?format=xlsx&gid=1484463303"
)

# 동점 판정 정밀도(None이면 원값 기준)
ROUND_DECIMALS = 6

# ===== CSS(반응형·폰트/폭·프로모) =====
st.markdown(
    """
<style>
/* 모바일 패딩 축소 */
@media (max-width: 640px) {
  .block-container { padding: 0.75rem 0.8rem !important; }
  div[data-testid="stMetricValue"] { font-size: 1.3rem !important; }
  .stButton button { width: 100% !important; padding: 0.8rem 1rem !important; }
  label, .stSelectbox label { font-size: 0.95rem !important; }
}

/* 프로모 박스 */
.promo-box { padding: 12px 14px; border-radius: 12px; background: #fafafa;
  border: 1px solid #eee; margin: 10px 0 0 0; }
.promo-title { font-size: 1.15rem; font-weight: 800; margin-bottom: 4px; }
.promo-line  { font-size: 1.02rem; font-weight: 600; line-height: 1.5; }
.promo-small { font-size: 1.0rem;  font-weight: 700; font-style: italic; margin-top: 6px; }

/* 표 헤더 글자 길면 줄바꿈 */
thead tr th div[role="button"] p {
  white-space: normal !important;
}

/* '선택 세대 상세' 표 헤더 폰트/셀 폭 보정 */
table td, table th {
  word-break: keep-all;
}
</style>
""",
    unsafe_allow_html=True,
)

# ===== 작은 유틸 =====
def normalize_gsheet_url(url: str) -> str:
    """edit 링크 → export 링크로 변환"""
    if not isinstance(url, str):
        return url
    if "docs.google.com/spreadsheets" in url and "/export" not in url:
        m = re.search(r"/spreadsheets/d/([^/]+)/", url)
        gid = parse_qs(urlparse(url).query).get("gid", [None])[0]
        if m:
            doc_id = m.group(1)
            if gid is None:
                return f"https://docs.google.com/spreadsheets/d/{doc_id}/export?format=xlsx"
            return f"https://docs.google.com/spreadsheets/d/{doc_id}/export?format=xlsx&gid={gid}"
    return url


def clean_price(series: pd.Series) -> pd.Series:
    """문자 섞인 가격 문자열 → 숫자(float)로 정리."""
    s = series.astype(str)
    s = (
        s.str.replace("\u00A0", "", regex=False)  # NBSP
        .str.replace(",", "", regex=False)
        .str.replace("`", "", regex=False)
        .str.replace("'", "", regex=False)
        .str.replace("억", "", regex=False)
        .str.strip()
    )
    s = s.str.replace(r"[^0-9.\-]", "", regex=True)  # 숫자/소수점/음수만
    return pd.to_numeric(s, errors="coerce")


def extract_floor(ho) -> float:
    """호수에서 숫자만 추출해 '층'으로 환산 (예: 702 → 7층, 1101 → 11층)"""
    s = str(ho)
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return np.nan
    if len(digits) >= 3:
        return float(int(digits[:-2])) if digits[:-2] else np.nan
    elif len(digits) == 2:
        return float(int(digits[0]))
    else:
        return float(int(digits))


def contiguous_ranges(sorted_ints):
    """정수 리스트(오름차순) → 연속 구간 [(s,e), ...]"""
    ranges = []
    start = prev = None
    for x in sorted_ints:
        x = int(x)
        if start is None:
            start = prev = x
        elif x == prev + 1:
            prev = x
        else:
            ranges.append((start, prev))
            start = prev = x
    if start is not None:
        ranges.append((start, prev))
    return ranges


def format_range(s, e):
    return f"{s}층" if s == e else f"{s}층에서 {e}층까지"


def detect_device_from_toggle() -> str:
    """모바일 간단 보기 토글 기준으로 device 기록"""
    return "mobile" if st.session_state.get("mobile_simple", False) else "desktop"


# ===== 데이터 로딩 =====
def load_data(source):
    """URL이면 read_excel/CSV, 로컬이면 read_excel → 표준화 후 환산감정가 생성(공시가÷0.69, fallback: 감정가(억))."""
    is_url = isinstance(source, str) and (source.startswith("http://") or source.startswith("https://"))
    with st.spinner("데이터 불러오는 중…"):
        if is_url:
            parsed = urlparse(source)
            fmt = (parse_qs(parsed.query).get("format", [None])[0] or "").lower()
            if fmt == "csv":
                df = pd.read_csv(source)
            else:
                df = pd.read_excel(source, sheet_name=0)
        else:
            p = Path(source)
            if not p.exists():
                raise FileNotFoundError(f"경로가 존재하지 않습니다: {p}")
            df = pd.read_excel(p, sheet_name=0)

    # 열 이름 표준화(필수: 구역·동·호·공시가(억) / 선택: 감정가(억), 평형)
    # 사용자가 실제 시트에서 쓰는 한글 열명을 그대로 맞춰줍니다.
    rename_map = {
        "구역": "구역",
        "동": "동",
        "호": "호",
        "공시가(억)": "공시가(억)",
        "감정가(억)": "감정가(억)",
        "평형": "평형",
        "25년 공시가(억)": "25년 공시가(억)",  # 혹시 이 열명으로 저장되어 있을 수도 있음
    }
    df = df.rename(columns=rename_map)

    for c in ["구역", "동", "호"]:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()

    # 25년 공시가가 따로 있으면 우선 사용, 없으면 '공시가(억)' 사용
    if "25년 공시가(억)" in df.columns:
        public = clean_price(df["25년 공시가(억)"])
    else:
        public = clean_price(df.get("공시가(억)", pd.Series(dtype=object)))

    # 환산감정가 = 공시가(억) ÷ 0.69 (fallback: 감정가(억) 클린)
    derived = public / 0.69
    fallback = clean_price(df.get("감정가(억)", pd.Series(dtype=object)))
    df["환산감정가_억"] = derived.where(~derived.isna(), fallback)

    # 평형이 없다면 빈칸
    if "평형" not in df.columns:
        df["평형"] = ""

    return df


# ===== 구글시트 로깅 =====
def append_usage_row(date_str, time_str, device, zone, dong, ho):
    """구글 시트에 간소화된 사용 로그 기록"""
    if "gcp_service_account" not in st.secrets or not st.secrets.get("USAGE_SHEET_ID"):
        return False, "시크릿에 서비스 계정/시트 ID가 없습니다."
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        sa_info = st.secrets["gcp_service_account"]
        creds = Credentials.from_service_account_info(
            sa_info,
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ],
        )
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(st.secrets["USAGE_SHEET_ID"])
        ws = sh.sheet1  # 첫 번째 시트 사용
        row = [date_str, time_str, device, zone, dong, ho]
        ws.append_row(row, value_input_option="RAW")
        return True, "ok"
    except Exception as e:
        return False, str(e)


# ===== 상단 UI =====
st.title("🏢 압구정 구역별 감정가 순위")
st.info(APP_DESCRIPTION)

top_left, top_right = st.columns([2, 1])
with top_left:
    st.toggle("📱 모바일 간단 보기", key="mobile_simple", value=True, help="모바일에서 보기 편한 간단 레이아웃")
with top_right:
    if st.button("🔄 데이터 새로고침"):
        st.rerun()

with st.expander("① 데이터 파일/URL 선택 — 필요한 열: ['구역','동','호','공시가(억)'/'25년 공시가(억)','감정가(억)','평형']", expanded=False):
    uploaded = st.file_uploader("엑셀 파일 업로드 (.xlsx)", type=["xlsx"])
    manual_source = st.text_input("로컬 파일 경로 또는 Google Sheets/CSV URL (선택)", value="")
    same_folder_default = Path.cwd() / "압구정 공시가.xlsx"

    if uploaded is not None:
        resolved_source = uploaded
        source_desc = "업로드된 파일 사용"
    elif manual_source.strip():
        ms = normalize_gsheet_url(manual_source.strip())
        resolved_source = ms
        source_desc = "직접 입력 소스 사용"
    else:
        resolved_source = DEFAULT_SHEET_URL
        source_desc = "기본 Google Sheets 사용"

    st.success(f"데이터 소스: {source_desc}")
    st.caption(f"현재 소스: {resolved_source if isinstance(resolved_source, str) else '업로드된 파일 객체'}")

# ===== 데이터 로딩 =====
try:
    if isinstance(resolved_source, str):
        df = load_data(resolved_source)
    else:
        df = load_data(resolved_source)  # 업로드 파일 객체도 동일 처리
    st.success("데이터 로딩 완료")
except Exception as e:
    st.error(f"데이터를 불러오지 못했습니다: {e}")
    st.stop()

# ===== 선택 UI =====
zones = sorted(df["구역"].dropna().unique().tolist()) if "구역" in df.columns else []
if not zones:
    st.warning("구역 데이터가 비어 있습니다.")
    st.stop()

# 모바일/데스크탑 레이아웃 분기
if st.session_state.get("mobile_simple", False):
    zone = st.selectbox("구역 선택", zones, index=0)
    zone_df = df[df["구역"] == zone].copy()

    dongs = sorted(zone_df["동"].dropna().unique().tolist())
    dong = st.selectbox("동 선택", dongs, index=0 if dongs else None)

    dong_df = zone_df[zone_df["동"] == dong].copy()
    hos = sorted(dong_df["호"].dropna().unique().tolist())
    ho = st.selectbox("호 선택", hos, index=0 if hos else None)
else:
    c1, c2, c3 = st.columns(3)
    with c1:
        zone = st.selectbox("구역 선택", zones, index=0)
    zone_df = df[df["구역"] == zone].copy()
    with c2:
        dongs = sorted(zone_df["동"].dropna().unique().tolist())
        dong = st.selectbox("동 선택", dongs, index=0 if dongs else None)
    dong_df = zone_df[zone_df["동"] == dong].copy()
    with c3:
        hos = sorted(dong_df["호"].dropna().unique().tolist())
        ho = st.selectbox("호 선택", hos, index=0 if hos else None)

sel_df = dong_df[dong_df["호"] == ho].copy()
if sel_df.empty:
    st.warning("선택한 동/호 데이터가 없습니다.")
    st.stop()

# ===== 확인(조회/기록) 버튼 =====
go = st.button("✅ 선택 세대 확인/기록")
st.divider()

# ===== 순위 계산(경쟁 순위) =====
total_units_all = len(zone_df)

work = zone_df.dropna(subset=["환산감정가_억"]).copy()
work = work[pd.to_numeric(work["환산감정가_억"], errors="coerce").notna()].copy()
work["환산감정가_억"] = work["환산감정가_억"].astype(float)

bad_mask = pd.to_numeric(zone_df["환산감정가_억"], errors="coerce").isna()
bad_rows = zone_df[bad_mask].copy()

# 동점 키(라운딩 or 원값) + 경쟁 순위
work["가격키"] = (
    work["환산감정가_억"].round(ROUND_DECIMALS)
    if ROUND_DECIMALS is not None
    else work["환산감정가_억"]
)
work["순위"] = work["가격키"].rank(method="min", ascending=False).astype(int)
work["공동세대수"] = work.groupby("가격키")["가격키"].transform("size")
work = work.sort_values(["가격키", "동", "호"], ascending=[False, True, True]).reset_index(drop=True)

# 선택 세대의 가격/키/순위
sel_price = float(sel_df.iloc[0]["환산감정가_억"]) if pd.notna(sel_df.iloc[0]["환산감정가_억"]) else np.nan
sel_key = round(sel_price, ROUND_DECIMALS) if (pd.notna(sel_price) and ROUND_DECIMALS is not None) else sel_price

if pd.notna(sel_key):
    subset = work[work["가격키"] == sel_key]
    sel_rank = int(subset["순위"].min()) if not subset.empty else None
    sel_tied = int(subset["공동세대수"].max()) if not subset.empty else 0
else:
    sel_rank, sel_tied = None, 0

total_units_valid = int(len(work))

# ===== 상단 지표 =====
m1, m2, m3, m4 = st.columns(4)
m1.metric("선택 구역", zone)
m2.metric("구역 전체 세대수", f"{total_units_all:,} 세대")
m3.metric("유효 세대수(환산감정가 있음)", f"{total_units_valid:,} 세대")
if pd.notna(sel_price):
    m4.metric("선택 세대 환산감정가(억)", f"{sel_price:,.2f}")
else:
    m4.metric("선택 세대 환산감정가(억)", "-")

if pd.isna(sel_price):
    st.info("선택 세대의 환산감정가가 비어 있거나 숫자 형식이 아닙니다. 순위 계산에서 제외됩니다.")
elif sel_rank is not None:
    msg = f"구역 내 순위: 공동 {sel_rank}위 ({sel_tied}세대)" if sel_tied > 1 else f"구역 내 순위: {sel_rank}위"
    st.success(msg)
else:
    st.info("선택 세대는 유효 순위 계산 집합에 포함되지 않았습니다.")

st.divider()

# ===== 선택 세대 상세 =====
st.subheader("선택 세대 상세")
detail = work[(work["동"] == dong) & (work["호"] == ho)].copy()

# '25년 공시가(억)' 값이 있으면 그걸, 없으면 '공시가(억)'를 표기용으로 사용
if "25년 공시가(억)" in sel_df.columns:
    public_one = clean_price(sel_df["25년 공시가(억)"]).iloc[0]
else:
    public_one = clean_price(sel_df.get("공시가(억)", pd.Series([np.nan]))).iloc[0]

row_show = pd.DataFrame(
    [{
        "구역": zone,
        "동": dong,
        "호": ho,
        "평형": str(sel_df["평형"].iloc[0]) if "평형" in sel_df.columns else "",
        "25년 공시가(억)": round(public_one, 2) if pd.notna(public_one) else np.nan,
        "환산감정가(억)": round(sel_price, 2) if pd.notna(sel_price) else np.nan,
        "순위": sel_rank if sel_rank is not None else "",
        "공동세대수": sel_tied if sel_tied else "",
    }]
)

st.dataframe(
    row_show,
    use_container_width=True,
    column_config={
        "동": st.column_config.TextColumn(width="small"),
        "평형": st.column_config.TextColumn(width="small"),
        "25년 공시가(억)": st.column_config.NumberColumn(format="%.2f", width="medium"),
        "환산감정가(억)": st.column_config.NumberColumn(format="%.2f", width="medium"),
        "순위": st.column_config.NumberColumn(width="small"),
        "공동세대수": st.column_config.NumberColumn(width="small"),
    },
    hide_index=True,
)

# === 프로모 카드(모바일/PC 공통, 항상 표 아래) ===
st.markdown(PROMO_TEXT_HTML, unsafe_allow_html=True)
st.divider()

# ===== 공동순위 요약 (선택 세대 금액 기준 · 동·평형별 연속 층 범위) =====
st.subheader("공동순위 요약 (선택 세대 금액 기준)")

if sel_rank is None or pd.isna(sel_key):
    st.info("선택 세대의 환산감정가가 유효하지 않아 공동순위를 계산할 수 없습니다.")
else:
    tmp = work.copy()
    tmp["층"] = tmp["호"].apply(extract_floor)
    grp = tmp[tmp["가격키"] == sel_key].copy()

    # 헤더
    st.markdown(f"**공동 {sel_rank}위 ({sel_tied}세대)** · 환산감정가: **{sel_key:,.2f}억**")

    no_floor = grp["층"].isna().sum()
    if no_floor > 0:
        st.caption(f"※ 층 정보가 없는 세대 {no_floor}건은 범위 요약에서 제외됩니다.")

    rows = []
    for (dong_name, pyeong), g in grp.dropna(subset=["층"]).groupby(["동", "평형"]):
        floors = sorted(set(int(x) for x in g["층"].dropna().tolist()))
        if not floors:
            continue
        ranges = contiguous_ranges(floors)
        ranges_str = ", ".join(format_range(s, e) for s, e in ranges)
        rows.append(
            {"동(평형)": f"{dong_name}동({pyeong})" if str(pyeong) else f"{dong_name}동", "층 범위": ranges_str, "세대수": len(g)}
        )

    # 동명 숫자 기준 정렬
    def _dong_num(s):
        m = re.search(r"\d+", str(s))
        return int(m.group()) if m else 10 ** 9

    rows = sorted(rows, key=lambda r: _dong_num(r["동(평형)"]))
    if rows:
        out = pd.DataFrame(rows)
        st.dataframe(out, use_container_width=True, hide_index=True)
        csv_agg = out.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "현재 공동순위 요약 CSV 다운로드",
            csv_agg,
            file_name=f"{zone}_공동{sel_rank}위_동평형층요약.csv",
            mime="text/csv",
        )
    else:
        st.info("해당 공동순위 그룹에서 요약할 층 정보가 없습니다.")

st.divider()

# ===== 압구정 내 유사한 차수 10 (구역·동·평형별 연속 층 범위) =====
st.subheader("압구정 내 금액이 유사한 차수 10 (구역·동·평형별 연속 층 범위)")
st.caption("※ 공시가격에 기반한 것으로 실제 시장 상황과 다를 수 있습니다.")

if pd.isna(sel_price):
    st.info("선택 세대의 환산감정가가 유효하지 않아 유사 금액을 찾을 수 없습니다.")
else:
    # 전 구역에서 환산감정가 유효 + 층 정보 계산
    pool = df.copy()
    pool = pool[pd.to_numeric(pool["환산감정가_억"], errors="coerce").notna()].copy()
    pool["환산감정가_억"] = pool["환산감정가_억"].astype(float)
    pool["층"] = pool["호"].apply(extract_floor)

    # 선택 세대 자체는 제외
    pool = pool[~((pool["구역"] == zone) & (pool["동"] == dong) & (pool["호"] == ho) &
                  (np.isclose(pool["환산감정가_억"], sel_price, rtol=0, atol=1e-6)))].copy()

    # 유사도(절대 차이) → 후보 정렬 후 상위 넉넉히 확보
    pool["유사도"] = (pool["환산감정가_억"] - sel_price).abs()
    cand = pool.sort_values(["유사도", "환산감정가_억"], ascending=[True, False]).head(1000).copy()

    # (구역, 동, 평형)별 요약
    def _zone_num(z):
        m = re.search(r"\d+", str(z))
        return int(m.group()) if m else 10 ** 9

    def _dong_num(d):
        m = re.search(r"\d+", str(d))
        return int(m.group()) if m else 10 ** 9

    rows2 = []
    for (zone_name, dong_name, pyeong), g in cand.dropna(subset=["층"]).groupby(["구역", "동", "평형"]):
        floors = sorted(set(int(x) for x in g["층"].dropna().tolist()))
        if not floors:
            continue
        ranges = contiguous_ranges(floors)
        ranges_str = ", ".join(format_range(s, e) for s, e in ranges)
        median_price = float(g["환산감정가_억"].median()) if len(g) else np.nan
        rows2.append(
            {
                "구역": zone_name,
                "동(평형)": f"{dong_name}동({pyeong})" if str(pyeong) else f"{dong_name}동",
                "층 범위": ranges_str,
                "세대수": int(len(g)),
                "중앙값 환산감정가(억)": round(median_price, 2) if pd.notna(median_price) else np.nan,
                "_sz": _zone_num(zone_name),
                "_sd": _dong_num(dong_name),
            }
        )

    if not rows2:
        st.info("유사 금액 결과가 없습니다.")
    else:
        out2 = pd.DataFrame(rows2).sort_values(
            ["_sz", "_sd", "세대수"], ascending=[True, True, False]
        ).head(10).drop(columns=["_sz", "_sd"])

        st.dataframe(
            out2,
            use_container_width=True,
            hide_index=True,
            column_config={
                "층 범위": st.column_config.TextColumn(width="small"),
                "세대수": st.column_config.NumberColumn(width="small"),
                "중앙값 환산감정가(억)": st.column_config.NumberColumn(format="%.2f"),
            },
        )
        csv_sim = out2.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "유사금액 범위 TOP10 CSV 다운로드",
            csv_sim,
            file_name=f"압구정_유사금액_범위_TOP10_{zone}_{dong}_{ho}.csv",
            mime="text/csv",
        )

# ===== 비정상 값 안내 =====
if not bad_rows.empty:
    with st.expander("비정상 환산감정가(미기재/비정상) 행 보기 / 다운로드", expanded=False):
        cols_exist = [c for c in ["구역", "동", "호", "공시가(억)", "25년 공시가(억)", "감정가(억)", "평형"] if c in bad_rows.columns]
        bad_show = bad_rows[["구역", "동", "호"] + cols_exist].copy().drop_duplicates()
        st.dataframe(bad_show.reset_index(drop=True), use_container_width=True)
        bad_csv = bad_show.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "비정상 환산감정가 목록 CSV 다운로드",
            bad_csv,
            file_name=f"{zone}_비정상_환산감정가_목록.csv",
            mime="text/csv",
        )

# ===== 로그(확인 버튼 눌렀을 때만) =====
if go:
    device = detect_device_from_toggle()
    now = now_local()                      # ← 로컬(기본 KST)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")


    ok, msg = append_usage_row(date_str, time_str, device, str(zone), str(dong), str(ho))
    if ok:
        st.success("조회/기록되었습니다.")
    else:
        st.warning(f"로그 기록 생략: {msg}")

