# apgujeong_rank_app.py
# 실행: streamlit run apgujeong_rank_app.py

import streamlit as st
import pandas as pd
import numpy as np
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs

st.set_page_config(page_title="압구정 구역별 감정가 순위", page_icon="🏢", layout="wide")

# ====== 사용자 문구 / 라벨 ======
APP_DESCRIPTION = (
    "⚠️ 데이터는 **2025년 공동주택 공시가격(공주가)** 을 바탕으로 계산한 것으로, "
    "재건축 시 **실행될 감정평가액과 차이**가 있을 수 있습니다.\n\n"
    "이 앱은 **구역 → 동 → 호**를 선택하면 같은 구역 내 **환산감정가(억)** 기준으로 "
    "**경쟁 순위**(공동이면 같은 순위, 다음 순위는 건너뜀)를 계산해 보여줍니다. "
    "하단 요약은 **현재 선택 세대가 속한 공동순위(같은 금액) 그룹**을 "
    "**동별 연속 층 범위**로 간소화하여 표시합니다."
)
DISPLAY_PRICE_LABEL = "환산감정가(억)"
PUBLIC_PRICE_LABEL  = "25년 공시가(억)"

# 기본 구글 시트
DEFAULT_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/1E_GAGLS7PgXFUvPiz2qsZYizKfi1mCrwez2u30OBCvI/"
    "export?format=xlsx&gid=1484463303"
)

# 동점 판단 정밀도
ROUND_DECIMALS = 6

# ====== 스타일 (모바일 + 홍보박스 강제표시) ======
st.markdown("""
<style>
@media (max-width: 640px) {
  .block-container { padding: 0.75rem 0.8rem !important; }
  div[data-testid="stMetricValue"] { font-size: 1.5rem !important; }
  .stButton button { width: 100% !important; padding: 0.8rem 1rem !important; }
  label, .stSelectbox label { font-size: 0.95rem !important; }
}

/* 홍보 박스: 어떤 레이아웃/테마에서도 반드시 보이도록 */
.promo-wrapper { display:block !important; visibility:visible !important; width:100% !important; }
.promo-box { 
  padding: 12px 14px; border-radius: 12px; background:#ffffff; 
  border:1px solid #eee; margin: 12px 0; width:100%;
  display:block !important; visibility:visible !important; 
  position:relative; z-index: 5;

  /* 💡핵심: 글자색을 다크/라이트 상관없이 검정으로 고정 */
  color:#111 !important;
}
.promo-box * { color:#111 !important; }   /* 내부 모든 텍스트도 검정 고정 */

.promo-title { font-size: 1.25rem; font-weight:800; margin-bottom: 6px; }
.promo-line  { font-size: 1.1rem;  font-weight:600; line-height:1.5; }
.promo-small { font-size: 1.0rem;  font-weight:700; font-style: italic; margin-top:6px; }

@media (max-width: 640px) {
  .promo-title { font-size: 1.15rem; }
  .promo-line  { font-size: 1.05rem; }
  .promo-small { font-size: 0.95rem; }
}
</style>
""", unsafe_allow_html=True)


# ====== 도우미 ======
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

def show_df(df: pd.DataFrame, mobile: bool, colcfg: dict | None = None):
    if mobile:
        st.table(df)
    else:
        try:
            st.dataframe(df, hide_index=True, use_container_width=True, column_config=colcfg or {})
        except Exception:
            st.dataframe(df, hide_index=True, use_container_width=True)

def render_promo(key_suffix: str = ""):
    """홍보 박스를 반드시 보이도록 강제 렌더링"""
    with st.container():
        st.markdown(
            f"""
            <div class="promo-wrapper" id="promo-{key_suffix}">
              <div class="promo-box">
                <div class="promo-title">📞 <b>압구정 원 부동산</b></div>
                <div class="promo-line">압구정 재건축 전문 컨설팅 · 순위를 알고 사야하는 압구정</div>
                <div class="promo-line"><b>문의</b></div>
                <div class="promo-line">02-540-3334 / 최이사 Mobile 010-3065-1780</div>
                <div class="promo-small">압구정 미래가치 예측.</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True
        )

# ====== 타이틀/설명 ======
st.title("🏢 압구정 구역별 감정가 순위")
st.info(APP_DESCRIPTION)

# 상단 토글 / 새로고침
top_left, top_right = st.columns([2,1], gap="small")
with top_left:
    mobile_simple = st.toggle("📱 모바일 간단 보기", value=True, help="모바일에서 보기 편한 간단 레이아웃")
with top_right:
    if st.button("🔄 데이터 새로고침"):
        st.rerun()

# ====== 데이터 소스 ======
with st.expander("① 데이터 파일/URL 선택 — 필요한 열: ['구역','동','호','평형','공시가(억)','감정가(억)']", expanded=False):
    uploaded = st.file_uploader("엑셀 파일 업로드 (.xlsx)", type=["xlsx"])
    manual_source = st.text_input("로컬 파일 경로 또는 Google Sheets/CSV URL (선택)", value="")
    if uploaded is not None:
        resolved_source = uploaded
        source_desc = "업로드된 파일 사용"
    elif manual_source.strip():
        resolved_source = normalize_gsheet_url(manual_source.strip())
        source_desc = "직접 입력 소스 사용"
    else:
        resolved_source = DEFAULT_SHEET_URL
        source_desc = "기본 Google Sheets 사용"
    st.success(f"데이터 소스: {source_desc}")
    st.caption(f"현재 소스: {resolved_source if isinstance(resolved_source, str) else '업로드된 파일 객체'}")

# ====== 데이터 로딩 ======
def load_data(source):
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

    df = df.rename(columns={
        "구역":"구역","동":"동","호":"호","평형":"평형",
        "공시가(억)":"공시가(억)","감정가(억)":"감정가(억)"
    })
    for c in ["구역","동","호","평형"]:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()

    # 환산감정가 = 공시가(억)/0.69 (fallback: 감정가(억) 클린)
    public = pd.to_numeric(df.get("공시가(억)"), errors="coerce")
    derived = public / 0.69
    fallback = clean_price(df.get("감정가(억)", pd.Series(dtype=object)))
    df["감정가_클린"] = derived.where(~derived.isna(), fallback)
    return df

try:
    if isinstance(resolved_source, str):
        df = load_data(resolved_source)
    else:
        df = pd.read_excel(resolved_source, sheet_name=0)
        df = df.rename(columns={
            "구역":"구역","동":"동","호":"호","평형":"평형",
            "공시가(억)":"공시가(억)","감정가(억)":"감정가(억)"
        })
        for c in ["구역","동","호","평형"]:
            if c in df.columns:
                df[c] = df[c].astype(str).str.strip()
        public = pd.to_numeric(df.get("공시가(억)"), errors="coerce")
        derived = public / 0.69
        fallback = clean_price(df.get("감정가(억)", pd.Series(dtype=object)))
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

# ====== 순위 계산 ======
total_units_all = int(len(zone_df))

work = zone_df.dropna(subset=["감정가_클린"]).copy()
work = work[pd.to_numeric(work["감정가_클린"], errors="coerce").notna()].copy()
work["감정가_클린"] = work["감정가_클린"].astype(float)

bad_mask = pd.to_numeric(zone_df["감정가_클린"], errors="coerce").isna()
bad_rows = zone_df[bad_mask].copy()

work["가격키"] = work["감정가_클린"].round(ROUND_DECIMALS) if ROUND_DECIMALS is not None else work["감정가_클린"]
work["순위"] = work["가격키"].rank(method="min", ascending=False).astype(int)
work["공동세대수"] = work.groupby("가격키")["가격키"].transform("size")
work = work.sort_values(["가격키", "동", "호"], ascending=[False, True, True]).reset_index(drop=True)

sel_row = sel_df.iloc[0]
sel_public_price = pd.to_numeric(sel_row.get("공시가(억)"), errors="coerce")
sel_price = float(sel_row.get("감정가_클린")) if pd.notna(sel_row.get("감정가_클린")) else np.nan
sel_key = round(sel_price, ROUND_DECIMALS) if (pd.notna(sel_price) and ROUND_DECIMALS is not None) else sel_price

if pd.notna(sel_key):
    subset = work[work["가격키"] == sel_key]
    sel_rank = int(subset["순위"].min()) if not subset.empty else None
    sel_tied = int(subset["공동세대수"].max()) if not subset.empty else 0
else:
    sel_rank, sel_tied = None, 0

total_units_valid = int(len(work))

# ====== 상단 지표 ======
if mobile_simple:
    st.metric("선택 구역", zone)
    st.metric("구역 전체 세대수", f"{total_units_all:,} 세대")
    st.metric("유효 세대수(환산감정가 있음)", f"{total_units_valid:,} 세대")
    st.metric(f"선택 세대 {DISPLAY_PRICE_LABEL}", f"{sel_price:,.2f}" if pd.notna(sel_price) else "-")
    st.metric(f"선택 세대 {PUBLIC_PRICE_LABEL}", f"{sel_public_price:,.2f}" if pd.notna(sel_public_price) else "-")
else:
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("선택 구역", zone)
    m2.metric("구역 전체 세대수", f"{total_units_all:,} 세대")
    m3.metric("유효 세대수(환산감정가 있음)", f"{total_units_valid:,} 세대")
    m4.metric(f"선택 세대 {DISPLAY_PRICE_LABEL}", f"{sel_price:,.2f}" if pd.notna(sel_price) else "-")
    m5.metric(f"선택 세대 {PUBLIC_PRICE_LABEL}", f"{sel_public_price:,.2f}" if pd.notna(sel_public_price) else "-")

st.divider()

# ====== 선택 세대 상세 ======
st.subheader("선택 세대 상세")
sel_detail = work[(work["동"] == dong) & (work["호"] == ho)].copy()
if "평형" in df.columns and "평형" not in sel_detail.columns:
    sel_detail = sel_detail.merge(sel_df[["동","호","평형"]], on=["동","호"], how="left")

basic_cols = ["구역","동","호","평형","공시가(억)","감정가_클린","순위","공동세대수"]
sel_view = sel_detail.reindex(columns=[c for c in basic_cols if c in sel_detail.columns]).copy()
sel_view = sel_view.rename(columns={"감정가_클린": DISPLAY_PRICE_LABEL, "공시가(억)": PUBLIC_PRICE_LABEL})

if mobile_simple:
    st.table(sel_view)
else:
    colcfg_sel = {
        "구역": st.column_config.Column(width="small"),
        "동": st.column_config.Column(width="small"),
        "호": st.column_config.Column(width="small"),
        "평형": st.column_config.Column(width="small"),
        PUBLIC_PRICE_LABEL: st.column_config.NumberColumn(format="%.2f", width="small"),
        DISPLAY_PRICE_LABEL: st.column_config.NumberColumn(format="%.2f", width="medium"),
        "순위": st.column_config.NumberColumn(width="small"),
        "공동세대수": st.column_config.NumberColumn(width="small"),
    }
    show_df(sel_view.reset_index(drop=True), mobile_simple, colcfg_sel)

# ★ 홍보박스: 이 위치(선택 세대 상세 바로 아래)에 강제 표시
render_promo(key_suffix="below-detail")
# 모바일은 특정 레이아웃에서 가려질 수 있어 한 번 더 시도 (중복 방지를 위해 key_suffix 다르게)
if mobile_simple:
    render_promo(key_suffix="below-detail-mobile")

st.divider()

# ====== 공동순위 요약 ======
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
    for dong_name, g in grp.dropna(subset=["층"]).groupby("동"):
        floors = sorted(set(int(x) for x in g["층"].dropna().tolist()))
        if not floors:
            continue
        ranges = contiguous_ranges(floors)
        ranges_str = ", ".join(format_range(s, e) for s, e in ranges)
        rows.append({"동": f"{dong_name}동" if "동" not in str(dong_name) else str(dong_name),
                     "층 범위": ranges_str, "세대수": int(len(g))})

    def _dong_num(d):
        m = re.search(r"\d+", str(d))
        return int(m.group()) if m else 10**9
    rows = sorted(rows, key=lambda r: _dong_num(r["동"]))

    if rows:
        out = pd.DataFrame(rows)
        if mobile_simple:
            st.table(out)
        else:
            colcfg_grp = {
                "동": st.column_config.Column(width="small"),
                "층 범위": st.column_config.Column(width="medium"),
                "세대수": st.column_config.NumberColumn(format="%d", width="small"),
            }
            show_df(out, mobile_simple, colcfg_grp)
        csv_agg = out.to_csv(index=False).encode("utf-8-sig")
        st.download_button("현재 공동순위 요약 CSV 다운로드", csv_agg,
                           file_name=f"{zone}_공동{sel_rank}위_동별층요약.csv", mime="text/csv")
    else:
        st.info("해당 공동순위 그룹에서 요약할 층 정보가 없습니다.")

# ====== 유사금액 TOP10 (❌ 최소차 표시는 제거) ======
st.divider()
st.subheader("압구정 내 금액이 유사한 차수 10 (구역·동(평형)·연속 층 범위)")
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

    pool["__diff"] = (pool["감정가_클린"] - sel_price).abs()
    cand = pool.sort_values(["__diff", "감정가_클린"], ascending=[True, False]).head(1000).copy()

    def _zone_num(z):
        m = re.search(r"\d+", str(z))
        return int(m.group()) if m else 10**9
    def _dong_num(d):
        m = re.search(r"\d+", str(d))
        return int(m.group()) if m else 10**9
    def _dong_label(d):
        s = str(d)
        return s if "동" in s else f"{s}동"

    rows = []
    group_cols = ["구역", "동"]
    if "평형" in cand.columns:
        group_cols.append("평형")

    for keys, g in cand.dropna(subset=["층"]).groupby(group_cols):
        if len(group_cols) == 3:
            zone_name, dong_name, py = keys
        else:
            zone_name, dong_name = keys
            py = None

        floors = sorted(set(int(x) for x in g["층"].dropna().tolist()))
        if not floors:
            continue
        ranges = contiguous_ranges(floors)
        ranges_str = ", ".join(format_range(s, e) for s, e in ranges)

        best_diff = float(g["__diff"].min())   # 정렬용(표시 X)
        median_price = float(g["감정가_클린"].median())

        if py:
            dong_disp = f"{_dong_label(dong_name)} ({py})"
        else:
            dong_disp = _dong_label(dong_name)

        rows.append({
            "구역": zone_name,
            "동(평형)": dong_disp,
            "층 범위": ranges_str,
            "중앙값(억)": round(median_price, 2),
            "해당 세대수": int(len(g)),
            "_sort_diff": best_diff,
            "_sort_zone": _zone_num(zone_name),
            "_sort_dong": _dong_num(dong_name),
        })

    if not rows:
        st.info("유사 금액 결과가 없습니다.")
    else:
        out = pd.DataFrame(rows)
        out = out.sort_values(
            ["_sort_diff", "해당 세대수", "_sort_zone", "_sort_dong"],
            ascending=[True, False, True, True]
        ).head(10).drop(columns=["_sort_diff", "_sort_zone", "_sort_dong"])

        display_cols = ["구역","동(평형)","층 범위","중앙값(억)","해당 세대수"]  # ← 최소차(억) 완전 제거
        if mobile_simple:
            st.table(out[display_cols])
        else:
            colcfg_sim = {
                "구역": st.column_config.Column(width="small"),
                "동(평형)": st.column_config.Column(width="medium"),
                "층 범위": st.column_config.Column(width="medium"),
                "중앙값(억)": st.column_config.NumberColumn(format="%.2f", width="small"),
                "해당 세대수": st.column_config.NumberColumn(format="%d", width="small"),
            }
            show_df(out[display_cols].reset_index(drop=True), mobile_simple, colcfg_sim)

        csv_sim = out[display_cols].to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "유사금액 범위 TOP10 CSV 다운로드",
            csv_sim,
            file_name=f"압구정_유사금액_범위_TOP10_{zone}_{dong}_{ho}.csv",
            mime="text/csv"
        )

