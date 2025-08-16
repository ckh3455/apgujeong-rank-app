# apgujeong_rank_app.py
# 실행: streamlit run apgujeong_rank_app.py

import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import numpy as np
import pandas as pd
import streamlit as st

# ====== 로그용 ======
from datetime import datetime, timezone, timedelta
from streamlit_js_eval import get_browser_info  # pip install streamlit-js-eval

# ====== 페이지 기본설정 ======
st.set_page_config(page_title="압구정 구역별 감정가 순위", page_icon="🏢", layout="wide")

# ====== 사용자 커스텀 문구/라벨 ======
APP_DESCRIPTION = (
    "⚠️ 데이터는 **2025년 공동주택 공시가격(공주가)** 을 바탕으로 계산한 것으로, "
    "재건축 시 **실행될 감정평가액과 차이**가 있을 수 있습니다.\n\n"
    "이 앱은 **구역 → 동 → 호**를 선택하면 같은 구역 내 **환산감정가(억)** 기준으로 "
    "**경쟁 순위**(공동이면 같은 순위, 다음 순위는 건너뜀)를 계산해 보여줍니다. "
    "하단 요약은 **현재 선택 세대가 속한 공동순위(같은 금액) 그룹**을 "
    "**동별 연속 층 범위**로 간소화하여 표시합니다."
)
DISPLAY_PRICE_LABEL = "환산감정가(억)"  # 공시가(억)/0.69
DISPLAY_PUBLIC_LABEL = "25년 공시가(억)"  # 시트에 '25년 공시가(억)'이 없으면 '공시가(억)' 사용

# ✅ 기본 Google Sheets (외부 공개 필요: '링크가 있는 모든 사용자 보기')
DEFAULT_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/1E_GAGLS7PgXFUvPiz2qsZYizKfi1mCrwez2u30OBCvI/"
    "export?format=xlsx&gid=1484463303"
)

# 동점 판정 정밀도(None이면 원값)
ROUND_DECIMALS = 6

# ====== 스타일 (모바일/데스크탑 공통) ======
st.markdown(
    """
<style>
/* 모바일일 때 여백/폰트 */
@media (max-width: 640px) {
  .block-container { padding: 0.7rem 0.6rem !important; }
  div[data-testid="stMetricValue"] { font-size: 1.35rem !important; }
  .stButton button { width: 100% !important; padding: 0.8rem 1rem !important; }
  label, .stSelectbox label, .stMarkdown p { font-size: 0.95rem !important; }
}

/* 표 폰트 살짝 축소 및 헤더 줄바꿈 허용 */
table, .stDataFrame { font-size: 0.95rem; }
th div, td div { white-space: nowrap; }

/* 선택 세대 상세 표의 헤더가 너무 길어지는 것 방지 (폰트 작게) */
.small-header th div { font-size: 0.86rem !important; }

/* 프로모 박스 */
.promo-box { 
  padding: 12px 14px; 
  border-radius: 12px; 
  background: #ffffff; 
  border: 1px solid #e8e8e8; 
  margin: 10px 0 0 0;
  box-shadow: 0 1px 2px rgba(0,0,0,.04);
}
.promo-title { font-size: 1.15rem; font-weight: 800; margin-bottom: 4px; color:#222; }
.promo-line  { font-size: 1.02rem; font-weight: 600; line-height: 1.55; color:#333; }
.promo-small { font-size: 0.98rem; font-weight: 700; font-style: italic; margin-top: 6px; color:#333; }

@media (max-width: 640px){
  .promo-title { font-size: 1.08rem; }
  .promo-line  { font-size: 1.0rem; }
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

# ====== 상단: 모바일 보기/새로고침 ======
top_left, top_right = st.columns([2, 1])
with top_left:
    mobile_simple = st.toggle("📱 모바일 간단 보기", value=True, help="모바일에서 보기 편한 간단 레이아웃")
with top_right:
    if st.button("🔄 데이터 새로고침"):
        st.rerun()

# ====== 도우미: GSheet edit URL → export URL 변환 ======
def normalize_gsheet_url(url: str) -> str:
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

# ====== 데이터 소스 선택 ======
with st.expander("① 데이터 파일/URL 선택 — 필요한 열: ['구역','동','호','공시가(억)'/'25년 공시가(억)','감정가(억)','평형']", expanded=False):
    uploaded = st.file_uploader("엑셀 파일 업로드 (.xlsx)", type=["xlsx"])
    manual_source = st.text_input("로컬 파일 경로 또는 Google Sheets/CSV URL (선택)", value="")
    same_folder_default = Path.cwd() / "압구정 공시가.xlsx"  # 로컬 파일 우선 사용 원하면 주석 해제

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
        # if same_folder_default.exists():
        #     resolved_source = str(same_folder_default)
        #     source_desc = f"같은 폴더 엑셀 사용: {same_folder_default}"

    st.success(f"데이터 소스: {source_desc}")
    st.caption(f"현재 소스: {resolved_source if isinstance(resolved_source, str) else '업로드된 파일 객체'}")
    if isinstance(resolved_source, str) and resolved_source.startswith(("http://", "https://")):
        m = re.search(r"/spreadsheets/d/([^/]+)/", resolved_source)
        gid = parse_qs(urlparse(resolved_source).query).get("gid", [None])[0]
        st.caption(f"Doc ID: {m.group(1) if m else '-'} / gid: {gid}")

# ====== 유틸 ======
def clean_price(series: pd.Series) -> pd.Series:
    """문자 섞인 가격 문자열 → 숫자(float)로 정리"""
    s = series.astype(str)
    s = (
        s.str.replace("\u00A0", "", regex=False)  # NBSP
        .str.replace(",", "", regex=False)
        .str.replace("`", "", regex=False)
        .str.replace("'", "", regex=False)
        .str.replace("억", "", regex=False)
        .str.strip()
    )
    s = s.str.replace(r"[^0-9.\-]", "", regex=True)
    return pd.to_numeric(s, errors="coerce")

def load_data(source):
    """URL이면 read_excel/CSV, 로컬이면 read_excel → 표준화 후 환산감정가 생성(공시가 ÷ 0.69, fallback: 감정가(억))."""
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

    # 열 이름 보정
    # 평형은 선택(optional)
    cols = list(df.columns)
    rename = {}
    for k in ["구역", "동", "호", "공시가(억)", "25년 공시가(억)", "감정가(억)", "평형"]:
        if k in cols:
            rename[k] = k
    df = df.rename(columns=rename)

    # 문자열 정리
    for c in ["구역", "동", "호", "평형"]:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()

    # 공시가 컬럼 탐지
    public_col = "25년 공시가(억)" if "25년 공시가(억)" in df.columns else ("공시가(억)" if "공시가(억)" in df.columns else None)
    if public_col is None:
        df["25년 공시가(억)"] = np.nan
        public_col = "25년 공시가(억)"

    public = clean_price(df.get(public_col, pd.Series(dtype=object)))
    derived = public / 0.69  # <- 요청 반영
    fallback = clean_price(df.get("감정가(억)", pd.Series(dtype=object)))
    df[DISPLAY_PUBLIC_LABEL] = public
    df["감정가_클린"] = derived.where(~derived.isna(), fallback)  # 환산감정가

    return df

# ====== 데이터 로딩 ======
try:
    if isinstance(resolved_source, str):
        df = load_data(resolved_source)
    else:
        df = pd.read_excel(resolved_source, sheet_name=0)
        # 보정
        for c in ["구역", "동", "호", "평형"]:
            if c in df.columns:
                df[c] = df[c].astype(str).str.strip()
        public_col = "25년 공시가(억)" if "25년 공시가(억)" in df.columns else ("공시가(억)" if "공시가(억)" in df.columns else None)
        if public_col is None:
            df["25년 공시가(억)"] = np.nan
            public_col = "25년 공시가(억)"
        public = clean_price(df.get(public_col, pd.Series(dtype=object)))
        derived = public / 0.69
        fallback = clean_price(df.get("감정가(억)", pd.Series(dtype=object)))
        df[DISPLAY_PUBLIC_LABEL] = public
        df["감정가_클린"] = derived.where(~derived.isna(), fallback)
    st.success("데이터 로딩 완료")
except Exception as e:
    st.error(f"데이터를 불러오지 못했습니다: {e}")
    st.stop()

# ====== 선택 UI ======
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

# ====== 순위 계산 (경쟁순위) ======
total_units_all = len(zone_df)

work = zone_df.dropna(subset=["감정가_클린"]).copy()
work = work[pd.to_numeric(work["감정가_클린"], errors="coerce").notna()].copy()
work["감정가_클린"] = work["감정가_클린"].astype(float)

bad_mask = pd.to_numeric(zone_df["감정가_클린"], errors="coerce").isna()
bad_rows = zone_df[bad_mask].copy()

work["가격키"] = work["감정가_클린"].round(ROUND_DECIMALS) if ROUND_DECIMALS is not None else work["감정가_클린"]
work["순위"] = work["가격키"].rank(method="min", ascending=False).astype(int)
work["공동세대수"] = work.groupby("가격키")["가격키"].transform("size")
work = work.sort_values(["가격키", "동", "호"], ascending=[False, True, True]).reset_index(drop=True)

# 선택 세대 값
sel_public = float(sel_df.iloc[0][DISPLAY_PUBLIC_LABEL]) if pd.notna(sel_df.iloc[0][DISPLAY_PUBLIC_LABEL]) else np.nan
sel_price = float(sel_df.iloc[0]["감정가_클린"]) if pd.notna(sel_df.iloc[0]["감정가_클린"]) else np.nan
sel_key = round(sel_price, ROUND_DECIMALS) if (pd.notna(sel_price) and ROUND_DECIMALS is not None) else sel_price

if pd.notna(sel_key):
    subset = work[work["가격키"] == sel_key]
    sel_rank = int(subset["순위"].min()) if not subset.empty else None
    sel_tied = int(subset["공동세대수"].max()) if not subset.empty else 0
else:
    sel_rank, sel_tied = None, 0

total_units_valid = int(len(work))

# ====== 간단 로그 ======
KST = timezone(timedelta(hours=9))
USAGE_SHEET_ID = st.secrets.get("USAGE_SHEET_ID", "")

def detect_device(fallback_mobile: bool = False) -> str:
    try:
        info = get_browser_info() or {}
        ua = (info.get("userAgent") or "").lower()
        width = int(info.get("screenWidth") or 0)
        if "mobi" in ua or (width and width < 768):
            return "mobile"
        return "pc"
    except Exception:
        return "mobile" if fallback_mobile else "pc"

def log_simple(event: str, zone: str = "", dong: str = "", ho: str = "", *, fallback_mobile: bool = False):
    try:
        if not USAGE_SHEET_ID:
            return
        device = detect_device(fallback_mobile=fallback_mobile)
        now = datetime.now(KST)
        row = [
            now.strftime("%Y-%m-%d"),  # date_ymd
            now.strftime("%H:%M"),     # time
            device,
            str(zone or ""),
            str(dong or ""),
            str(ho or ""),
            event,
        ]
        import gspread
        from google.oauth2 import service_account
        SCOPES = ["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
        creds = service_account.Credentials.from_service_account_info(dict(st.secrets), scopes=SCOPES)
        gc = gspread.authorize(creds)
        ws = gc.open_by_key(USAGE_SHEET_ID).sheet1
        ws.append_row(row, value_input_option="USER_ENTERED")
    except Exception:
        pass

# 처음 로드/선택 로깅
log_simple("view", zone=zone, dong=f"{dong}동", ho=str(ho), fallback_mobile=mobile_simple)

# ====== 상단 지표 ======
if mobile_simple:
    st.metric("선택 구역", zone)
    st.metric("구역 전체 세대수", f"{total_units_all:,} 세대")
    st.metric("유효 세대수(환산감정가 있음)", f"{total_units_valid:,} 세대")
else:
    m1, m2, m3, m4 = st.columns([1,1,1,1])
    m1.metric("선택 구역", zone)
    m2.metric("구역 전체 세대수", f"{total_units_all:,} 세대")
    m3.metric("유효 세대수(환산감정가 있음)", f"{total_units_valid:,} 세대")
    # 공시가 별도 표시
    if pd.notna(sel_public):
        m4.metric("선택 세대 25년 공시가(억)", f"{sel_public:,.2f}")
    else:
        m4.metric("선택 세대 25년 공시가(억)", "-")

if pd.isna(sel_price):
    st.info("선택 세대의 환산감정가가 비어 있거나 숫자 형식이 아닙니다. 순위 계산에서 제외됩니다.")
elif sel_rank is not None:
    msg = f"구역 내 순위: 공동 {sel_rank}위 ({sel_tied}세대)" if sel_tied > 1 else f"구역 내 순위: {sel_rank}위"
    st.success(msg)
else:
    st.info("선택 세대는 유효 순위 계산 집합에 포함되지 않았습니다.")

st.caption("※ 환산감정가는 공시가(억)를 0.69로 나눈 값입니다.")
st.divider()

# ====== 선택 세대 상세 (표 + 프로모 박스) ======
st.subheader("선택 세대 상세")

detail = work[(work["동"] == dong) & (work["호"] == ho)].copy()
# detail이 비어 있을 수 있으니, 원본 df에서 보강
if detail.empty:
    detail = sel_df.copy()
    # 랭크/공동수 보강
    if pd.notna(sel_key):
        detail["가격키"] = sel_key
        detail["순위"] = sel_rank if sel_rank is not None else np.nan
        detail["공동세대수"] = sel_tied

# 보기용 컬럼 구성
cols_show = ["구역","동","호"]
if "평형" in detail.columns:
    cols_show += ["평형"]
cols_show += [DISPLAY_PUBLIC_LABEL, "감정가_클린", "순위", "공동세대수"]
detail_view = detail[cols_show].copy()
detail_view = detail_view.rename(columns={
    "감정가_클린": DISPLAY_PRICE_LABEL
})

# 숫자 포맷(소수 2자리)
for c in [DISPLAY_PUBLIC_LABEL, DISPLAY_PRICE_LABEL]:
    if c in detail_view.columns:
        detail_view[c] = pd.to_numeric(detail_view[c], errors="coerce").round(2)

# 좁은 열 폭: 동/평형
col_config = {}
if "동" in detail_view.columns:
    col_config["동"] = st.column_config.TextColumn("동", width="small")
if "평형" in detail_view.columns:
    col_config["평형"] = st.column_config.TextColumn("평형", width="small")

st.dataframe(
    detail_view.reset_index(drop=True),
    use_container_width=True,
    hide_index=True,
    column_config=col_config,
)

# ===== 프로모 (항상 표시) =====
st.markdown(PROMO_TEXT_HTML, unsafe_allow_html=True)
st.divider()

# ====== 공동순위 요약 (선택 세대 금액 기준) ======
st.subheader("공동순위 요약 (선택 세대 금액 기준)")

def extract_floor(ho) -> float:
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
    return f"{s}층" if s == e else f"{s}층~{e}층"

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
        rows.append({"동": f"{dong_name}동" if "동" not in str(dong_name) else str(dong_name),
                     "층 범위": ranges_str, "세대수": len(g)})

    # 동 숫자 정렬
    def _dong_num(d):
        m = re.search(r"\d+", str(d))
        return int(m.group()) if m else 10**9

    rows = sorted(rows, key=lambda r: _dong_num(r["동"]))

    if rows:
        out = pd.DataFrame(rows)
        st.dataframe(
            out,
            use_container_width=True,
            hide_index=True,
            column_config={
                "동": st.column_config.TextColumn("동", width="small"),
                "층 범위": st.column_config.TextColumn("층 범위", width="medium"),
                "세대수": st.column_config.NumberColumn("세대수", width="small"),
            },
        )
        csv_agg = out.to_csv(index=False).encode("utf-8-sig")
        st.download_button("현재 공동순위 요약 CSV 다운로드", csv_agg,
                           file_name=f"{zone}_공동{sel_rank}위_동별층요약.csv", mime="text/csv")
    else:
        st.info("해당 공동순위 그룹에서 요약할 층 정보가 없습니다.")

# 비정상 값 안내
if not bad_rows.empty:
    st.warning(f"환산감정가 비정상 값 {len(bad_rows)}건 발견 — 유효 세대수에서 제외됩니다.")
    with st.expander("비정상 환산감정가 행 보기 / 다운로드", expanded=False):
        cols_exist = [c for c in ["구역","동","호",DISPLAY_PUBLIC_LABEL,"감정가(억)"] if c in bad_rows.columns]
        bad_show = bad_rows[["구역","동","호"] + cols_exist].copy().drop_duplicates()
        st.dataframe(bad_show.reset_index(drop=True), use_container_width=True)
        bad_csv = bad_show.to_csv(index=False).encode("utf-8-sig")
        st.download_button("비정상 환산감정가 목록 CSV 다운로드", bad_csv,
                           file_name=f"{zone}_비정상_환산감정가_목록.csv", mime="text/csv")

st.divider()

# ====== 압구정 내 유사금액 10 (구역·동(평형)·층 범위·세대수·대표 환산가) ======
st.subheader("압구정 내 금액이 유사한 차수 10 (구역·동(평형)·층 범위·세대수·대표 환산가)")

if pd.isna(sel_price):
    st.info("선택 세대의 환산감정가가 유효하지 않아 유사 금액을 찾을 수 없습니다.")
else:
    pool = df.copy()
    pool = pool[pd.to_numeric(pool["감정가_클린"], errors="coerce").notna()].copy()
    pool["감정가_클린"] = pool["감정가_클린"].astype(float)
    pool["층"] = pool["호"].apply(extract_floor)

    # 선택 세대 자체 제외
    pool = pool[~(
        (pool["구역"] == zone) &
        (pool["동"] == dong) &
        (pool["호"] == ho) &
        (np.isclose(pool["감정가_클린"], sel_price, rtol=0, atol=1e-6))
    )].copy()

    pool["유사도"] = (pool["감정가_클린"] - sel_price).abs()
    cand = pool.sort_values(["유사도", "감정가_클린"], ascending=[True, False]).head(1500).copy()

    def _zone_num(z):
        m = re.search(r"\d+", str(z))
        return int(m.group()) if m else 10**9
    def _dong_num(d):
        m = re.search(r"\d+", str(d))
        return int(m.group()) if m else 10**9
    def _dong_label(d, p):
        base = f"{d}동" if "동" not in str(d) else str(d)
        return f"{base} ({p})" if pd.notna(p) and str(p).strip() not in ["", "nan", "None"] else base

    rows2 = []
    group_cols = ["구역","동"]
    if "평형" in cand.columns:
        group_cols.append("평형")

    for key, g in cand.dropna(subset=["층"]).groupby(group_cols):
        zone_name, dong_name = key[0], key[1]
        pyeong = key[2] if len(key) > 2 else np.nan

        floors = sorted(set(int(x) for x in g["층"].dropna().tolist()))
        if not floors:
            continue
        ranges = contiguous_ranges(floors)
        ranges_str = ", ".join(format_range(s, e) for s, e in ranges)

        representative = float(g["감정가_클린"].median())  # 대표 환산가: 중앙값
        rows2.append({
            "구역": zone_name,
            "동(평형)": _dong_label(dong_name, pyeong),
            "층 범위": ranges_str,
            "세대수": int(len(g)),
            "대표 " + DISPLAY_PRICE_LABEL: round(representative, 2),
            "_sort_zone": _zone_num(zone_name),
            "_sort_dong": _dong_num(dong_name),
        })

    if not rows2:
        st.info("유사 금액 결과가 없습니다.")
    else:
        out2 = pd.DataFrame(rows2)
        out2 = out2.sort_values(
            ["_sort_zone", "_sort_dong", "세대수"],
            ascending=[True, True, False]
        ).head(10).drop(columns=["_sort_zone","_sort_dong"])

        st.dataframe(
            out2.reset_index(drop=True),
            use_container_width=True,
            hide_index=True,
            column_config={
                "동(평형)": st.column_config.TextColumn("동(평형)", width="small"),
                "층 범위": st.column_config.TextColumn("층 범위", width="medium"),
                "세대수": st.column_config.NumberColumn("세대수", width="small"),
                "대표 " + DISPLAY_PRICE_LABEL: st.column_config.NumberColumn("대표 " + DISPLAY_PRICE_LABEL, width="small"),
            },
        )

        csv_sim = out2.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "유사금액 범위 TOP10 CSV 다운로드",
            csv_sim,
            file_name=f"압구정_유사금액_범위_TOP10_{zone}_{dong}_{ho}.csv",
            mime="text/csv",
        )
