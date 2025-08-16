# apgujeong_rank_app.py
# 실행: streamlit run apgujeong_rank_app.py

import time
import re
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

import numpy as np
import pandas as pd
import streamlit as st

# ──────────────────────────────────────────────────────────────────────────────
# 페이지 & 안내문
# ──────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="압구정 구역별 감정가 순위", page_icon="🏢", layout="wide")

APP_DESCRIPTION = (
    "⚠️ 데이터는 **2025년 공동주택 공시가격(공주가)** 을 바탕으로 계산한 것으로, "
    "재건축 시 **실행될 감정평가액과 차이**가 있을 수 있습니다.\n\n"
    "이 앱은 **구역 → 동 → 호**를 선택하면 같은 구역 내 **환산감정가(억)** 기준으로 "
    "**경쟁 순위**(공동이면 같은 순위, 다음 순위는 건너뜀)를 계산해 보여줍니다. "
    "하단 요약은 **현재 선택 세대가 속한 공동순위(같은 금액) 그룹**을 "
    "**동별 연속 층 범위**로 간소화하여 표시합니다."
)

# 표시용 라벨
DISPLAY_PRICE_LABEL = "환산감정가(억)"
DISPLAY_PRICE_NOTE = "※ 환산감정가는 공시가(억)를 0.7로 나눈 값(공시가 없으면 감정가(억) 사용)입니다."

# ✅ 기본 Google Sheets (외부 공개 필요: '링크가 있는 모든 사용자 보기')
DEFAULT_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/1E_GAGLS7PgXFUvPiz2qsZYizKfi1mCrwez2u30OBCvI/"
    "export?format=xlsx&gid=1484463303"
)

# 동점 판정 정밀도(None이면 원값 사용)
ROUND_DECIMALS = 6

# ──────────────────────────────────────────────────────────────────────────────
# 스타일(CSS) – 모바일 가독성 & 프로모 박스
# ──────────────────────────────────────────────────────────────────────────────
st.markdown(
    """
<style>
@media (max-width: 640px) {
  .block-container { padding: 0.75rem 0.8rem !important; }
  div[data-testid="stMetricValue"] { font-size: 1.5rem !important; }
  .stButton button { width: 100% !important; padding: 0.8rem 1rem !important; }
  label, .stSelectbox label { font-size: 0.95rem !important; }
}
/* 프로모 박스 */
.promo-box { 
  padding: 12px 14px; 
  border-radius: 12px; 
  background: #fafafa; 
  border: 1px solid #eee; 
  margin: 10px 0 0 0;
}
.promo-title { font-size: 1.25rem; font-weight: 800; margin-bottom: 6px; }
.promo-line  { font-size: 1.1rem;  font-weight: 700; line-height: 1.5; }
.promo-small { font-size: 1.0rem;  font-weight: 700; font-style: italic; margin-top: 6px; }
@media (max-width: 640px) {
  .promo-title { font-size: 1.25rem; }
  .promo-line  { font-size: 1.15rem; }
  .promo-small { font-size: 1.05rem; }
}
</style>
""",
    unsafe_allow_html=True,
)

PROMO_TEXT_HTML = """
<div class="promo-box">
  <div class="promo-title">📞 압구정 원 부동산</div>
  <div class="promo-line">압구정 재건축 전문 컨설팅 · 순위를 알고 사야하는 압구정</div>
  <div class="promo-line"><strong>문의</strong></div>
  <div class="promo-line">02-540-3334 / 최이사 Mobile 010-3065-1780</div>
  <div class="promo-small">압구정 미래가치 예측.</div>
</div>
"""

st.title("🏢 압구정 구역별 감정가 순위")
st.info(APP_DESCRIPTION)

# 상단 토글 & 새로고침 버튼
top_left, top_right = st.columns([2, 1])
with top_left:
    mobile_simple = st.toggle("📱 모바일 간단 보기", value=True, help="모바일에서 보기 편한 간단 레이아웃")
with top_right:
    if st.button("🔄 데이터 새로고침"):
        st.rerun()

# ──────────────────────────────────────────────────────────────────────────────
# 유틸 함수(링크 변환/캐시 무시/클리닝)
# ──────────────────────────────────────────────────────────────────────────────
def normalize_gsheet_url(url: str) -> str:
    """edit 링크 → export 링크로 변환(가능한 경우)."""
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


def add_cache_bust(url: str) -> str:
    """쿼리에 cb(밀리초)를 붙여 브라우저/프록시/구글 export 캐시 우회."""
    try:
        u = urlparse(url)
        q = parse_qs(u.query)
        q["cb"] = [str(int(time.time() * 1000))]
        return urlunparse(u._replace(query=urlencode(q, doseq=True)))
    except Exception:
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


def to_float_one(x):
    val = clean_price(pd.Series([x]))[0]
    return float(val) if pd.notna(val) else np.nan


# ──────────────────────────────────────────────────────────────────────────────
# 데이터 로딩(항상 최신, 캐시 무시) + 표준화/환산
# ──────────────────────────────────────────────────────────────────────────────
def load_data(source):
    """
    URL이면 read_excel/CSV, 로컬이면 read_excel → 표준화 후 환산감정가 생성(공시가÷0.7, fallback: 감정가(억)).
    '평형' 컬럼이 있으면 그대로 유지(없으면 NaN).
    """
    is_url = isinstance(source, str) and source.startswith(("http://", "https://"))
    with st.spinner("데이터 불러오는 중…"):
        if is_url:
            src = add_cache_bust(source)  # ✅ 캐시 무시
            parsed = urlparse(src)
            fmt = (parse_qs(parsed.query).get("format", [None])[0] or "").lower()
            if fmt == "csv":
                df = pd.read_csv(src)
            else:
                df = pd.read_excel(src, sheet_name=0)
        else:
            p = Path(source)
            if not p.exists():
                raise FileNotFoundError(f"경로가 존재하지 않습니다: {p}")
            df = pd.read_excel(p, sheet_name=0)

    # 열 이름 표준화(존재하는 컬럼만 rename)
    rename_map = {
        "구역": "구역",
        "동": "동",
        "호": "호",
        "공시가(억)": "공시가(억)",
        "감정가(억)": "감정가(억)",
        "평형": "평형",  # 새로 추가된 컬럼
    }
    for k, v in list(rename_map.items()):
        if k not in df.columns:
            rename_map.pop(k)
    df = df.rename(columns=rename_map)

    # 문자열 정리
    for c in ["구역", "동", "호", "평형"]:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()

    # 환산감정가 = 공시가(억) / 0.7 (공시가 없으면 감정가(억) 클린 사용)
    public = pd.to_numeric(df.get("공시가(억)"), errors="coerce")
    derived = public / 0.7
    fallback = clean_price(df.get("감정가(억)", pd.Series(dtype=object)))
    df["감정가_클린"] = derived.where(~derived.isna(), fallback)  # 순위 계산에 사용하는 수치

    # 평형 컬럼이 없으면 생성(NaN)
    if "평형" not in df.columns:
        df["평형"] = np.nan

    return df


# ──────────────────────────────────────────────────────────────────────────────
# ① 데이터 소스 선택
# ──────────────────────────────────────────────────────────────────────────────
with st.expander("① 데이터 파일/URL 선택 — 필요한 열: ['구역','동','호','공시가(억)','감정가(억)']", expanded=False):
    uploaded = st.file_uploader("엑셀 파일 업로드 (.xlsx)", type=["xlsx"])
    manual_source = st.text_input("로컬 파일 경로 또는 Google Sheets/CSV URL (선택)", value="")
    same_folder_default = Path.cwd() / "압구정 공시가.xlsx"  # 선택사항

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
        # 같은 폴더 엑셀을 기본으로 쓰려면 주석 해제
        # if same_folder_default.exists():
        #     resolved_source = str(same_folder_default)
        #     source_desc = f"같은 폴더 엑셀 사용: {same_folder_default}"

    st.success(f"데이터 소스: {source_desc}")
    st.caption(f"현재 소스: {resolved_source if isinstance(resolved_source, str) else '업로드된 파일 객체'}")
    if isinstance(resolved_source, str) and resolved_source.startswith(("http://", "https://")):
        m = re.search(r"/spreadsheets/d/([^/]+)/", resolved_source)
        gid = parse_qs(urlparse(resolved_source).query).get("gid", [None])[0]
        st.caption(f"Doc ID: {m.group(1) if m else '-'} / gid: {gid}")

# 실제 로드
try:
    if isinstance(resolved_source, str):
        df = load_data(resolved_source)
    else:
        df = pd.read_excel(resolved_source, sheet_name=0)
        # 표준화
        for c in ["구역", "동", "호", "평형"]:
            if c in df.columns:
                df[c] = df[c].astype(str).str.strip()
        public = pd.to_numeric(df.get("공시가(억)"), errors="coerce")
        derived = public / 0.7
        fallback = clean_price(df.get("감정가(억)", pd.Series(dtype=object)))
        df["감정가_클린"] = derived.where(~derived.isna(), fallback)
        if "평형" not in df.columns:
            df["평형"] = np.nan
    st.success("데이터 로딩 완료")
except Exception as e:
    st.error(f"데이터를 불러오지 못했습니다: {e}")
    st.stop()

# ──────────────────────────────────────────────────────────────────────────────
# ② 선택 UI
# ──────────────────────────────────────────────────────────────────────────────
zones = sorted(df["구역"].dropna().unique().tolist())
if not zones:
    st.warning("구역 데이터가 비어 있습니다.")
    st.stop()

if mobile_simple:
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

# ──────────────────────────────────────────────────────────────────────────────
# ③ 유효성/순위 계산(경쟁 순위, 높은 금액 우선)
# ──────────────────────────────────────────────────────────────────────────────
total_units_all = len(zone_df)

work = zone_df.dropna(subset=["감정가_클린"]).copy()
work = work[pd.to_numeric(work["감정가_클린"], errors="coerce").notna()].copy()
work["감정가_클린"] = work["감정가_클린"].astype(float)

bad_mask = pd.to_numeric(zone_df["감정가_클린"], errors="coerce").isna()
bad_rows = zone_df[bad_mask].copy()

# 동점 키(라운딩 or 원값)
work["가격키"] = work["감정가_클린"].round(ROUND_DECIMALS) if ROUND_DECIMALS is not None else work["감정가_클린"]

# 경쟁 순위
work["순위"] = work["가격키"].rank(method="min", ascending=False).astype(int)
work["공동세대수"] = work.groupby("가격키")["가격키"].transform("size")

# 높은 금액 우선 정렬(+동/호 보조)
work = work.sort_values(["가격키", "동", "호"], ascending=[False, True, True]).reset_index(drop=True)

# 선택 세대 순위/공동
sel_price = float(sel_df.iloc[0]["감정가_클린"]) if pd.notna(sel_df.iloc[0]["감정가_클린"]) else np.nan
sel_key = round(sel_price, ROUND_DECIMALS) if (pd.notna(sel_price) and ROUND_DECIMALS is not None) else sel_price
sel_pyung = sel_df.iloc[0]["평형"] if "평형" in sel_df.columns else np.nan

if pd.notna(sel_key):
    subset = work[work["가격키"] == sel_key]
    sel_rank = int(subset["순위"].min()) if not subset.empty else None
    sel_tied = int(subset["공동세대수"].max()) if not subset.empty else 0
else:
    sel_rank, sel_tied = None, 0

total_units_valid = int(len(work))

# ──────────────────────────────────────────────────────────────────────────────
# ④ 상단 지표
# ──────────────────────────────────────────────────────────────────────────────
if mobile_simple:
    st.metric("선택 구역", zone)
    st.metric("구역 전체 세대수", f"{total_units_all:,} 세대")
    st.metric("유효 세대수(환산감정가 있음)", f"{total_units_valid:,} 세대")
    st.metric(f"선택 세대 {DISPLAY_PRICE_LABEL}", f"{sel_price:,.2f}" if pd.notna(sel_price) else "-")
    if pd.notna(sel_pyung):
        st.metric("선택 세대 평형", str(sel_pyung))
else:
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("선택 구역", zone)
    m2.metric("구역 전체 세대수", f"{total_units_all:,} 세대")
    m3.metric("유효 세대수(환산감정가 있음)", f"{total_units_valid:,} 세대")
    m4.metric(f"선택 세대 {DISPLAY_PRICE_LABEL}", f"{sel_price:,.2f}" if pd.notna(sel_price) else "-")
    m5.metric("선택 세대 평형", str(sel_pyung) if pd.notna(sel_pyung) else "-")

if pd.isna(sel_price):
    st.info("선택 세대의 환산감정가가 비어 있거나 숫자 형식이 아닙니다. 순위 계산에서 제외됩니다.")
elif sel_rank is not None:
    msg = f"구역 내 순위: 공동 {sel_rank}위 ({sel_tied}세대)" if sel_tied > 1 else f"구역 내 순위: {sel_rank}위"
    st.success(msg)
else:
    st.info("선택 세대는 유효 순위 계산 집합에 포함되지 않았습니다.")

st.caption(DISPLAY_PRICE_NOTE)
st.divider()

# ──────────────────────────────────────────────────────────────────────────────
# ⑤ 선택 세대 상세 (프로모 박스 함께 표시)
# ──────────────────────────────────────────────────────────────────────────────
basic_cols = ["동", "호", "평형", "감정가_클린", "순위", "공동세대수"]
full_cols = ["구역", "동", "호", "평형", "공시가(억)", "감정가(억)", "감정가_클린", "순위", "공동세대수"]
show_cols = basic_cols if mobile_simple else full_cols

st.subheader("선택 세대 상세")
sel_detail = work[(work["동"] == dong) & (work["호"] == ho)].copy()
if not sel_detail.empty:
    showDF = sel_detail[show_cols].rename(columns={"감정가_클린": DISPLAY_PRICE_LABEL})
    st.dataframe(showDF.reset_index(drop=True), use_container_width=True, height=200 if mobile_simple else None)
else:
    st.info("선택 세대는 유효 순위 계산 집합에 없습니다.")

# ✅ 프로모 텍스트(모바일에서도 보이도록 강화)
st.markdown(PROMO_TEXT_HTML, unsafe_allow_html=True)
st.divider()

# ──────────────────────────────────────────────────────────────────────────────
# ⑥ 공동순위 요약 (선택 세대 금액 기준 · 동별 연속 층 범위)
# ──────────────────────────────────────────────────────────────────────────────
st.subheader("공동순위 요약 (선택 세대 금액 기준)")

def extract_floor(ho) -> float:
    """호수에서 '층'으로 추출(예: 702→7, 1101→11)."""
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
    """정수 리스트 오름차순 → 연속구간 [(s,e), ...]"""
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

if sel_rank is None or pd.isna(sel_key):
    st.info("선택 세대의 환산감정가가 유효하지 않아 공동순위를 계산할 수 없습니다.")
else:
    tmp = work.copy()
    tmp["층"] = tmp["호"].apply(extract_floor)

    grp = tmp[tmp["가격키"] == sel_key].copy()

    st.markdown(f"**공동 {sel_rank}위 ({sel_tied}세대)** · {DISPLAY_PRICE_LABEL}: **{sel_key:,.2f}**")

    no_floor = grp["층"].isna().sum()
    if no_floor > 0:
        st.caption(f"※ 층 정보가 없는 세대 {no_floor}건은 범위 요약에서 제외됩니다.")

    rows = []
    for dong_name, g in grp.dropna(subset=["층"]).groupby("동"):
        floors = sorted(set(int(x) for x in g["층"].dropna().tolist()))
        if not floors:
            continue
        ranges = contiguous_ranges(floors)
        ranges_str = ", ".join(format_range(s, e) for s, e in ranges)

        # 해당 동 내 대표 평형(단일/다수 구분)
        py = g.get("평형")
        if py is not None:
            uniq = [str(x) for x in sorted(set(py.dropna().astype(str)))]
            py_label = uniq[0] if len(uniq) == 1 else "/".join(uniq[:3]) + ("…" if len(uniq) > 3 else "")
        else:
            py_label = ""

        rows.append({"동": f"{dong_name}동" if "동" not in str(dong_name) else str(dong_name),
                     "평형": py_label, "층 범위": ranges_str, "세대수": int(len(g))})

    def _dong_num(d):
        m = re.search(r"\d+", str(d))
        return int(m.group()) if m else 10**9

    rows = sorted(rows, key=lambda r: _dong_num(r["동"]))
    if rows:
        out = pd.DataFrame(rows)
        st.dataframe(out, use_container_width=True)
        csv_agg = out.to_csv(index=False).encode("utf-8-sig")
        st.download_button("현재 공동순위 요약 CSV 다운로드", csv_agg,
                           file_name=f"{zone}_공동{sel_rank}위_동별층요약.csv", mime="text/csv")
    else:
        st.info("해당 공동순위 그룹에서 요약할 층 정보가 없습니다.")

# 비정상 값 안내
if not bad_rows.empty:
    st.warning(f"환산감정가 비정상 값 {len(bad_rows)}건 발견 — 유효 세대수에서 제외됩니다.")
    with st.expander("비정상 환산감정가 행 보기 / 다운로드", expanded=False):
        cols_exist = [c for c in ["구역", "동", "호", "공시가(억)", "감정가(억)", "평형"] if c in bad_rows.columns]
        bad_show = bad_rows[["구역", "동", "호"] + cols_exist].copy().drop_duplicates()
        st.dataframe(bad_show.reset_index(drop=True), use_container_width=True)
        bad_csv = bad_show.to_csv(index=False).encode("utf-8-sig")
        st.download_button("비정상 환산감정가 목록 CSV 다운로드", bad_csv,
                           file_name=f"{zone}_비정상_환산감정가_목록.csv", mime="text/csv")

st.divider()

# ──────────────────────────────────────────────────────────────────────────────
# ⑦ 압구정 내 유사금액 10 (구역·동별 연속 층 범위) — (평형) 표기
# ──────────────────────────────────────────────────────────────────────────────
st.subheader("압구정 내 금액이 유사한 차수 10 (구역·동별 연속 층 범위)")
st.caption("※ 공시가격에 기반한 것으로 실제 시장 상황과 다를 수 있습니다.")

if pd.isna(sel_price):
    st.info("선택 세대의 환산감정가가 유효하지 않아 유사 금액을 찾을 수 없습니다.")
else:
    # 전 구역에서 환산감정가 유효 + 층 정보 계산
    pool = df.copy()
    pool = pool[pd.to_numeric(pool["감정가_클린"], errors="coerce").notna()].copy()
    pool["감정가_클린"] = pool["감정가_클린"].astype(float)
    pool["층"] = pool["호"].apply(extract_floor)

    # 선택 세대 자체는 제외
    pool = pool[~(
        (pool["구역"] == zone) &
        (pool["동"] == dong) &
        (pool["호"] == ho) &
        (np.isclose(pool["감정가_클린"], sel_price, rtol=0, atol=1e-6))
    )].copy()

    # 유사도(절대 차이) 계산 → 후보군 대량 확보
    pool["유사도"] = (pool["감정가_클린"] - sel_price).abs()
    cand = pool.sort_values(["유사도", "감정가_클린"], ascending=[True, False]).head(1000).copy()

    def _zone_num(z):
        m = re.search(r"\d+", str(z))
        return int(m.group()) if m else 10**9

    def _dong_num2(d):
        m = re.search(r"\d+", str(d))
        return int(m.group()) if m else 10**9

    def _dong_label(d):
        s = str(d)
        return s if "동" in s else f"{s}동"

    # (구역, 동)별 연속 층 범위 + (평형) 표기
    rows = []
    for (zone_name, dong_name), g in cand.dropna(subset=["층"]).groupby(["구역", "동"]):
        floors = sorted(set(int(x) for x in g["층"].dropna().tolist()))
        if not floors:
            continue
        ranges = contiguous_ranges(floors)
        ranges_str = ", ".join(format_range(s, e) for s, e in ranges)

        py = g.get("평형")
        if py is not None:
            uniq = [str(x) for x in sorted(set(py.dropna().astype(str)))]
            py_label = uniq[0] if len(uniq) == 1 else "/".join(uniq[:3]) + ("…" if len(uniq) > 3 else "")
        else:
            py_label = ""

        best_diff = float(g["유사도"].min())           # 이 (구역,동)에서 선택가와 가장 가까운 차이
        median_price = float(g["감정가_클린"].median()) # 참고용 중앙값

        rows.append({
            "구역": zone_name,
            "동": _dong_label(dong_name),
            "평형": py_label,
            "층 범위": ranges_str,
            "해당 세대수": int(len(g)),
            "최소차(억)": round(best_diff, 2),
            "중앙값 " + DISPLAY_PRICE_LABEL: round(median_price, 2),
            "_sort_zone": _zone_num(zone_name),
            "_sort_dong": _dong_num2(dong_name),
        })

    if not rows:
        st.info("유사 금액 결과가 없습니다.")
    else:
        out = pd.DataFrame(rows)
        # 정렬: 최소차 오름차순 → 해당 세대수 내림차순 → 구역번호 오름차순 → 동번호 오름차순
        out = out.sort_values(
            ["최소차(억)", "해당 세대수", "_sort_zone", "_sort_dong"],
            ascending=[True, False, True, True]
        ).head(10).drop(columns=["_sort_zone", "_sort_dong"])

        # “몇구역 몇동 (평형)” 표시열 추가(가독성)
        out.insert(0, "대상", out.apply(
            lambda r: f"{r['구역']} {r['동']}" + (f" ({r['평형']})" if str(r['평형']).strip() not in ["", "nan", "None"] else ""),
            axis=1
        ))

        st.dataframe(out.reset_index(drop=True), use_container_width=True)

        csv_sim = out.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "유사금액 범위 TOP10 CSV 다운로드",
            csv_sim,
            file_name=f"압구정_유사금액_범위_TOP10_{zone}_{dong}_{ho}.csv",
            mime="text/csv"
        )
