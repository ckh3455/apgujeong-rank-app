# apgujeong_rank_app.py
# 실행: streamlit run apgujeong_rank_app.py

import re, sqlite3, secrets
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import streamlit as st
import os
import csv


# --- (선택) gspread가 없으면 구글시트 로깅 비활성화 ---
GSHEET_AVAILABLE = True
try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception:
    GSHEET_AVAILABLE = False

st.set_page_config(page_title="압구정 구역별 감정가 순위", page_icon="🏢", layout="wide")

# ======= 사용자 문구 / 라벨 =======
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

DEFAULT_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/1E_GAGLS7PgXFUvPiz2qsZYizKfi1mCrwez2u30OBCvI/"
    "export?format=xlsx&gid=1484463303"
)
ROUND_DECIMALS = 6

# ----- 스타일 -----
st.markdown("""
<style>
@media (max-width: 640px) {
  .block-container { padding: 0.75rem 0.8rem !important; }
  div[data-testid="stMetricValue"] { font-size: 1.5rem !important; }
  .stButton button { width: 100% !important; padding: 0.8rem 1rem !important; }
  label, .stSelectbox label { font-size: 0.95rem !important; }
}
.promo-box { padding: 10px 12px; border-radius: 10px; background: #fafafa; border: 1px solid #eee; margin: 8px 0 0 0;}
.promo-title { font-size: 1.25rem; font-weight: 800; margin-bottom: 6px; }
.promo-line  { font-size: 1.1rem;  font-weight: 600; line-height: 1.5; }
.promo-small { font-size: 1.0rem;  font-weight: 700; font-style: italic; margin-top: 6px; }
@media (max-width: 640px) {.promo-title { font-size: 1.2rem;} .promo-line{ font-size: 1.05rem;}}
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

st.title("🏢 압구정 구역별 감정가 순위")
st.info(APP_DESCRIPTION)

# ===== 사이드바: 모바일 토글 & 새로고침 & 구글시트 로그 사용 =====
with st.sidebar:
    mobile_simple = st.toggle("📱 모바일 간단 보기", value=True)
    if st.button("🔄 데이터 새로고침"):
        st.rerun()
    enable_gsheets = st.toggle(
        "구글시트 로그 사용 (배포 시 ON)",
        value=False,
        help="ON이면 구글시트에 이벤트를 기록합니다(Secrets 필요). OFF이면 로컬 SQLite만 기록."
    )

# ====================== 로깅: SQLite + (선택) Google Sheets ======================

def _db_path() -> Path:
    """사용자 로컬(OneDrive 외) 폴더에 DB 저장."""
    base = Path(os.getenv("LOCALAPPDATA") or (Path.home() / ".apgujeong_rank"))
    base = base / "ApgujeongRank"
    base.mkdir(parents=True, exist_ok=True)
    return base / "usage.db"

@st.cache_resource
def get_db():
    dbp = _db_path()
    conn = sqlite3.connect(
        str(dbp),
        check_same_thread=False,  # streamlit 멀티스레드 대비
        timeout=30                # 잠김 대기
    )
    # 잠금 충돌 완화
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS usage_events(
            ts TEXT, session_id TEXT, event TEXT,
            zone TEXT, dong TEXT, ho TEXT,
            visitor_id TEXT, campaign TEXT
        )
    """)
    conn.commit()
    return conn

def _csv_fallback_row(now_utc, event, zone, dong, ho):
    return [
        now_utc.strftime("%Y-%m-%d %H:%M:%S"),
        st.session_state.get("sid",""),
        event, zone, dong, ho,
        st.session_state.get("visitor_id",""),
        st.session_state.get("campaign",""),
    ]

def log_event(event, zone=None, dong=None, ho=None):
    """1) SQLite 기록, 2) (가능하면) Google Sheets 기록. 실패해도 앱은 계속."""
    now_utc = datetime.now(timezone.utc)

    # --- 1) SQLite (안정 커밋)
    try:
        conn = get_db()
        with conn:  # 실패 시 자동 롤백, 성공 시 커밋
            conn.execute("""
                INSERT INTO usage_events
                (ts, session_id, event, zone, dong, ho, visitor_id, campaign)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                now_utc.isoformat(),
                st.session_state.get("sid",""),
                event,
                str(zone) if zone is not None else None,
                str(dong) if dong is not None else None,
                str(ho)   if ho   is not None else None,
                st.session_state.get("visitor_id",""),
                st.session_state.get("campaign",""),
            ))
    except Exception:
        # --- SQLite가 잠김/손상 등으로 실패하면 CSV로 폴백
        try:
            csv_path = _db_path().with_suffix(".csv")
            header_needed = not csv_path.exists()
            with csv_path.open("a", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                if header_needed:
                    w.writerow(["ts_local","session_id","event","zone","dong","ho","visitor_id","campaign"])
                w.writerow(_csv_fallback_row(now_utc.astimezone(), event, zone, dong, ho))
        except Exception:
            pass  # 폴백도 실패하면 조용히 무시(앱 계속)

    # --- 2) Google Sheets (켜져 있고 설정돼 있을 때만)
    ws = get_gsheet() if 'get_gsheet' in globals() else None
    if ws is not None:
        try:
            ts_utc = now_utc.strftime("%Y-%m-%d %H:%M:%S")
            ts_kst = (now_utc + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")
            ws.append_row(
                [ts_utc, ts_kst, st.session_state.get("sid",""),
                 event, zone, dong, ho,
                 st.session_state.get("visitor_id",""),
                 st.session_state.get("campaign","")],
                value_input_option="RAW"
            )
        except Exception:
            pass

# 세션/방문자/캠페인 식별자 (최신 API)
if "sid" not in st.session_state:
    st.session_state.sid = secrets.token_hex(8)
try:
    qp_raw = dict(st.query_params)
except Exception:
    qp_raw = st.experimental_get_query_params()
qp = {k: (v[0] if isinstance(v, list) else v) for k, v in qp_raw.items()}
visitor_id = qp.get("vid") or secrets.token_hex(6)
campaign   = qp.get("utm", "")
if "vid" not in qp:
    qp_out = {**qp, "vid": visitor_id}
    try:
        st.query_params = qp_out
    except Exception:
        st.experimental_set_query_params(**qp_out)
st.session_state.visitor_id = visitor_id
st.session_state.campaign   = campaign

# 최초 진입 1회 기록
if "logged_open" not in st.session_state:
    log_event("app_open")
    st.session_state.logged_open = True

# ===== 도우미 =====
def normalize_gsheet_url(url: str) -> str:
    if not isinstance(url, str): return url
    if "docs.google.com/spreadsheets" in url and "/export" not in url:
        m = re.search(r"/spreadsheets/d/([^/]+)/", url)
        gid = parse_qs(urlparse(url).query).get("gid", [None])[0]
        if m:
            doc_id = m.group(1)
            return f"https://docs.google.com/spreadsheets/d/{doc_id}/export?format=xlsx" + (f"&gid={gid}" if gid else "")
    return url

# ===== ① 데이터 소스 =====
with st.expander("① 데이터 파일/URL 선택 — 필요한 열: ['구역','동','호','공시가(억)','감정가(억)']", expanded=False):
    uploaded = st.file_uploader("엑셀 파일 업로드 (.xlsx)", type=["xlsx"])
    manual_source = st.text_input("로컬 파일 경로 또는 Google Sheets/CSV URL (선택)", value="")
    if uploaded is not None:
        resolved_source, source_desc = uploaded, "업로드된 파일 사용"
    elif manual_source.strip():
        resolved_source, source_desc = normalize_gsheet_url(manual_source.strip()), "직접 입력 소스 사용"
    else:
        resolved_source, source_desc = DEFAULT_SHEET_URL, "기본 Google Sheets 사용"
    st.success(f"데이터 소스: {source_desc}")
    st.caption(f"현재 소스: {resolved_source if isinstance(resolved_source, str) else '업로드된 파일 객체'}")

# ===== ② 로딩 + 환산 =====
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
    is_url = isinstance(source, str) and (source.startswith("http://") or source.startswith("https://"))
    with st.spinner("데이터 불러오는 중…"):
        if is_url:
            fmt = (parse_qs(urlparse(source).query).get("format", [None])[0] or "").lower()
            df = pd.read_csv(source) if fmt == "csv" else pd.read_excel(source, sheet_name=0)
        else:
            df = pd.read_excel(Path(source), sheet_name=0)
    df = df.rename(columns={"구역":"구역","동":"동","호":"호","공시가(억)":"공시가(억)","감정가(억)":"감정가(억)"})
    for c in ["구역","동","호"]:
        if c in df.columns: df[c] = df[c].astype(str).str.strip()
    public = pd.to_numeric(df.get("공시가(억)"), errors="coerce")
    derived = public / 0.7
    fallback = clean_price(df.get("감정가(억)", pd.Series(dtype=object)))
    df["감정가_클린"] = derived.where(~derived.isna(), fallback)
    return df

try:
    df = load_data(resolved_source) if isinstance(resolved_source, str) else load_data(resolved_source)
    st.success("데이터 로딩 완료")
except Exception as e:
    st.error(f"데이터를 불러오지 못했습니다: {e}")
    st.stop()

# ===== ③ 선택(폼 X, 즉시 갱신) + 조회/기록 버튼 =====
zones = sorted(df["구역"].dropna().unique().tolist())
if not zones:
    st.warning("구역 데이터가 비어 있습니다."); st.stop()

ZONE_PH, DONG_PH, HO_PH = "— 구역 선택 —", "— 동 선택 —", "— 호 선택 —"

def _reset_dong(): st.session_state["dong_sel"] = DONG_PH; st.session_state["ho_sel"] = HO_PH
def _reset_ho():   st.session_state["ho_sel"] = HO_PH

zone_options = [ZONE_PH] + zones
zone_cur = st.session_state.get("zone_sel", ZONE_PH)
if zone_cur not in zone_options: zone_cur = ZONE_PH
zone_sel = st.selectbox("구역", zone_options, index=zone_options.index(zone_cur), key="zone_sel", on_change=_reset_dong)

dongs = sorted(df[df["구역"] == zone_sel]["동"].dropna().unique().tolist()) if zone_sel != ZONE_PH else []
dong_options = [DONG_PH] + dongs
dong_cur = st.session_state.get("dong_sel", DONG_PH)
if dong_cur not in dong_options: dong_cur = DONG_PH
dong_sel = st.selectbox("동", dong_options, index=dong_options.index(dong_cur), key="dong_sel",
                        on_change=_reset_ho, disabled=(zone_sel == ZONE_PH))

hos = sorted(df[(df["구역"] == zone_sel) & (df["동"] == dong_sel)]["호"].dropna().unique().tolist()) \
      if (zone_sel != ZONE_PH and dong_sel != DONG_PH) else []
ho_options = [HO_PH] + hos
ho_cur = st.session_state.get("ho_sel", HO_PH)
if ho_cur not in ho_options: ho_cur = HO_PH
ho_sel = st.selectbox("호", ho_options, index=ho_options.index(ho_cur), key="ho_sel",
                      disabled=(zone_sel == ZONE_PH or dong_sel == DONG_PH))

ready = (zone_sel != ZONE_PH and dong_sel != DONG_PH and ho_sel != HO_PH)
if st.button("🔎 조회 / 기록", disabled=not ready, use_container_width=mobile_simple):
    st.session_state["picked"] = {"zone": zone_sel, "dong": dong_sel, "ho": ho_sel}
    log_event("select", zone=zone_sel, dong=dong_sel, ho=ho_sel)

if "picked" not in st.session_state:
    st.info("구역·동·호를 고르고 **[🔎 조회 / 기록]** 버튼을 눌러주세요.")
    st.stop()

# ===== ④ 순위 계산 =====
zone = st.session_state["picked"]["zone"]
dong = st.session_state["picked"]["dong"]
ho   = st.session_state["picked"]["ho"]

zone_df = df[df["구역"] == zone].copy()
dong_df = zone_df[zone_df["동"] == dong].copy()
sel_df  = dong_df[dong_df["호"] == ho].copy()
if sel_df.empty:
    st.warning("선택한 동/호 데이터가 없습니다."); st.stop()

total_units_all = len(zone_df)
work = zone_df.dropna(subset=["감정가_클린"]).copy()
work = work[pd.to_numeric(work["감정가_클린"], errors="coerce").notna()].copy()
work["감정가_클린"] = work["감정가_클린"].astype(float)

bad_rows = zone_df[pd.to_numeric(zone_df["감정가_클린"], errors="coerce").isna()].copy()

work["가격키"] = work["감정가_클린"].round(ROUND_DECIMALS) if ROUND_DECIMALS is not None else work["감정가_클린"]
work["순위"] = work["가격키"].rank(method="min", ascending=False).astype(int)
work["공동세대수"] = work.groupby("가격키")["가격키"].transform("size")
work = work.sort_values(["가격키", "동", "호"], ascending=[False, True, True]).reset_index(drop=True)

sel_price = float(sel_df.iloc[0]["감정가_클린"]) if pd.notna(sel_df.iloc[0]["감정가_클린"]) else np.nan
sel_key = round(sel_price, ROUND_DECIMALS) if (pd.notna(sel_price) and ROUND_DECIMALS is not None) else sel_price

if pd.notna(sel_key):
    subset = work[work["가격키"] == sel_key]
    sel_rank = int(subset["순위"].min()) if not subset.empty else None
    sel_tied = int(subset["공동세대수"].max()) if not subset.empty else 0
else:
    sel_rank, sel_tied = None, 0

total_units_valid = int(len(work))

# ===== ⑤ 상단 지표 =====
if mobile_simple:
    st.metric("선택 구역", zone)
    st.metric("구역 전체 세대수", f"{total_units_all:,} 세대")
    st.metric("유효 세대수(환산감정가 있음)", f"{total_units_valid:,} 세대")
    st.metric(f"선택 세대 {DISPLAY_PRICE_LABEL}", f"{sel_price:,.2f}" if pd.notna(sel_price) else "-")
else:
    a,b,c,d = st.columns(4)
    a.metric("선택 구역", zone)
    b.metric("구역 전체 세대수", f"{total_units_all:,} 세대")
    c.metric("유효 세대수(환산감정가 있음)", f"{total_units_valid:,} 세대")
    d.metric(f"선택 세대 {DISPLAY_PRICE_LABEL}", f"{sel_price:,.2f}" if pd.notna(sel_price) else "-")

if pd.isna(sel_price):
    st.info("선택 세대의 환산감정가가 비어 있거나 숫자 형식이 아닙니다.")
elif sel_rank is not None:
    st.success(f"구역 내 순위: {'공동 ' if sel_tied>1 else ''}{sel_rank}위" + (f" ({sel_tied}세대)" if sel_tied>1 else ""))
else:
    st.info("선택 세대는 유효 순위 계산 집합에 포함되지 않았습니다.")

st.caption(DISPLAY_PRICE_NOTE)
st.divider()

# ===== ⑥ 선택 세대 상세 + 프로모 =====
basic_cols = ["동", "호", "감정가_클린", "순위", "공동세대수"]
full_cols  = ["구역","동","호","공시가(억)","감정가(억)","감정가_클린","순위","공동세대수"]
show_cols  = basic_cols if mobile_simple else full_cols

st.subheader("선택 세대 상세")
sel_detail = work[(work["동"] == dong) & (work["호"] == ho)].copy()
if not sel_detail.empty:
    st.dataframe(sel_detail[show_cols].rename(columns={"감정가_클린": DISPLAY_PRICE_LABEL}).reset_index(drop=True),
                 use_container_width=True, height=200 if mobile_simple else None)
else:
    st.info("선택 세대는 유효 순위 계산 집합에 없습니다.")

st.markdown(PROMO_TEXT_HTML, unsafe_allow_html=True)
st.divider()

# ===== ⑦ 공동순위 요약 (동별 연속 층 범위) =====
st.subheader("공동순위 요약 (선택 세대 금액 기준)")
def extract_floor(ho)->float:
    s=str(ho); d="".join(ch for ch in s if ch.isdigit())
    if not d: return np.nan
    return float(int(d[:-2] if len(d)>=3 else d[0] if len(d)==2 else d))
def contiguous_ranges(sorted_ints):
    r=[]; stt=pre=None
    for x in sorted_ints:
        x=int(x)
        if stt is None: stt=pre=x
        elif x==pre+1: pre=x
        else: r.append((stt,pre)); stt=pre=x
    if stt is not None: r.append((stt,pre)); return r
def format_range(s,e): return f"{s}층" if s==e else f"{s}층에서 {e}층까지"

if sel_rank is None or pd.isna(sel_key):
    st.info("선택 세대의 환산감정가가 유효하지 않아 공동순위를 계산할 수 없습니다.")
else:
    tmp = work.copy(); tmp["층"] = tmp["호"].apply(extract_floor)
    grp = tmp[tmp["가격키"] == sel_key].copy()
    st.markdown(f"**공동 {sel_rank}위 ({sel_tied}세대)** · {DISPLAY_PRICE_LABEL}: **{sel_key:,.2f}**")
    no_floor = grp["층"].isna().sum()
    if no_floor>0: st.caption(f"※ 층 정보가 없는 세대 {no_floor}건은 범위 요약에서 제외됩니다.")
    rows=[]
    for dong_name,g in grp.dropna(subset=["층"]).groupby("동"):
        floors=sorted(set(int(x) for x in g["층"].dropna().tolist()))
        if not floors: continue
        rng=", ".join(format_range(s,e) for s,e in contiguous_ranges(floors))
        rows.append({"동": f"{dong_name}동" if "동" not in str(dong_name) else str(dong_name),
                     "층 범위": rng, "세대수": len(g)})
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
    else:
        st.info("해당 공동순위 그룹에서 요약할 층 정보가 없습니다.")

# 비정상 값 안내
bad_rows = zone_df[pd.to_numeric(zone_df["감정가_클린"], errors="coerce").isna()].copy()
if not bad_rows.empty:
    with st.expander("비정상 환산감정가 행 보기", expanded=False):
        cols_exist=[c for c in ["구역","동","호","공시가(억)","감정가(억)"] if c in bad_rows.columns]
        st.dataframe(bad_rows[["구역","동","호"]+cols_exist].drop_duplicates().reset_index(drop=True), use_container_width=True)

st.divider()

# ===== ⑧ 압구정 내 금액이 유사한 차수 10 (구역·동별 연속 층 범위) =====
st.subheader("압구정 내 금액이 유사한 차수 10 (구역·동별 연속 층 범위)")
st.caption("※ 공시가격에 기반한 것으로 실제 시장 상황과 다를 수 있습니다.")

if pd.isna(sel_price):
    st.info("선택 세대의 환산감정가가 유효하지 않아 유사 금액을 찾을 수 없습니다.")
else:
    pool = df.copy()
    pool = pool[pd.to_numeric(pool["감정가_클린"], errors="coerce").notna()].copy()
    pool["감정가_클린"] = pool["감정가_클린"].astype(float)
    pool["층"] = pool["호"].apply(extract_floor)

    # 선택 세대 제외
    pool = pool[~(
        (pool["구역"] == zone) &
        (pool["동"] == dong) &
        (pool["호"] == ho) &
        (np.isclose(pool["감정가_클린"], sel_price, rtol=0, atol=1e-6))
    )].copy()

    # 유사도 계산 → 상위 후보
    pool["유사도"] = (pool["감정가_클린"] - sel_price).abs()
    cand = pool.sort_values(["유사도", "감정가_클린"], ascending=[True, False]).head(1000).copy()

    # 정렬/라벨 유틸
    def _zone_num(z):
        m = re.search(r"\d+", str(z));  return int(m.group()) if m else 10**9
    def _dong_num(d):
        m = re.search(r"\d+", str(d));  return int(m.group()) if m else 10**9
    def _dong_label(d):
        s = str(d); return s if "동" in s else f"{s}동"

    # (구역, 동) 별 연속 층 범위 요약
    rows = []
    for (zone_name, dong_name), g in cand.dropna(subset=["층"]).groupby(["구역", "동"]):
        floors = sorted(set(int(x) for x in g["층"].dropna().tolist()))
        if not floors:
            continue
        ranges = contiguous_ranges(floors)
        ranges_str = ", ".join(
            f"{s}층" if s == e else f"{s}층에서 {e}층까지" for s, e in ranges
        )
        best_diff = float(g["유사도"].min())
        median_price = float(g["감정가_클린"].median())
        rows.append({
            "구역": zone_name,
            "동": _dong_label(dong_name),
            "층 범위": ranges_str,
            "해당 세대수": int(len(g)),
            "최소차(억)": round(best_diff, 2),
            "중앙값 " + DISPLAY_PRICE_LABEL: round(median_price, 2),
            "_z": _zone_num(zone_name),
            "_d": _dong_num(dong_name),
        })

    if not rows:
        st.info("유사 금액 결과가 없습니다.")
    else:
        out = pd.DataFrame(rows).sort_values(
            ["최소차(억)", "해당 세대수", "_z", "_d"],
            ascending=[True, False, True, True]
        ).head(10).drop(columns=["_z", "_d"])

        st.dataframe(out.reset_index(drop=True), use_container_width=True)

        csv_sim = out.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "유사금액 범위 TOP10 CSV 다운로드",
            csv_sim,
            file_name=f"압구정_유사금액_범위_TOP10_{zone}_{dong}_{ho}.csv",
            mime="text/csv"
        )

