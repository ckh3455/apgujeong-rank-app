# apgujeong_rank_app.py
# 실행: streamlit run apgujeong_rank_app.py

import streamlit as st
import pandas as pd
import numpy as np
import re, json, uuid
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone, timedelta

# ===== 페이지 설정 =====
st.set_page_config(page_title="압구정 구역별 감정가 순위", page_icon="🏢", layout="wide")

# ===== 사용자 문구/라벨 =====
APP_DESCRIPTION = (
    "⚠️ 데이터는 **2025년 공동주택 공시가격(공주가)** 을 바탕으로 계산한 것으로, "
    "재건축 시 **실행될 감정평가액과 차이**가 있을 수 있습니다.\n\n"
    "이 앱은 **구역 → 동 → 호**를 선택하면 같은 구역 내 **환산감정가(억)** 기준으로 "
    "**경쟁 순위**(공동이면 같은 순위, 다음 순위는 건너뜀)를 계산해 보여줍니다. "
    "하단 요약은 **현재 선택 세대가 속한 공동순위(같은 금액) 그룹**을 "
    "**동별 연속 층 범위**로 간소화하여 표시합니다."
)
DISPLAY_PRICE_LABEL = "환산감정가(억)"
DISPLAY_PRICE_NOTE  = "※ 환산감정가는 공시가(억)를 0.7로 나눈 값입니다."

# ✅ 기본 Google Sheets (외부 공개 필요: '링크가 있는 모든 사용자 보기')
DEFAULT_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/1E_GAGLS7PgXFUvPiz2qsZYizKfi1mCrwez2u30OBCvI/"
    "export?format=xlsx&gid=1484463303"
)

# 동점 판정 정밀도(None이면 원값)
ROUND_DECIMALS = 6

# ===== 스타일 (모바일 + 프로모 박스 + 다크/라이트 대비) =====
st.markdown("""
<style>
@media (max-width: 640px) {
  .block-container { padding: 0.75rem 0.8rem !important; }
  div[data-testid="stMetricValue"] { font-size: 1.5rem !important; }
  .stButton button { width: 100% !important; padding: 0.8rem 1rem !important; }
  label, .stSelectbox label { font-size: 0.95rem !important; }
}
/* 프로모 박스(라이트) */
.promo-box { 
  padding: 12px 14px; border-radius: 12px; 
  background:#fffbe6; border:1px solid #f59e0b; color:#111;
  margin: 6px 0 10px 0;
}
.promo-title { font-size: 1.25rem; font-weight: 800; margin-bottom: 6px; }
.promo-line  { font-size: 1.1rem;  font-weight: 600; line-height: 1.55; }
.promo-small { font-size: 1.0rem;  font-weight: 700; font-style: italic; margin-top: 6px; }
/* 다크 테마 */
@media (prefers-color-scheme: dark) {
  .promo-box { background:#2b2b1f; border-color:#f59e0b; color:#fff; }
}
</style>
""", unsafe_allow_html=True)

PROMO_TEXT_HTML = """
<div class="promo-box">
  <div class="promo-title">📞 압구정 원 부동산</div>
  <div class="promo-line">압구정 재건축 전문 컨설팅 · 순위를 알고 사야하는 압구정</div>
  <div class="promo-line"><strong>문의</strong></div>
  <div class="promo-line">02-540-3334 / 최이사 Mobile 010-3065-1780</div>
  <div class="promo-small">압구정 미래가치 예측.</div>
</div>
"""

# ===== 유틸 =====
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

def clean_price(series: pd.Series) -> pd.Series:
    s = series.astype(str)
    s = (s.str.replace('\u00A0','', regex=False)
           .str.replace(',', '', regex=False)
           .str.replace('`', '', regex=False)
           .str.replace("'", '', regex=False)
           .str.replace('억', '', regex=False)
           .str.strip())
    s = s.str.replace(r'[^0-9.\-]', '', regex=True)
    return pd.to_numeric(s, errors='coerce')

def load_data(source):
    is_url = isinstance(source, str) and source.startswith(("http://","https://"))
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

    # 표준화
    df = df.rename(columns={
        "구역":"구역","동":"동","호":"호","공시가(억)":"공시가(억)","감정가(억)":"감정가(억)"
    })
    for c in ["구역","동","호"]:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()

    # ✅ 평형이 있으면 문자열 정리
    if "평형" in df.columns:
        df["평형"] = df["평형"].astype(str).str.strip()

    # 환산감정가 = 공시가/0.7, 없으면 감정가(억)로 대체
    public = pd.to_numeric(df.get("공시가(억)"), errors="coerce")
    derived = public / 0.7
    fallback = clean_price(df.get("감정가(억)", pd.Series(dtype=object)))
    df["감정가_클린"] = derived.where(~derived.isna(), fallback)

    return df

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
    ranges, start, prev = [], None, None
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

def _fmt_pyeong(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    s = str(x).strip()
    if not s:
        return ""
    return s if "평" in s else f"{s}평형"

# ====== Google Sheets 로깅 ======
def _load_service_account_from_secrets():
    try:
        if "gcp_service_account" in st.secrets:
            return dict(st.secrets["gcp_service_account"])
        if "google_service_account" in st.secrets:
            return dict(st.secrets["google_service_account"])
        if "gcp_service_account_json" in st.secrets:
            return json.loads(st.secrets["gcp_service_account_json"])
    except Exception:
        pass
    return None

def _get_gspread_client():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except Exception:
        return None, "gspread_not_available"

    sa_info = _load_service_account_from_secrets()
    if not sa_info:
        return None, "no_service_account"

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(sa_info, scopes=scopes)
    try:
        client = gspread.authorize(creds)
        return client, None
    except Exception as e:
        return None, f"auth_error: {e}"

def _get_sheet():
    sheet_id = st.secrets.get("USAGE_SHEET_ID", "")
    if not sheet_id:
        return None, "no_sheet_id"
    client, err = _get_gspread_client()
    if err or client is None:
        return None, err
    try:
        sh = client.open_by_key(sheet_id)
        ws = sh.sheet1
        return ws, None
    except Exception as e:
        return None, f"open_sheet_error: {e}"

def _kst_now_str():
    kst = timezone(timedelta(hours=9))
    return datetime.now(tz=kst).strftime("%Y-%m-%d %H:%M:%S")

def log_event(event, extra=None):
    ws, err = _get_sheet()
    if err or ws is None:
        return False, err

    session_id = st.session_state.get("session_id") or str(uuid.uuid4())
    st.session_state["session_id"] = session_id

    now = _kst_now_str()
    req_id = st.session_state.get("request_id") or str(uuid.uuid4())
    st.session_state["request_id"] = req_id

    zone = (extra or {}).get("zone", "")
    dong = (extra or {}).get("dong", "")
    ho   = (extra or {}).get("ho", "")

    row = [now, now, session_id, event, zone, dong, ho, req_id]
    try:
        ws.append_row(row, value_input_option="RAW")
        return True, None
    except Exception as e:
        return False, str(e)

def log_selection_if_ready():
    z = st.session_state.get("zone")
    d = st.session_state.get("dong")
    h = st.session_state.get("ho")
    if z and d and h:
        log_event("select", {"zone": str(z), "dong": str(d), "ho": str(h)})

# ===== 앱 UI =====
st.title("🏢 압구정 구역별 감정가 순위")
st.info(APP_DESCRIPTION)

# 보기 모드 & 새로고침
left, right = st.columns([2,1])
with left:
    mobile_simple = st.toggle("📱 모바일 간단 보기", value=True, help="모바일에서 보기 편한 간단 레이아웃")
with right:
    if st.button("🔄 데이터 새로고침"):
        st.rerun()

# 데이터 소스
with st.expander("① 데이터 파일/URL 선택 — 필요한 열: ['구역','동','호','공시가(억)','감정가(억)']", expanded=False):
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
    if isinstance(resolved_source, str) and resolved_source.startswith(("http://","https://")):
        m = re.search(r"/spreadsheets/d/([^/]+)/", resolved_source)
        gid = parse_qs(urlparse(resolved_source).query).get("gid", [None])[0]
        st.caption(f"Doc ID: {m.group(1) if m else '-'} / gid: {gid}")

# 로드
try:
    if isinstance(resolved_source, str):
        df = load_data(resolved_source)
    else:
        df = pd.read_excel(resolved_source, sheet_name=0)
        df = df.rename(columns={"구역":"구역","동":"동","호":"호","공시가(억)":"공시가(억)","감정가(억)":"감정가(억)"})
        for c in ["구역","동","호"]:
            df[c] = df[c].astype(str).str.strip()
        if "평형" in df.columns:
            df["평형"] = df["평형"].astype(str).str.strip()
        public = pd.to_numeric(df.get("공시가(억)"), errors="coerce")
        derived = public / 0.7
        fallback = clean_price(df.get("감정가(억)", pd.Series(dtype=object)))
        df["감정가_클린"] = derived.where(~derived.isna(), fallback)
    st.success("데이터 로딩 완료")
except Exception as e:
    st.error(f"데이터를 불러오지 못했습니다: {e}")
    st.stop()

# 선택 UI
zones = sorted(df["구역"].dropna().unique().tolist())
if not zones:
    st.warning("구역 데이터가 비어 있습니다.")
    st.stop()

if "session_id" not in st.session_state:
    st.session_state["session_id"] = str(uuid.uuid4())

# 앱 열림 로그(조용히)
log_event("app_open", {})

# 셀렉트박스 (선택 시 자동 기록)
if mobile_simple:
    zone = st.selectbox("구역 선택", zones, index=None, placeholder="구역 선택",
                        key="zone", on_change=log_selection_if_ready)
    zone_df = df[df["구역"] == st.session_state.get("zone")].copy()

    dongs = sorted(zone_df["동"].dropna().unique().tolist())
    dong = st.selectbox("동 선택", dongs, index=None, placeholder="동 선택",
                        key="dong", on_change=log_selection_if_ready)
    dong_df = zone_df[zone_df["동"] == st.session_state.get("dong")].copy()

    hos = sorted(dong_df["호"].dropna().unique().tolist())
    ho = st.selectbox("호 선택", hos, index=None, placeholder="호 선택",
                      key="ho", on_change=log_selection_if_ready)
else:
    c1,c2,c3 = st.columns(3)
    with c1:
        zone = st.selectbox("구역 선택", zones, index=None, placeholder="구역 선택",
                            key="zone", on_change=log_selection_if_ready)
    zone_df = df[df["구역"] == st.session_state.get("zone")].copy()
    with c2:
        dongs = sorted(zone_df["동"].dropna().unique().tolist())
        dong = st.selectbox("동 선택", dongs, index=None, placeholder="동 선택",
                            key="dong", on_change=log_selection_if_ready)
    dong_df = zone_df[zone_df["동"] == st.session_state.get("dong")].copy()
    with c3:
        hos = sorted(dong_df["호"].dropna().unique().tolist())
        ho = st.selectbox("호 선택", hos, index=None, placeholder="호 선택",
                          key="ho", on_change=log_selection_if_ready)

# 선택 검증
if not st.session_state.get("zone") or not st.session_state.get("dong") or not st.session_state.get("ho"):
    st.warning("구역 → 동 → 호를 모두 선택해 주세요.")
    st.stop()

# 유효성/순위 계산
total_units_all = len(zone_df)

work = zone_df.dropna(subset=["감정가_클린"]).copy()
work = work[pd.to_numeric(work["감정가_클린"], errors="coerce").notna()].copy()
work["감정가_클린"] = work["감정가_클린"].astype(float)

bad_mask = pd.to_numeric(zone_df["감정가_클린"], errors="coerce").isna()
bad_rows = zone_df[bad_mask].copy()

# 동점 키 + 경쟁순위
work["가격키"] = work["감정가_클린"].round(ROUND_DECIMALS) if ROUND_DECIMALS is not None else work["감정가_클린"]
work["순위"] = work["가격키"].rank(method="min", ascending=False).astype(int)
work["공동세대수"] = work.groupby("가격키")["가격키"].transform("size")
work = work.sort_values(["가격키", "동", "호"], ascending=[False, True, True]).reset_index(drop=True)

# 선택 세대 정보
sel_row = work[(work["동"] == st.session_state["dong"]) & (work["호"] == st.session_state["ho"])]
sel_price = float(sel_row["감정가_클린"].iloc[0]) if not sel_row.empty else np.nan
sel_key   = round(sel_price, ROUND_DECIMALS) if (pd.notna(sel_price) and ROUND_DECIMALS is not None) else sel_price
if pd.notna(sel_key):
    subset = work[work["가격키"] == sel_key]
    sel_rank = int(subset["순위"].min()) if not subset.empty else None
    sel_tied = int(subset["공동세대수"].max()) if not subset.empty else 0
else:
    sel_rank, sel_tied = None, 0
total_units_valid = int(len(work))

# ===== 상단 지표 =====
if mobile_simple:
    st.metric("선택 구역", st.session_state["zone"])
    st.metric("구역 전체 세대수", f"{total_units_all:,} 세대")
    st.metric("유효 세대수(환산감정가 있음)", f"{total_units_valid:,} 세대")
    st.metric(f"선택 세대 {DISPLAY_PRICE_LABEL}", f"{sel_price:,.2f}" if pd.notna(sel_price) else "-")
    # 모바일: 상단에 프로모션 즉시 노출
    st.markdown(PROMO_TEXT_HTML, unsafe_allow_html=True)
    st.divider()
else:
    m1,m2,m3,m4 = st.columns(4)
    m1.metric("선택 구역", st.session_state["zone"])
    m2.metric("구역 전체 세대수", f"{total_units_all:,} 세대")
    m3.metric("유효 세대수(환산감정가 있음)", f"{total_units_valid:,} 세대")
    m4.metric(f"선택 세대 {DISPLAY_PRICE_LABEL}", f"{sel_price:,.2f}" if pd.notna(sel_price) else "-")

if pd.isna(sel_price):
    st.info("선택 세대의 환산감정가가 비어 있거나 숫자 형식이 아닙니다. 순위 계산에서 제외됩니다.")
elif sel_rank is not None:
    msg = f"구역 내 순위: 공동 {sel_rank}위 ({sel_tied}세대)" if sel_tied > 1 else f"구역 내 순위: {sel_rank}위"
    st.success(msg)
else:
    st.info("선택 세대는 유효 순위 계산 집합에 포함되지 않았습니다.")

st.caption(DISPLAY_PRICE_NOTE)
st.divider()

# ===== 선택 세대 상세 =====
basic_cols = ["동", "호", "감정가_클린", "순위", "공동세대수"]
full_cols  = ["구역", "동", "호", "공시가(억)", "감정가(억)", "감정가_클린", "순위", "공동세대수"]

# 평형 컬럼이 있으면 '호' 다음에 끼워 넣기
def _inject_pyeong(cols):
    cols = cols.copy()
    if "평형" in df.columns and "평형" not in cols:
        try:
            i = cols.index("호") + 1
        except ValueError:
            i = len(cols)
        cols = cols[:i] + ["평형"] + cols[i:]
    return cols

show_cols = _inject_pyeong(basic_cols if mobile_simple else full_cols)

st.subheader("선택 세대 상세")

# 선택한 동·호 옆에 평형 캡션
sel_pyeong = sel_row["평형"].iloc[0] if ("평형" in sel_row.columns and not sel_row.empty) else None
if sel_pyeong:
    st.caption(f"선택: **{st.session_state['dong']}동 {st.session_state['ho']}호** ({_fmt_pyeong(sel_pyeong)})")

if not sel_row.empty:
    sel_view = sel_row[show_cols].rename(columns={"감정가_클린": DISPLAY_PRICE_LABEL})
    st.dataframe(sel_view.reset_index(drop=True),
                 use_container_width=True, height=200 if mobile_simple else None)
else:
    st.info("선택 세대는 유효 순위 계산 집합에 없습니다.")

# 데스크톱에서는 기존 위치에 프로모션 노출
if not mobile_simple:
    st.markdown("---")
    st.markdown(PROMO_TEXT_HTML, unsafe_allow_html=True)
    st.markdown("---")

# ===== 공동순위 요약 (선택 세대 금액 기준) =====
st.subheader("공동순위 요약 (선택 세대 금액 기준)")
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
        floors = sorted(set(int(x) for x in g["층"].dropna()))
        if not floors:
            continue
        ranges = contiguous_ranges(floors)
        ranges_str = ", ".join(format_range(s, e) for s, e in ranges)
        rows.append({"동": f"{dong_name}동" if "동" not in str(dong_name) else str(dong_name),
                     "층 범위": ranges_str, "세대수": len(g)})

    def _dong_num(d):
        m = re.search(r"\d+", str(d))
        return int(m.group()) if m else 10**9
    rows = sorted(rows, key=lambda r: _dong_num(r["동"]))

    if rows:
        out = pd.DataFrame(rows)
        st.dataframe(out, use_container_width=True)
        csv_agg = out.to_csv(index=False).encode("utf-8-sig")
        st.download_button("현재 공동순위 요약 CSV 다운로드", csv_agg,
                           file_name=f"{st.session_state['zone']}_공동{sel_rank}위_동별층요약.csv",
                           mime="text/csv")
    else:
        st.info("해당 공동순위 그룹에서 요약할 층 정보가 없습니다.")

# 비정상 값 안내
if not bad_rows.empty:
    st.warning(f"환산감정가 비정상 값 {len(bad_rows)}건 발견 — 유효 세대수에서 제외됩니다.")
    with st.expander("비정상 환산감정가 행 보기 / 다운로드", expanded=False):
        cols_exist = [c for c in ["구역","동","호","공시가(억)","감정가(억)"] if c in bad_rows.columns]
        if "평형" in bad_rows.columns:
            cols_exist = ["평형"] + cols_exist
        show_cols_bad = ["구역","동","호"] + cols_exist
        bad_show = bad_rows[show_cols_bad].copy().drop_duplicates()
        st.dataframe(bad_show.reset_index(drop=True), use_container_width=True)
        bad_csv = bad_show.to_csv(index=False).encode("utf-8-sig")
        st.download_button("비정상 환산감정가 목록 CSV 다운로드", bad_csv,
                           file_name=f"{st.session_state['zone']}_비정상_환산감정가_목록.csv", mime="text/csv")

st.divider()

# ===== 압구정 내 유사금액 10 (구역·동별 연속 층 범위) =====
st.subheader("압구정 내 금액이 유사한 차수 10 (구역·동별 연속 층 범위)")
st.caption("※ 공시가격에 기반한 것으로 실제 시장 상황과 다를 수 있습니다.")
if pd.isna(sel_price):
    st.info("선택 세대의 환산감정가가 유효하지 않아 유사 금액을 찾을 수 없습니다.")
else:
    pool = df.copy()
    pool = pool[pd.to_numeric(pool["감정가_클린"], errors="coerce").notna()].copy()
    pool["감정가_클린"] = pool["감정가_클린"].astype(float)
    pool["층"] = pool["호"].apply(extract_floor)

    # 선택 세대 자체 제외
    pool = pool[~(
        (pool["구역"] == st.session_state["zone"]) &
        (pool["동"] == st.session_state["dong"]) &
        (pool["호"] == st.session_state["ho"]) &
        (np.isclose(pool["감정가_클린"], sel_price, rtol=0, atol=1e-6))
    )].copy()

    pool["유사도"] = (pool["감정가_클린"] - sel_price).abs()
    cand = pool.sort_values(["유사도", "감정가_클린"], ascending=[True, False]).head(1000).copy()

    def _zone_num(z):
        m = re.search(r"\d+", str(z))
        return int(m.group()) if m else 10**9
    def _dong_num(d):
        m = re.search(r"\d+", str(d))
        return int(m.group()) if m else 10**9
    def _dong_label(d):
        s = str(d);  return s if "동" in s else f"{s}동"

    rows = []

    # ✅ 평형이 있으면 ['구역','동','평형'] 기준으로 그룹핑 → 동 (평형) 형식으로 표기
    group_cols = ["구역", "동"] + (["평형"] if "평형" in cand.columns else [])

    for keys, g in cand.dropna(subset=["층"]).groupby(group_cols):
        if len(group_cols) == 3:
            zone_name, dong_name, pyeong_val = keys
        else:
            zone_name, dong_name = keys
            pyeong_val = None

        floors = sorted(set(int(x) for x in g["층"].dropna().tolist()))
        if not floors:
            continue
        ranges = contiguous_ranges(floors)
        ranges_str = ", ".join(format_range(s, e) for s, e in ranges)

        best_diff = float(g["유사도"].min())
        median_price = float(g["감정가_클린"].median())

        dong_disp = _dong_label(dong_name)
        if pyeong_val not in [None, "", np.nan]:
            dong_disp = f"{dong_disp} ({_fmt_pyeong(pyeong_val)})"

        rows.append({
            "구역": zone_name,
            "동": dong_disp,
            "층 범위": ranges_str,
            "해당 세대수": int(len(g)),
            "최소차(억)": round(best_diff, 2),
            "중앙값 " + DISPLAY_PRICE_LABEL: round(median_price, 2),
            "_sort_zone": _zone_num(zone_name),
            "_sort_dong": _dong_num(dong_name),
        })

    if not rows:
        st.info("유사 금액 결과가 없습니다.")
    else:
        out = pd.DataFrame(rows).sort_values(
            ["최소차(억)", "해당 세대수", "_sort_zone", "_sort_dong"],
            ascending=[True, False, True, True]
        ).head(10).drop(columns=["_sort_zone","_sort_dong"])
        st.dataframe(out.reset_index(drop=True), use_container_width=True)
        csv_sim = out.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "유사금액 범위 TOP10 CSV 다운로드",
            csv_sim,
            file_name=f"압구정_유사금액_범위_TOP10_{st.session_state['zone']}_{st.session_state['dong']}_{st.session_state['ho']}.csv",
            mime="text/csv"
        )

# ===== 조회/확인 버튼 (선택 기록 보강) =====
if st.button("확인", type="primary"):
    log_selection_if_ready()
    st.success("선택이 기록되었습니다. (시트 반영까지 약간의 지연이 있을 수 있습니다)")
