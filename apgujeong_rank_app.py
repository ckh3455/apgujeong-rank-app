# apgujeong_rank_app.py
# 실행: streamlit run apgujeong_rank_app.py

import streamlit as st
import pandas as pd
import numpy as np
import re, uuid
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ===== 페이지 세팅 =====
st.set_page_config(
    page_title="압구정 구역별 감정가 순위",
    page_icon="🏢",
    layout="wide"
)

# ====== 사용자 커스텀 문구/상수 ======
APP_DESCRIPTION = (
    "⚠️ 데이터는 **2025년 공동주택 공시가격(공주가)** 을 바탕으로 계산한 것으로, "
    "재건축 시 **실행될 감정평가액과 차이**가 있을 수 있습니다.\n\n"
    "이 앱은 **구역 → 동 → 호**를 선택하면 같은 구역 내 **환산감정가(억)** 기준으로 "
    "**경쟁 순위**(공동이면 같은 순위, 다음 순위는 건너뜀)를 계산해 보여줍니다. "
    "하단 요약은 **현재 선택 세대가 속한 공동순위(같은 금액) 그룹**을 "
    "**동별(또는 동·평형별) 연속 층 범위**로 간소화하여 표시합니다."
)

DISPLAY_PRICE_LABEL = "환산감정가(억)"   # 보여줄 환산가 라벨
PUBLIC_PRICE_LABEL  = "25년 공시가(억)"   # 25년 공시가 라벨
ROUND_DECIMALS      = 6                  # 동점 판정 소수 라운딩
ADJUST_DIVISOR      = 0.69               # 환산감정가 = 공시가 ÷ 0.69 (요청 반영)

# 기본 구글시트 데이터 소스(외부 공개: 링크 있는 모든 사용자 보기)
DEFAULT_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1E_GAGLS7PgXFUvPiz2qsZYizKfi1mCrwez2u30OBCvI/"
    "export?format=xlsx&gid=1484463303"
)

# 프로모(업소 홍보) 카드
PROMO_HTML = """
<div class="promo-box">
  <div class="promo-title">📞 압구정 원 부동산</div>
  <div class="promo-line">압구정 재건축 전문 컨설팅 · 순위를 알고 사야하는 압구정</div>
  <div class="promo-line"><strong>문의</strong></div>
  <div class="promo-line">02-540-3334 / 최이사 Mobile 010-3065-1780</div>
  <div class="promo-small">압구정 미래가치 예측.</div>
</div>
"""

# ====== 스타일: 모바일/데스크탑 모두 표 가독성 개선 ======
st.markdown("""
<style>
@media (max-width: 640px) {
  .block-container { padding: 0.75rem 0.8rem !important; }
  div[data-testid="stMetricValue"] { font-size: 1.4rem !important; }
  .stButton button { width: 100% !important; padding: 0.8rem 1rem !important; }
  label, .stSelectbox label { font-size: 0.95rem !important; }
}

/* 프로모 박스 */
.promo-box { 
  padding: 12px 14px; 
  border-radius: 12px; 
  background: #fafafa; 
  border: 1px solid #eee; 
  margin: 12px 0 10px 0;
}
.promo-title { font-size: 1.25rem; font-weight: 800; margin-bottom: 6px; }
.promo-line  { font-size: 1.05rem; font-weight: 600; line-height: 1.5; }
.promo-small { font-size: 1.0rem; font-weight: 700; font-style: italic; margin-top: 6px; }

/* 데이터프레임 헤더/셀 모바일 크기 */
#sel-detail-table div[data-testid="stDataFrame"] th {
  font-size: .80rem !important;
  white-space: normal !important;
  line-height: 1.1 !important;
}
#sel-detail-table div[data-testid="stDataFrame"] td {
  font-size: .95rem !important;
}
@media (max-width:640px){
  #sel-detail-table div[data-testid="stDataFrame"] th { font-size: .72rem !important; }
  #sel-detail-table div[data-testid="stDataFrame"] td { font-size: .90rem !important; }
}
</style>
""", unsafe_allow_html=True)

# ====== 공용 도우미 함수 ======
def normalize_gsheet_url(url: str) -> str:
    """Google Sheets 'edit' URL -> 'export?format=xlsx...' URL 로 변환"""
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
    """문자 섞인 가격 문자열 -> 숫자(float)로 정리."""
    if series is None:
        return pd.Series(dtype=float)
    s = series.astype(str)
    s = (s.str.replace('\u00A0','', regex=False)  # NBSP
           .str.replace(',', '', regex=False)
           .str.replace('`', '', regex=False)
           .str.replace("'", '', regex=False)
           .str.replace('억', '', regex=False)
           .str.strip())
    s = s.str.replace(r'[^0-9.\-]', '', regex=True)  # 숫자/소수점/음수만
    return pd.to_numeric(s, errors='coerce')

# ====== 데이터 로딩 (버튼으로 갱신) ======
if "refresh_nonce" not in st.session_state:
    st.session_state["refresh_nonce"] = 0

def bump_refresh():
    st.session_state["refresh_nonce"] += 1

@st.cache_data(show_spinner=False)
def load_data(source, _nonce:int):
    """URL/로컬 엑셀 -> DataFrame 표준화 + 환산감정가 생성(공시가÷0.69, fallback: 감정가(억))."""
    is_url = isinstance(source, str) and (source.startswith("http://") or source.startswith("https://"))
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

    # 열 이름 표준화(존재하는 것만)
    rename_map = {
        "구역":"구역", "동":"동", "호":"호",
        "공시가(억)":"공시가(억)", "감정가(억)":"감정가(억)",
        "평형":"평형"
    }
    df = df.rename(columns=rename_map)

    # 문자열 정리
    for c in ["구역","동","호"]:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()

    # 25년 공시가 컬럼 만들기(있으면 숫자화)
    if "공시가(억)" in df.columns:
        df[PUBLIC_PRICE_LABEL] = pd.to_numeric(df["공시가(억)"], errors="coerce")
    elif PUBLIC_PRICE_LABEL in df.columns:
        df[PUBLIC_PRICE_LABEL] = pd.to_numeric(df[PUBLIC_PRICE_LABEL], errors="coerce")

    # 환산감정가 = 공시가 ÷ 0.69, 공시가 없으면 감정가(억) 클린 사용
    public = df.get(PUBLIC_PRICE_LABEL, pd.Series(dtype=float))
    public = pd.to_numeric(public, errors="coerce")
    derived = public / ADJUST_DIVISOR
    fallback = clean_price(df.get("감정가(억)", pd.Series(dtype=object)))
    df["감정가_클린"] = derived.where(~derived.isna(), fallback)

    # 평형 숫자화 시도
    if "평형" in df.columns:
        df["평형"] = pd.to_numeric(df["평형"], errors="coerce")

    return df

# ====== 헤더/설명/상단 컨트롤 ======
st.title("🏢 압구정 구역별 감정가 순위")
st.info(APP_DESCRIPTION)

left, right = st.columns([2,1])
with left:
    mobile_simple = st.toggle("📱 모바일 간단 보기", value=True, help="모바일에서 보기 편한 간단 레이아웃")
with right:
    if st.button("🔄 데이터 새로고침", use_container_width=True):
        bump_refresh()
        st.cache_data.clear()
        st.rerun()

# ====== 데이터 소스 선택 ======
with st.expander("① 데이터 파일/URL 선택 — 필요한 열: ['구역','동','호','공시가(억)'/ '25년 공시가(억)','감정가(억)','평형']", expanded=False):
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
    st.caption(f"현재 소스: {resolved_source if isinstance(resolved_source, str) else '업로드된 파일'}")

# ====== 데이터 읽기 ======
try:
    df = load_data(resolved_source, st.session_state["refresh_nonce"])
    st.success("데이터 로딩 완료")
except Exception as e:
    st.error(f"데이터를 불러오지 못했습니다: {e}")
    st.stop()

# ====== 선택 UI ======
zones = sorted(df["구역"].dropna().unique().tolist()) if "구역" in df.columns else []
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

# ====== 순위/유효세대 계산 ======
total_units_all = len(zone_df)

work = zone_df.dropna(subset=["감정가_클린"]).copy()
work = work[pd.to_numeric(work["감정가_클린"], errors="coerce").notna()].copy()
work["감정가_클린"] = work["감정가_클린"].astype(float)

bad_mask = pd.to_numeric(zone_df.get("감정가_클린", pd.Series(dtype=float)), errors="coerce").isna()
bad_rows = zone_df[bad_mask].copy()

# 동점 키
work["가격키"] = work["감정가_클린"].round(ROUND_DECIMALS) if ROUND_DECIMALS is not None else work["감정가_클린"]

# 경쟁 순위(내림차순 큰 값이 1위)
work["순위"] = work["가격키"].rank(method="min", ascending=False).astype(int)
work["공동세대수"] = work.groupby("가격키")["가격키"].transform("size")

# 정렬
work = work.sort_values(["가격키", "동", "호"], ascending=[False, True, True]).reset_index(drop=True)

# 선택 세대 값
sel_public = float(sel_df.iloc[0].get(PUBLIC_PRICE_LABEL, np.nan)) if not sel_df.empty else np.nan
sel_price  = float(sel_df.iloc[0].get("감정가_클린", np.nan))      if not sel_df.empty else np.nan
sel_key = round(sel_price, ROUND_DECIMALS) if (pd.notna(sel_price) and ROUND_DECIMALS is not None) else sel_price

if pd.notna(sel_key):
    subset = work[work["가격키"] == sel_key]
    sel_rank = int(subset["순위"].min()) if not subset.empty else None
    sel_tied = int(subset["공동세대수"].max()) if not subset.empty else 0
else:
    sel_rank, sel_tied = None, 0

total_units_valid = int(len(work))

# ====== 상단 지표(숫자 두 자리) ======
if mobile_simple:
    st.metric("선택 구역", zone)
    st.metric("구역 전체 세대수", f"{total_units_all:,} 세대")
    st.metric("선택 세대 25년 공시가(억)", f"{sel_public:,.2f}" if pd.notna(sel_public) else "-")
    st.metric(f"선택 세대 {DISPLAY_PRICE_LABEL}", f"{sel_price:,.2f}" if pd.notna(sel_price) else "-")
else:
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("선택 구역", zone)
    m2.metric("구역 전체 세대수", f"{total_units_all:,} 세대")
    m3.metric("유효 세대수(환산감정가 있음)", f"{total_units_valid:,} 세대")
    m4.metric("선택 세대 25년 공시가(억)", f"{sel_public:,.2f}" if pd.notna(sel_public) else "-")
    m5.metric(f"선택 세대 {DISPLAY_PRICE_LABEL}", f"{sel_price:,.2f}" if pd.notna(sel_price) else "-")

if pd.isna(sel_price):
    st.info("선택 세대의 환산감정가가 비어 있거나 숫자 형식이 아닙니다. 순위 계산에서 제외됩니다.")
elif sel_rank is not None:
    msg = f"구역 내 순위: 공동 {sel_rank}위 ({sel_tied}세대)" if sel_tied > 1 else f"구역 내 순위: {sel_rank}위"
    st.success(msg)
else:
    st.info("선택 세대는 유효 순위 계산 집합에 포함되지 않았습니다.")

st.divider()

# ====== ⑥ 선택 세대 상세 (라벨/폭/폰트 최적화 & 소수 2자리) ======
st.subheader("선택 세대 상세")

if not sel_df.empty:
    view = (
        sel_df[["구역","동","호","평형", PUBLIC_PRICE_LABEL, "감정가_클린"]]
        .rename(columns={"감정가_클린": DISPLAY_PRICE_LABEL})
        .reset_index(drop=True)
    )
    col_conf = {
        "구역": st.column_config.TextColumn("구역", width="small"),
        "동": st.column_config.TextColumn("동", width="small"),
        "호": st.column_config.TextColumn("호", width="small"),
        "평형": st.column_config.NumberColumn("평형", width="small"),
        PUBLIC_PRICE_LABEL: st.column_config.NumberColumn("공시가", format="%.2f", width="small"),
        DISPLAY_PRICE_LABEL: st.column_config.NumberColumn("환산가", format="%.2f", width="small"),
    }
    st.markdown('<div id="sel-detail-table">', unsafe_allow_html=True)
    st.dataframe(view, use_container_width=True, column_config=col_conf, hide_index=True)
    st.markdown('</div>', unsafe_allow_html=True)
else:
    st.info("선택 세대는 유효 순위 계산 집합에 없습니다.")

# === 프로모 텍스트(선택 세대 상세 아래, 모바일에서도 노출) ===
st.markdown(PROMO_HTML, unsafe_allow_html=True)

st.divider()

# ====== ⑦ 공동순위 요약 (선택 세대 금액 기준 · 동별 연속 층 범위) ======
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
    if "평형" in grp.columns and grp["평형"].notna().any():
        # 동·평형 별로 나눠 연속 층 범위
        for (dong_name, pyeong), g in grp.dropna(subset=["층"]).groupby(["동","평형"], dropna=True):
            floors = sorted(set(int(x) for x in g["층"].dropna().tolist()))
            if not floors:
                continue
            ranges = contiguous_ranges(floors)
            ranges_str = ", ".join(format_range(s, e) for s, e in ranges)
            rows.append({"동(평형)": f"{dong_name}동 ({int(pyeong)}평)", "층 범위": ranges_str, "세대수": len(g)})
    else:
        for dong_name, g in grp.dropna(subset=["층"]).groupby("동"):
            floors = sorted(set(int(x) for x in g["층"].dropna().tolist()))
            if not floors:
                continue
            ranges = contiguous_ranges(floors)
            ranges_str = ", ".join(format_range(s, e) for s, e in ranges)
            rows.append({"동": f"{dong_name}동", "층 범위": ranges_str, "세대수": len(g)})

    if rows:
        out = pd.DataFrame(rows)
        st.dataframe(out, use_container_width=True, hide_index=True)
        # CSV 다운로드
        csv_agg = out.to_csv(index=False).encode("utf-8-sig")
        st.download_button("현재 공동순위 요약 CSV 다운로드", csv_agg,
                           file_name=f"{zone}_공동{sel_rank}위_동별층요약.csv", mime="text/csv")
    else:
        st.info("해당 공동순위 그룹에서 요약할 층 정보가 없습니다.")

# ====== 비정상 값 안내 ======
if not bad_rows.empty:
    st.warning(f"환산감정가 비정상 값 {len(bad_rows)}건 발견 — 유효 세대수에서 제외됩니다.")
    with st.expander("비정상 환산감정가 행 보기 / 다운로드", expanded=False):
        cols_exist = [c for c in ["구역","동","호", PUBLIC_PRICE_LABEL,"감정가(억)"] if c in bad_rows.columns]
        bad_show = bad_rows[["구역","동","호"] + cols_exist].copy().drop_duplicates()
        st.dataframe(bad_show.reset_index(drop=True), use_container_width=True)
        bad_csv = bad_show.to_csv(index=False).encode("utf-8-sig")
        st.download_button("비정상 환산감정가 목록 CSV 다운로드", bad_csv,
                           file_name=f"{zone}_비정상_환산감정가_목록.csv", mime="text/csv")

st.divider()

# ====== ⑧ 압구정 내 유사금액 10 (구역·동(평형)별 연속 층 범위) - 최소차 제거 ======
st.subheader("압구정 내 금액이 유사한 차수 10 (구역·동(평형)별 연속 층 범위)")
st.caption("※ 공시가격에 기반한 것으로 실제 시장 상황과 다를 수 있습니다.")

if pd.isna(sel_price):
    st.info("선택 세대의 환산감정가가 유효하지 않아 유사 금액을 찾을 수 없습니다.")
else:
    pool = df.copy()
    pool = pool[pd.to_numeric(pool["감정가_클린"], errors="coerce").notna()].copy()
    pool["감정가_클린"] = pool["감정가_클린"].astype(float)
    pool["층"] = pool["호"].apply(extract_floor)

    pool = pool[~(
        (pool["구역"] == zone) &
        (pool["동"] == dong) &
        (pool["호"] == ho) &
        (np.isclose(pool["감정가_클린"], sel_price, rtol=0, atol=1e-6))
    )].copy()

    # 유사도(절대차) 계산 -> 후보 정렬
    pool["유사도"] = (pool["감정가_클린"] - sel_price).abs()
    cand = pool.sort_values(["유사도", "감정가_클린"], ascending=[True, False]).head(1000).copy()

    def _zone_num(z):
        m = re.search(r"\d+", str(z))
        return int(m.group()) if m else 10**9

    def _dong_num(d):
        m = re.search(r"\d+", str(d))
        return int(m.group()) if m else 10**9

    def _dong_label(d, p=None):
        s = f"{d}동"
        if p is not None and not pd.isna(p):
            try:
                ip = int(p)
            except Exception:
                ip = p
            s += f" ({ip}평)"
        return s

    # (구역, 동[, 평형]) 별 연속 층 범위 요약
    rows = []
    if "평형" in cand.columns and cand["평형"].notna().any():
        gb_keys = ["구역", "동", "평형"]
    else:
        gb_keys = ["구역", "동"]

    for keys, g in cand.dropna(subset=["층"]).groupby(gb_keys):
        k = list(keys)
        zone_name = k[0]
        dong_name = k[1]
        pyeong = k[2] if len(k) > 2 else None

        floors = sorted(set(int(x) for x in g["층"].dropna().tolist()))
        if not floors:
            continue
        ranges = contiguous_ranges(floors)
        ranges_str = ", ".join(format_range(s, e) for s, e in ranges)

        rows.append({
            "구역": zone_name,
            "동(평형)": _dong_label(dong_name, pyeong),
            "층 범위": ranges_str,
            "세대수": int(len(g)),
            "_z": _zone_num(zone_name),
            "_d": _dong_num(dong_name)
        })

    if not rows:
        st.info("유사 금액 결과가 없습니다.")
    else:
        out = pd.DataFrame(rows).sort_values(["_z","_d","세대수"], ascending=[True, True, False]).head(10)
        out = out.drop(columns=["_z","_d"])
        col_conf_sim = {
            "구역": st.column_config.TextColumn("구역", width="small"),
            "동(평형)": st.column_config.TextColumn("동(평형)", width="small"),
            "층 범위": st.column_config.TextColumn("층 범위", width="medium"),
            "세대수": st.column_config.NumberColumn("세대수", width="small")
        }
        st.dataframe(out, use_container_width=True, hide_index=True, column_config=col_conf_sim)

        csv_sim = out.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "유사금액 범위 TOP10 CSV 다운로드",
            csv_sim,
            file_name=f"압구정_유사금액_범위_TOP10_{zone}_{dong}_{ho}.csv",
            mime="text/csv"
        )
