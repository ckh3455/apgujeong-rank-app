# apgujeong_rank_app.py
# 실행: streamlit run apgujeong_rank_app.py

import streamlit as st
import pandas as pd
import numpy as np
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from datetime import datetime
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None

# ─────────────────────────────────────────────────────────
# 기본 설정
# ─────────────────────────────────────────────────────────
st.set_page_config(page_title="압구정 구역별 감정가 순위", page_icon="🏢", layout="wide")

APP_DESCRIPTION = (
    "⚠️ 데이터는 **2025년 공동주택 공시가격(공주가)** 을 바탕으로 계산한 것으로, "
    "재건축 시 **실행될 감정평가액과 차이**가 있을 수 있습니다.\n\n"
    "이 앱은 **구역 → 동 → 호**를 선택한 뒤, **확인** 버튼을 누르면 같은 구역 내 "
    "**환산감정가(억)** 기준으로 **경쟁 순위**(공동이면 같은 순위, 다음 순위는 건너뜀)를 보여줍니다. "
    "하단 요약은 **현재 선택 세대가 속한 공동순위(같은 금액) 그룹**을 "
    "**동별 연속 층 범위**로 간소화하여 표시합니다."
)

# 표시 라벨/상수
DISPLAY_PRICE_LABEL = "환산감정가(억)"      # 공시가/0.69
PUBLIC_PRICE_LABEL  = "25년 공시가(억)"
ROUND_DECIMALS = 6          # 동점키 라운딩
DERIVE_RATIO = 0.69         # 공시가 ÷ 0.69

# 기본 구글시트 (외부공개 필요)
DEFAULT_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/1E_GAGLS7PgXFUvPiz2qsZYizKfi1mCrwez2u30OBCvI/"
    "export?format=xlsx&gid=1484463303"
)

# 프로모 박스 (모바일에서도 항상 보이도록)
PROMO_TEXT_HTML = """
<div class="promo-box">
  <div class="promo-title">📞 <strong>압구정 원 부동산</strong></div>
  <div class="promo-line">압구정 재건축 전문 컨설팅 · <strong>순위를 알고 사야하는 압구정</strong></div>
  <div class="promo-line"><strong>문의</strong></div>
  <div class="promo-line">02-540-3334 / 최이사 Mobile 010-3065-1780</div>
  <div class="promo-small">압구정 미래가치 예측.</div>
</div>
"""

# ─────────────────────────────────────────────────────────
# 스타일
# ─────────────────────────────────────────────────────────
st.markdown("""
<style>
/* 모바일 여백/폰트 */
@media (max-width: 640px) {
  .block-container { padding: 0.75rem 0.8rem !important; }
  div[data-testid="stMetricValue"] { font-size: 1.5rem !important; }
  .stButton button { width: 100% !important; padding: 0.8rem 1rem !important; }
  label, .stSelectbox label { font-size: 0.95rem !important; }
}
/* 프로모 박스 */
.promo-box { padding: 12px 14px; border-radius: 12px; background: #fafafa; border: 1px solid #eee; margin: 10px 0; }
.promo-title { font-size: 1.2rem; font-weight: 800; margin-bottom: 6px; }
.promo-line  { font-size: 1.05rem; font-weight: 600; line-height: 1.5; }
.promo-small { font-size: 1.0rem; font-weight: 700; font-style: italic; margin-top: 6px; }
@media (max-width: 640px) {
  .promo-title { font-size: 1.15rem; }
  .promo-line  { font-size: 1.0rem; }
}
/* 표 폰트 살짝 줄이기 */
.small-table .stDataFrame { font-size: 0.9rem; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────────────────
def now_local():
    """KST 기준 현재 시각 반환(실패시 UTC)"""
    try:
        if ZoneInfo is not None:
            return datetime.now(ZoneInfo("Asia/Seoul"))
    except Exception:
        pass
    return datetime.utcnow()

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

# ─────────────────────────────────────────────────────────
# 데이터 로딩
# ─────────────────────────────────────────────────────────
def load_data(source):
    """URL/로컬 모두 지원, 표준화 후 환산감정가(억) 생성(공시가 ÷ 0.69)"""
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

    # 표준 열 이름 (있으면 그대로 유지)
    # 필요한 열: ['구역','동','호','25년 공시가(억)' 또는 '공시가(억)','감정가(억)'(옵션), '평형'(옵션)]
    rename_map = {"구역":"구역","동":"동","호":"호","공시가(억)":"공시가(억)","감정가(억)":"감정가(억)","25년 공시가(억)":"25년 공시가(억)","평형":"평형"}
    df = df.rename(columns=rename_map)

    # 문자열 정리
    for c in ["구역","동","호"]:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()

    # 공시가 열 결정
    public_raw = None
    if "25년 공시가(억)" in df.columns:
        public_raw = clean_price(df["25년 공시가(억)"])
        df["25년 공시가(억)"] = public_raw
    elif "공시가(억)" in df.columns:
        public_raw = clean_price(df["공시가(억)"])
        df["25년 공시가(억)"] = public_raw  # 표준화
    else:
        df["25년 공시가(억)"] = np.nan

    # 환산감정가 = 공시가 ÷ 0.69 (공시가 없으면 감정가(억) 사용 가능)
    derived = df["25년 공시가(억)"] / DERIVE_RATIO
    fallback = clean_price(df.get("감정가(억)", pd.Series(dtype=object)))
    df["환산감정가(억)"] = derived.where(~derived.isna(), fallback)

    # 평형이 수치면 int로
    if "평형" in df.columns:
        try:
            df["평형"] = pd.to_numeric(df["평형"], errors="coerce")
        except Exception:
            pass

    return df

# ─────────────────────────────────────────────────────────
# 구글시트 로그
# ─────────────────────────────────────────────────────────
def _get_gspread_client():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        scope = ["https://www.googleapis.com/auth/spreadsheets",
                 "https://www.googleapis.com/auth/drive"]
        # secrets에 [gcp_service] 섹션 또는 루트 키로 들어온 경우 모두 처리
        if "gcp_service" in st.secrets:
            info = dict(st.secrets["gcp_service"])
        else:
            # 루트에 직접 들어있을 때
            keys = ["type","project_id","private_key_id","private_key","client_email",
                    "client_id","auth_uri","token_uri","auth_provider_x509_cert_url",
                    "client_x509_cert_url","universe_domain"]
            if "type" in st.secrets:
                info = {k: st.secrets.get(k,"") for k in keys}
            else:
                return None
        creds = Credentials.from_service_account_info(info, scopes=scope)
        return gspread.authorize(creds)
    except Exception:
        return None

USAGE_SHEET_ID = st.secrets.get("USAGE_SHEET_ID", "")
_gg_client = _get_gspread_client() if USAGE_SHEET_ID else None

def log_event_simple(date_str, time_str, zone, dong_ho, device, event="select"):
    """간소화 로그: [날짜, 시간, 구역, 동-호, device, event]"""
    if not (_gg_client and USAGE_SHEET_ID):
        return
    try:
        ws = _gg_client.open_by_key(USAGE_SHEET_ID).sheet1
        ws.append_row([date_str, time_str, zone, dong_ho, device, event], value_input_option="RAW")
        st.session_state["__last_log_ok__"] = True
    except Exception as e:
        st.session_state["__last_log_ok__"] = False
        st.session_state["__last_log_err__"] = str(e)

def detect_device():
    """브라우저 화면 너비로 모바일/PC 추정 (모듈 실패시 토글값으로 대체)"""
    try:
        from streamlit_js_eval import get_browser_info
        bi = get_browser_info()
        width = int(bi.get("windowWidth", 1200))
        return "mobile" if width < 780 else "pc"
    except Exception:
        return "mobile" if st.session_state.get("mobile_simple", True) else "pc"

# ─────────────────────────────────────────────────────────
# 선택 확정(게이트)
# ─────────────────────────────────────────────────────────
if "confirmed" not in st.session_state:
    st.session_state.confirmed = False
if "last_selection" not in st.session_state:
    st.session_state.last_selection = None  # (구역, 동, 호)

def reset_confirm():
    st.session_state.confirmed = False

# ─────────────────────────────────────────────────────────
# UI 상단
# ─────────────────────────────────────────────────────────
st.title("🏢 압구정 구역별 감정가 순위")
st.info(APP_DESCRIPTION)

top_left, top_right = st.columns([2,1])
with top_left:
    mobile_simple = st.toggle("📱 모바일 간단 보기", value=True, help="모바일에서 보기 편한 간단 레이아웃", key="mobile_simple")
with top_right:
    if st.button("🔄 데이터 새로고침"):
        st.session_state.confirmed = False
        st.rerun()

# ─────────────────────────────────────────────────────────
# 데이터 소스 선택
# ─────────────────────────────────────────────────────────
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
    if isinstance(resolved_source, str) and resolved_source.startswith(("http://","https://")):
        m = re.search(r"/spreadsheets/d/([^/]+)/", resolved_source)
        gid = parse_qs(urlparse(resolved_source).query).get("gid", [None])[0]
        st.caption(f"Doc ID: {m.group(1) if m else '-'} / gid: {gid}")

# 실제 로딩
try:
    if isinstance(resolved_source, str):
        df = load_data(resolved_source)
    else:
        df = load_data(resolved_source)
    st.success("데이터 로딩 완료")
except Exception as e:
    st.error(f"데이터를 불러오지 못했습니다: {e}")
    st.stop()

# ─────────────────────────────────────────────────────────
# 선택 UI
# ─────────────────────────────────────────────────────────
zones = sorted(df["구역"].dropna().unique().tolist())
if not zones:
    st.warning("구역 데이터가 비어 있습니다.")
    st.stop()

if mobile_simple:
    zone = st.selectbox("구역 선택", zones, index=0, key="zone", on_change=reset_confirm)
    zone_df = df[df["구역"] == zone].copy()
    dongs = sorted(zone_df["동"].dropna().unique().tolist())
    dong = st.selectbox("동 선택", dongs, index=0 if dongs else None, key="dong", on_change=reset_confirm)
    dong_df = zone_df[zone_df["동"] == dong].copy()
    hos = sorted(dong_df["호"].dropna().unique().tolist())
    ho = st.selectbox("호 선택", hos, index=0 if hos else None, key="ho", on_change=reset_confirm)
else:
    c1, c2, c3 = st.columns(3)
    with c1:
        zone = st.selectbox("구역 선택", zones, index=0, key="zone", on_change=reset_confirm)
    zone_df = df[df["구역"] == zone].copy()
    with c2:
        dongs = sorted(zone_df["동"].dropna().unique().tolist())
        dong = st.selectbox("동 선택", dongs, index=0 if dongs else None, key="dong", on_change=reset_confirm)
    dong_df = zone_df[zone_df["동"] == dong].copy()
    with c3:
        hos = sorted(dong_df["호"].dropna().unique().tolist())
        ho = st.selectbox("호 선택", hos, index=0 if hos else None, key="ho", on_change=reset_confirm)

# 확인 버튼 & 로그
colA, colB = st.columns([1, 4])
with colA:
    if st.button("확인", type="primary", use_container_width=True):
        st.session_state.confirmed = True
        st.session_state.last_selection = (zone, dong, ho)

        # 로그 기록(간소화)
        now = now_local()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M")
        device = detect_device()
        log_event_simple(date_str, time_str, zone, f"{dong}-{ho}", device, event="select")

# 확인 전까지는 결과 숨김
if not st.session_state.confirmed:
    st.info("구역 → 동 → 호를 선택한 뒤 **확인** 버튼을 누르면 결과가 표시됩니다.")
    st.stop()
if st.session_state.last_selection != (zone, dong, ho):
    st.info("선택이 변경되었습니다. **확인** 버튼을 다시 눌러주세요.")
    st.stop()

# ─────────────────────────────────────────────────────────
# 순위 계산
# ─────────────────────────────────────────────────────────
total_units_all = len(zone_df)

work = zone_df.dropna(subset=["환산감정가(억)"]).copy()
work = work[pd.to_numeric(work["환산감정가(억)"], errors="coerce").notna()].copy()
work["환산감정가(억)"] = work["환산감정가(억)"].astype(float)

bad_mask = pd.to_numeric(zone_df["환산감정가(억)"], errors="coerce").isna()
bad_rows = zone_df[bad_mask].copy()

work["가격키"] = work["환산감정가(억)"].round(ROUND_DECIMALS) if ROUND_DECIMALS is not None else work["환산감정가(억)"]
work["순위"] = work["가격키"].rank(method="min", ascending=False).astype(int)
work["공동세대수"] = work.groupby("가격키")["가격키"].transform("size")
work = work.sort_values(["가격키", "동", "호"], ascending=[False, True, True]).reset_index(drop=True)

sel_row = work[(work["동"] == dong) & (work["호"] == ho)].copy()
if sel_row.empty:
    st.warning("선택 세대가 유효한 환산감정가 집합에 없습니다.")
    st.stop()

sel_price = float(sel_row.iloc[0]["환산감정가(억)"])
sel_key   = round(sel_price, ROUND_DECIMALS) if ROUND_DECIMALS is not None else sel_price
subset = work[work["가격키"] == sel_key]
sel_rank = int(subset["순위"].min())
sel_tied = int(subset["공동세대수"].max())
total_units_valid = int(len(work))

# ─────────────────────────────────────────────────────────
# 상단 지표
# ─────────────────────────────────────────────────────────
if mobile_simple:
    st.metric("선택 구역", zone)
    st.metric("구역 전체 세대수", f"{total_units_all:,} 세대")
else:
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("선택 구역", zone)
    m2.metric("구역 전체 세대수", f"{total_units_all:,} 세대")
    # 아래에서 표로 함께 보여주므로 요약 메트릭은 간단히 유지

st.divider()

# ─────────────────────────────────────────────────────────
# 선택 세대 상세 (표)  — 동/평형 칸 좁게, 가격 2자리 반올림
# ─────────────────────────────────────────────────────────
st.subheader("선택 세대 상세")

view = sel_row[["구역","동","호"] + (["평형"] if "평형" in sel_row.columns else []) + [PUBLIC_PRICE_LABEL,"환산감정가(억)","순위","공동세대수"]].copy()
# 2자리 반올림 표시
if PUBLIC_PRICE_LABEL in view.columns:
    view[PUBLIC_PRICE_LABEL] = pd.to_numeric(view[PUBLIC_PRICE_LABEL], errors="coerce").round(2)
view["환산감정가(억)"] = pd.to_numeric(view["환산감정가(억)"], errors="coerce").round(2)

colconf = {
    "동": st.column_config.TextColumn("동", width="small"),
    "평형": st.column_config.NumberColumn("평형", width="small", format="%d") if "평형" in view.columns else None,
    PUBLIC_PRICE_LABEL: st.column_config.NumberColumn(PUBLIC_PRICE_LABEL, format="%.2f"),
    "환산감정가(억)": st.column_config.NumberColumn("환산감정가(억)", format="%.2f")
}
# None 제거
colconf = {k:v for k,v in colconf.items() if v is not None}

st.dataframe(view.reset_index(drop=True),
             use_container_width=True,
             height=120 if mobile_simple else 160,
             column_config=colconf,
             hide_index=True)

# ✅ 프로모 텍스트: 선택 세대 상세 바로 아래 (모바일에서도 항상 보임)
st.markdown(PROMO_TEXT_HTML, unsafe_allow_html=True)

# 순위 메시지
msg = f"구역 내 순위: 공동 {sel_rank}위 ({sel_tied}세대)" if sel_tied > 1 else f"구역 내 순위: {sel_rank}위"
st.success(msg)

st.divider()

# ─────────────────────────────────────────────────────────
# 공동순위 요약 (선택 금액 기준 · 동별 연속 층)
# ─────────────────────────────────────────────────────────
st.subheader("공동순위 요약 (선택 세대 금액 기준)")

tmp = work.copy()
tmp["층"] = tmp["호"].apply(extract_floor)
grp = tmp[tmp["가격키"] == sel_key].copy()
st.markdown(f"**공동 {sel_rank}위 ({sel_tied}세대)** · {DISPLAY_PRICE_LABEL}: **{round(sel_key,2):,.2f}**")

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

def _dong_num(d):
    m = re.search(r"\d+", str(d))
    return int(m.group()) if m else 10**9
rows = sorted(rows, key=lambda r: _dong_num(r["동"]))

if rows:
    out = pd.DataFrame(rows)
    st.dataframe(out, use_container_width=True, hide_index=True)
else:
    st.info("해당 공동순위 그룹에서 요약할 층 정보가 없습니다.")

# 비정상 값 안내
if not bad_rows.empty:
    with st.expander("비정상 환산감정가(숫자 아님/결측) — 제외된 행 보기", expanded=False):
        cols_exist = [c for c in ["구역","동","호",PUBLIC_PRICE_LABEL,"감정가(억)"] if c in bad_rows.columns]
        bad_show = bad_rows[["구역","동","호"] + cols_exist].copy().drop_duplicates()
        st.dataframe(bad_show.reset_index(drop=True), use_container_width=True)

st.divider()

# ─────────────────────────────────────────────────────────
# 압구정 내 유사 금액 차수 10  (구역·동·평형별 연속 층 범위) - 최소차 제거
# ─────────────────────────────────────────────────────────
st.subheader("압구정 내 금액이 유사한 차수 10 (구역·동·평형별 연속 층 범위)")
st.caption("※ 공시가격에 기반한 것으로 실제 시장 상황과 다를 수 있습니다.")

pool = df.copy()
pool = pool[pd.to_numeric(pool["환산감정가(억)"], errors="coerce").notna()].copy()
pool["환산감정가(억)"] = pool["환산감정가(억)"].astype(float)
pool["층"] = pool["호"].apply(extract_floor)

# 선택 세대 자체 제외
pool = pool[~(
    (pool["구역"] == zone) &
    (pool["동"] == dong) &
    (pool["호"] == ho) &
    (np.isclose(pool["환산감정가(억)"], sel_price, rtol=0, atol=1e-6))
)].copy()

# 유사도(절대차) 계산 → 후보군 확보
pool["유사도"] = (pool["환산감정가(억)"] - sel_price).abs()
cand = pool.sort_values(["유사도", "환산감정가(억)"], ascending=[True, False]).head(1000).copy()

def _zone_num(z):
    m = re.search(r"\d+", str(z))
    return int(m.group()) if m else 10**9
def _dong_num2(d):
    m = re.search(r"\d+", str(d))
    return int(m.group()) if m else 10**9
def _dong_label(d):
    s = str(d)
    return s if "동" in s else f"{s}동"

rows2 = []
for (zone_name, dong_name), g in cand.dropna(subset=["층"]).groupby(["구역","동"]):
    floors = sorted(set(int(x) for x in g["층"].dropna().tolist()))
    if not floors:
        continue
    ranges = contiguous_ranges(floors)
    ranges_str = ", ".join(format_range(s, e) for s, e in ranges)
    median_price = float(g["환산감정가(억)"].median())
    rows2.append({
        "구역": zone_name,
        "동(평형)": f"{_dong_label(dong_name)}" + (f" ({int(g['평형'].median())}평형)" if "평형" in g.columns and pd.notna(g["평형"].median()) else ""),
        "층 범위": ranges_str,
        "세대수": int(len(g)),
        "유사차수 환산가(억)": round(median_price, 2),
        "_sort_zone": _zone_num(zone_name),
        "_sort_dong": _dong_num2(dong_name),
    })

if not rows2:
    st.info("유사 금액 결과가 없습니다.")
else:
    out2 = pd.DataFrame(rows2).sort_values(
        ["_sort_zone", "_sort_dong", "세대수"], ascending=[True, True, False]
    ).head(10).drop(columns=["_sort_zone","_sort_dong"])
    colconf2 = {
        "층 범위": st.column_config.TextColumn("층 범위", width="small"),
        "세대수": st.column_config.NumberColumn("세대수", width="small", format="%d"),
        "유사차수 환산가(억)": st.column_config.NumberColumn("유사차수 환산가(억)", format="%.2f")
    }
    st.dataframe(out2.reset_index(drop=True), use_container_width=True, hide_index=True, column_config=colconf2)

# ─────────────────────────────────────────────────────────
# 관리자: 로그 상태
# ─────────────────────────────────────────────────────────
with st.expander("관리자: 로그 상태/테스트", expanded=False):
    st.write("USAGE_SHEET_ID 설정:", bool(USAGE_SHEET_ID))
    st.write("GSpread 클라이언트:", bool(_gg_client))
    last_ok = st.session_state.get("__last_log_ok__", None)
    if last_ok is True:
        st.success("최근 로그 기록 성공")
    elif last_ok is False:
        st.error("최근 로그 기록 실패: " + st.session_state.get("__last_log_err__", ""))
    else:
        st.info("아직 로그 기록 없음")
