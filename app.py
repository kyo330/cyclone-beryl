# app.py
import streamlit as st
from streamlit.components.v1 import html as st_html
import pandas as pd
import io, re

st.set_page_config(page_title="Lightning — Fixed Area", layout="wide")
st.markdown("#### HLMA Website")

# ───────────────────────── Uploaders ─────────────────────────
col1, col2 = st.columns(2)
with col1:
    upl_points_dat = st.file_uploader(
        "Upload Lightning Points .DAT (NMT LMA export)",
        type=["dat"],
        help="Plain-text .dat from LMA export. The app will auto-detect columns; if not, you can map them below."
    )
with col2:
    upl_winds = st.file_uploader(
        "Upload Storm/Wind Reports CSV (Lat, Lon[, Time, Comments])",
        type=["csv"],
        help="Columns (aliases OK): Lat/Latitude, Lon/Longitude. Optional Time/Date/Datetime and Comments/Remark/Description."
    )

# ─────────────────────── Helpers ───────────────────────
NUM_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")

def is_num(tok: str) -> bool:
    return bool(NUM_RE.fullmatch(tok))

def parse_lma_dat(file) -> pd.DataFrame | None:
    """Parse an LMA-like .dat text file into a numeric table (col1..colN)."""
    try:
        raw = file.read()
        # try ascii first; fall back to utf-8
        try:
            text = raw.decode("ascii", errors="ignore")
        except Exception:
            text = raw.decode("utf-8", errors="ignore")
        lines = text.splitlines()
        data_rows = []
        for ln in lines:
            s = ln.strip()
            if not s:
                continue
            toks = s.split()
            # Heuristic: first token numeric AND >=4 numeric tokens → likely data row
            if toks and is_num(toks[0]):
                n_numeric = sum(1 for t in toks if is_num(t))
                if n_numeric >= 4:
                    # keep only numeric tokens (drop headers that intermix text)
                    nums = [t for t in toks if is_num(t)]
                    data_rows.append(nums)
        if not data_rows:
            return None
        max_cols = max(len(r) for r in data_rows)
        rows = [r + [None]*(max_cols - len(r)) for r in data_rows]
        df = pd.DataFrame(rows, columns=[f"col{i+1}" for i in range(max_cols)])
        for c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df
    except Exception:
        return None

def in_range(series: pd.Series, lo: float, hi: float) -> bool:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return False
    frac = s.between(lo, hi).mean()
    return (frac > 0.95) and (s.std() > 1e-6)

def infer_lma_columns(df_num: pd.DataFrame):
    # candidates by range
    lat_candidates = [c for c in df_num.columns if in_range(df_num[c], -90, 90)]
    lon_candidates = [c for c in df_num.columns if in_range(df_num[c], -180, 180)]
    # altitude (meters): positive, plausible; median < 30,000 m
    alt_candidates = [c for c in df_num.columns
                      if (df_num[c].dropna().gt(0).mean() > 0.9) and (df_num[c].median(skipna=True) < 30000)]
    # "time-like": monotonic-ish increasing for many rows
    time_candidates = []
    for c in df_num.columns:
        s = df_num[c].dropna().values
        if len(s) > 10:
            diffs = pd.Series(s).diff().fillna(0).values
            # consider monotonic/non-decreasing majority
            if (diffs[: min(200, len(diffs))] >= 0).mean() > 0.7:
                time_candidates.append(c)

    chosen = {
        "lat": lat_candidates[0] if lat_candidates else None,
        "lon": lon_candidates[0] if lon_candidates else None,
        "alt": alt_candidates[0] if alt_candidates else None,
        "time": time_candidates[0] if time_candidates else None,
    }
    return chosen, lat_candidates, lon_candidates, alt_candidates, time_candidates

def finalize_points_from_dat(df_num: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    lat_c, lon_c, alt_c, time_c = mapping.get("lat"), mapping.get("lon"), mapping.get("alt"), mapping.get("time")
    if not (lat_c and lon_c and alt_c):
        return pd.DataFrame(columns=["lat","lon","alt","time"])
    out = pd.DataFrame({
        "lat": pd.to_numeric(df_num[lat_c], errors="coerce"),
        "lon": pd.to_numeric(df_num[lon_c], errors="coerce"),
        "alt": pd.to_numeric(df_num[alt_c], errors="coerce"),
        "time": df_num[time_c].astype(str) if time_c else ""
    }).dropna(subset=["lat","lon","alt"])
    return out

def _find_col(cols, aliases):
    lower = {c.lower(): c for c in cols}
    for a in aliases:
        if a.lower() in lower:
            return lower[a.lower()]
    return None

def load_wind_df(file):
    if not file:
        return None
    df = pd.read_csv(file)
    lat_c = _find_col(df.columns, ["Lat", "Latitude", "lat", "latitude", "y"])
    lon_c = _find_col(df.columns, ["Lon", "Longitude", "lon", "longitude", "x"])
    com_c = _find_col(df.columns, ["Comments", "Comment", "Remark", "Remarks", "Desc", "Description"])
    time_c = _find_col(df.columns, ["Time", "Valid", "IssueTime", "Date", "Datetime", "timestamp", "time"])
    if not (lat_c and lon_c):
        st.info("Storm report CSV has no Lat/Lon columns; wind layer will be empty.")
        return None
    out = pd.DataFrame({
        "Lat": pd.to_numeric(df[lat_c], errors="coerce"),
        "Lon": pd.to_numeric(df[lon_c], errors="coerce"),
        "Time": df[time_c].astype(str) if time_c else "",
        "Comments": df[com_c].astype(str) if com_c else ""
    }).dropna(subset=["Lat","Lon"])
    return out

def df_to_js_records(df):
    return df.to_json(orient="records") if (df is not None and not df.empty) else "[]"

# ───────────────────── Parse inputs ─────────────────────
points_df = None
if upl_points_dat is not None:
    df_num = parse_lma_dat(upl_points_dat)
    if df_num is None or df_num.empty:
        st.error("Could not parse the .dat file. Ensure it is a plain-text numeric export.")
    else:
        inferred, lat_opts, lon_opts, alt_opts, time_opts = infer_lma_columns(df_num)
        # If any of lat/lon/alt missing, show manual mapping selectors
        st.markdown("**Lightning .DAT column mapping**")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            lat_sel = st.selectbox("Latitude column", options=df_num.columns.tolist(),
                                   index=(df_num.columns.tolist().index(inferred["lat"]) if inferred["lat"] in df_num.columns else 0))
        with c2:
            lon_sel = st.selectbox("Longitude column", options=df_num.columns.tolist(),
                                   index=(df_num.columns.tolist().index(inferred["lon"]) if inferred["lon"] in df_num.columns else 0))
        with c3:
            alt_sel = st.selectbox("Altitude (m) column", options=df_num.columns.tolist(),
                                   index=(df_num.columns.tolist().index(inferred["alt"]) if inferred["alt"] in df_num.columns else 0))
        with c4:
            time_sel = st.selectbox("Time column (optional)", options=["<none>"] + df_num.columns.tolist(),
                                    index=( (df_num.columns.tolist().index(inferred["time"]) + 1) if inferred["time"] in df_num.columns else 0))
        mapping = {
            "lat": lat_sel,
            "lon": lon_sel,
            "alt": alt_sel,
            "time": (None if time_sel == "<none>" else time_sel)
        }
        points_df = finalize_points_from_dat(df_num, mapping)

winds_df  = load_wind_df(upl_winds) if upl_winds else None

points_js = df_to_js_records(points_df)
winds_js  = df_to_js_records(winds_df)

# ─────────────────────────── HTML Template ───────────────────────────
html_tpl = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Lightning Map</title>

  <!-- Leaflet + plugins -->
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

  <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css" />
  <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css" />
  <script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>

  <script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/PapaParse/5.3.0/papaparse.min.js"></script>

  <!-- Optional: arrows on the cyclone path -->
  <script src="https://unpkg.com/leaflet-polylinedecorator@1.7.0/dist/leaflet.polylineDecorator.min.js"></script>

  <style>
    :root { --sidebar-w: 340px; }
    html, body { height: 100%; }
    body { margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; }
    header { padding: 12px 16px; background:#0d47a1; color:white; }
    header h2 { margin: 0; font-size: 18px; }
    #app { display:flex; min-height: calc(100vh - 48px); }
    #sidebar {
      width: var(--sidebar-w);
      padding: 12px;
      box-shadow: 2px 0 6px rgba(0,0,0,0.08);
      z-index: 999;
      background: #fff;
    }
    #map { flex:1; width: 100%; height: 80vh; min-height: 600px; position: relative; }
    fieldset { border:1px solid #e0e0e0; border-radius:8px; margin-bottom:12px; }
    legend { padding:0 6px; font-weight:600; }
    label { display:block; margin:8px 0 4px; font-size: 13px; }
    select, input[type="number"] { width:100%; }
    .row { display:flex; gap:8px; }
    .row > div { flex:1; }
    .summary {
      position: absolute; top: 16px; left: 16px; background: rgba(255,255,255,0.95);
      padding:10px; border-radius:8px; box-shadow:0 2px 6px rgba(0,0,0,0.15); font-size:12px;
      min-width: 220px; z-index: 500;
    }
    .summary h4 { margin: 0 0 6px 0; font-size: 13px; }
    .stat { display:flex; justify-content: space-between; margin: 2px 0; }
    .legend {
      position: absolute; bottom: 16px; left: 16px; background: rgba(255,255,255,0.97);
      padding:12px; border-radius:8px; box-shadow:0 2px 6px rgba(0,0,0,0.15); font-size:12px;
      z-index: 500; min-width: 260px;
    }
    .legend h4 { margin: 0 0 8px 0; font-size: 13px; }
    .legend-item { display:flex; align-items:center; gap:10px; margin:6px 0; }
    .dot { width: 12px; height: 12px; border-radius: 50%; display:inline-block; border:1px solid rgba(0,0,0,0.25); }
    .dot.low { background:#ffe633; }
    .dot.med { background:#ffc300; }
    .dot.high { background:#ff5733; }
    .dot.extreme { background:#c70039; }
    .cluster-badge { display:inline-flex; align-items:center; justify-content:center; width: 22px; height: 22px; border-radius: 50%; background:#1976d2; color:#fff; font-weight:700; font-size:11px; border:1px solid rgba(0,0,0,0.2); }
    .wind-badge { display:inline-block; color:blue; font-weight:700; font-size:16px; line-height:1; }
    .path-swatch { width:16px; height:4px; background:#0057b7; border-radius:2px; display:inline-block; }
    .legend-note { color:#555; font-size:11px; margin-top:6px; }
    button { cursor:pointer; padding:8px 10px; border:1px solid #d0d0d0; background:#fafafa; border-radius:8px; }
    button:hover { background:#f0f0f0; }
    .footer-note { font-size: 11px; color:#666; margin-top:8px; }
    @media (max-width: 980px){
      #app { flex-direction: column; }
      #sidebar { width: auto; box-shadow: none; border-bottom:1px solid #eee; }
      #map { height: 70vh; min-height: 420px; }
      .summary { position: static; margin: 8px; }
      .legend { left: auto; right: 16px; }
    }
  </style>

  <!-- Inject uploaded data -->
  <script>
    window.INIT_DATA = {
      points: __POINTS__,
      winds:  __WINDS__
    };
    window.HAS_POINTS = Array.isArray(window.INIT_DATA.points) && window.INIT_DATA.points.length > 0;
    window.HAS_WINDS  = Array.isArray(window.INIT_DATA.winds)  && window.INIT_DATA.winds.length  > 0;
  </script>
</head>
<body>
  <header>
    <h2>Lightning — Fixed Area (Emergency Response)</h2>
  </header>

  <div id="app">
    <aside id="sidebar">
      <fieldset>
        <legend>Filters</legend>
        <label for="altitude-filter">Altitude range</label>
        <select id="altitude-filter">
          <option value="all">All</option>
          <option value="lt12">&lt; 12 km</option>
          <option value="12-14">12–14 km</option>
          <option value="14-16">14–16 km (Danger)</option>
          <option value="gt16">&gt; 16 km</option>
        </select>

        <div class="row">
          <div>
            <label for="cluster-toggle">Marker clustering</label>
            <select id="cluster-toggle">
              <option value="on">On</option>
              <option value="off">Off</option>
            </select>
          </div>
          <div>
            <label for="heat-toggle">Heatmap</label>
            <select id="heat-toggle">
              <option value="off">Off</option>
              <option value="on">On</option>
            </select>
          </div>
        </div>

        <label for="recent-mins">Show strikes from last (minutes)</label>
        <input type="number" id="recent-mins" min="0" step="5" value="0" />

        <fieldset>
          <legend>Auto-refresh</legend>
          <div class="row">
            <div>
              <label for="auto-refresh">Enable</label>
              <select id="auto-refresh">
                <option value="off">Off</option>
                <option value="on">On</option>
              </select>
            </div>
            <div>
              <label for="refresh-sec">Every (seconds)</label>
              <input type="number" id="refresh-sec" min="30" step="30" value="120" />
            </div>
