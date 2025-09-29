import streamlit as st
from streamlit.components.v1 import html as st_html
import pandas as pd
import re

st.set_page_config(page_title="Lightning — Fixed Area", layout="wide")
st.markdown("#### HLMA Website")

# ───────────────────────── Uploaders ─────────────────────────
col1, col2 = st.columns(2)
with col1:
    upl_points = st.file_uploader(
        "Upload Lightning Strikes (.dat from HLMA/LMA export)",
        type=["dat"],
        help="We parse numeric rows and infer lat/lon/alt (+ optional time)."
    )
with col2:
    upl_winds = st.file_uploader(
        "Upload Storm/Wind Reports CSV (Lat, Lon[, Time, Comments])",
        type=["csv"],
        help="Columns (aliases OK): Lat/Latitude, Lon/Longitude; optional Time/Date/Datetime and Comments/Remark."
    )

# ─────────────────────── Helpers & Normalizers ───────────────────────
def _find_col(cols, aliases):
    lower = {c.lower(): c for c in cols}
    for a in aliases:
        if a.lower() in lower:
            return lower[a.lower()]
    return None

def _is_float_token(t: str) -> bool:
    return re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", (t or "").strip()) is not None

def parse_lma_dat(file) -> pd.DataFrame | None:
    """
    Heuristic parser for HLMA/LMA .dat exports.
    - Reads bytes once (file.getvalue()), decodes (ascii→utf-8 fallback)
    - Keeps lines that begin with a number and contain >=4 numeric tokens
    - Infers lat, lon, alt (m), and optional time column
    """
    if not file:
        return None

    raw = file.getvalue()  # <- robust for Streamlit reruns
    try:
        text = raw.decode("ascii", errors="replace")
    except Exception:
        text = raw.decode("utf-8", errors="replace")

    rows = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        toks = s.split()
        if len(toks) < 4:
            continue
        if not _is_float_token(toks[0]):
            continue  # header/comment lines
        num_toks = [t for t in toks if _is_float_token(t)]
        if len(num_toks) < 4:
            continue
        # keep as floats where possible
        row = []
        for t in toks:
            if _is_float_token(t):
                try:
                    row.append(float(t))
                except Exception:
                    row.append(None)
            else:
                row.append(None)
        rows.append(row)

    if not rows:
        st.error("No numeric data rows detected in the .dat file. If your .dat is fixed-width or different schema, I can add a manual column mapper.")
        return None

    max_cols = max(len(r) for r in rows)
    rows = [r + [None]*(max_cols - len(r)) for r in rows]
    df = pd.DataFrame(rows, columns=[f"c{i+1}" for i in range(max_cols)])

    # inference helpers
    def in_range(series, lo, hi, min_ratio=0.9):
        s = pd.to_numeric(series, errors="coerce").dropna()
        if s.empty: return False
        return (s.between(lo, hi).mean() >= min_ratio) and (s.std() > 1e-6)

    lat_cands = [c for c in df.columns if in_range(df[c], -90, 90)]
    lon_cands = [c for c in df.columns if in_range(df[c], -180, 180)]

    lat_col = lat_cands[0] if lat_cands else None
    lon_col = lon_cands[0] if lon_cands else None

    # altitude candidates: mostly positive
    alt_cols = []
    for c in df.columns:
        s = pd.to_numeric(df[c], errors="coerce").dropna()
        if not s.empty and (s > 0).mean() >= 0.95:
            alt_cols.append(c)

    def alt_score(series):
        s = pd.to_numeric(series, errors="coerce").dropna()
        if s.empty: return 0
        med = s.median()
        if 50 <= med <= 30000: return 2  # meters
        if 0.05 <= med <= 50:  return 1  # likely km
        return 0

    best = (-1, None)
    for c in alt_cols:
        sc = alt_score(df[c])
        if sc > best[0]:
            best = (sc, c)
    alt_col = best[1]

    # time: roughly non-decreasing sequence
    time_col, best_time_score = None, -1
    for c in df.columns:
        s = pd.to_numeric(df[c], errors="coerce").dropna()
        if len(s) < 10: continue
        diffs = s.diff().dropna()
        if len(diffs) == 0: continue
        inc_ratio = (diffs >= 0).mean()
        if inc_ratio > best_time_score and inc_ratio >= 0.6:
            best_time_score, time_col = inc_ratio, c

    if not (lat_col and lon_col and alt_col):
        st.error(f"Failed to infer columns.\nLat candidates: {lat_cands}\nLon candidates: {lon_cands}\nAlt candidates: {alt_cols}")
        return None

    out = pd.DataFrame({
        "lat": pd.to_numeric(df[lat_col], errors="coerce"),
        "lon": pd.to_numeric(df[lon_col], errors="coerce"),
        "alt": pd.to_numeric(df[alt_col], errors="coerce"),
    }).dropna(subset=["lat", "lon", "alt"])

    # convert km→m if most altitudes < 50
    s_alt = out["alt"].dropna()
    if not s_alt.empty and (s_alt < 50).mean() >= 0.8:
        out["alt"] = out["alt"] * 1000.0

    out["time"] = df[time_col].astype(str) if time_col else ""
    return out

def parse_wind_csv(file):
    if not file:
        return None
    df = pd.read_csv(file)
    lat_c = _find_col(df.columns, ["Lat", "Latitude", "lat", "latitude", "y"])
    lon_c = _find_col(df.columns, ["Lon", "Longitude", "lon", "longitude", "x"])
    com_c = _find_col(df.columns, ["Comments", "Comment", "Remark", "Remarks", "Desc", "Description"])
    time_c = _find_col(df.columns, ["Time", "Valid", "IssueTime", "Date", "Datetime", "timestamp", "time"])
    if not (lat_c and lon_c):
        st.error("Storm report CSV is missing Lat/Lon; please include those.")
        return None
    out = pd.DataFrame({
        "Lat": pd.to_numeric(df[lat_c], errors="coerce"),
        "Lon": pd.to_numeric(df[lon_c], errors="coerce"),
        "Time": df[time_c].astype(str) if time_c else "",
        "Comments": df[com_c].astype(str) if com_c else ""
    }).dropna(subset=["Lat", "Lon"])
    return out

# ───────────── Parse uploads + SHOW PREVIEWS (this is what you asked to see) ─────────────
points_df = parse_lma_dat(upl_points) if upl_points else None
winds_df  = parse_wind_csv(upl_winds) if upl_winds else None

with st.expander("Parsed lightning strikes (.dat) — preview", expanded=True):
    if points_df is not None and not points_df.empty:
        st.write(f"Rows: {len(points_df):,}  |  Columns: {list(points_df.columns)}")
        st.dataframe(points_df.head(50), use_container_width=True)
    else:
        st.info("No parsed lightning rows yet. Upload a .dat file.")

with st.expander("Parsed storm/wind reports (CSV) — preview", expanded=True):
    if winds_df is not None and not winds_df.empty:
        st.write(f"Rows: {len(winds_df):,}  |  Columns: {list(winds_df.columns)}")
        st.dataframe(winds_df.head(50), use_container_width=True)
    else:
        st.info("No parsed storm/wind rows yet. Upload a CSV.")

# Convert to JSON strings for injection
def df_to_js_records(df):
    return df.to_json(orient="records") if (df is not None and not df.empty) else "[]"

points_js = df_to_js_records(points_df)
winds_js  = df_to_js_records(winds_df)

# ─────────────────────────── HTML (no external data links) ───────────────────────────
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
  <script src="https://unpkg.com/leaflet-polylinedecorator@1.7.0/dist/leaflet.polylineDecorator.min.js"></script>

  <style>
    :root { --sidebar-w: 340px; }
    html, body { height: 100%; }
    body { margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; }
    header { padding: 12px 16px; background:#0d47a1; color:white; }
    header h2 { margin: 0; font-size: 18px; }
    #app { display:flex; min-height: calc(100vh - 48px); }
    #sidebar { width: var(--sidebar-w); padding: 12px; box-shadow: 2px 0 6px rgba(0,0,0,0.08); z-index: 999; background: #fff; }
    #map { flex:1; width: 100%; height: 80vh; min-height: 600px; position: relative; }
    fieldset { border:1px solid #e0e0e0; border-radius:8px; margin-bottom:12px; }
    legend { padding:0 6px; font-weight:600; }
    label { display:block; margin:8px 0 4px; font-size: 13px; }
    select, input[type="number"] { width:100%; }
    .row { display:flex; gap:8px; }
    .row > div { flex:1; }
    .summary { position: absolute; top: 16px; left: 16px; background: rgba(255,255,255,0.95); padding:10px; border-radius:8px; box-shadow:0 2px 6px rgba(0,0,0,0.15); font-size:12px; min-width: 220px; z-index: 500; }
    .summary h4 { margin: 0 0 6px 0; font-size: 13px; }
    .stat { display:flex; justify-content: space-between; margin: 2px 0; }
    .legend { position: absolute; bottom: 16px; left: 16px; background: rgba(255,255,255,0.97); padding:12px; border-radius:8px; box-shadow:0 2px 6px rgba(0,0,0,0.15); font-size:12px; z-index: 500; min-width: 260px; }
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
  </style>

  <script>
    // Injected from Streamlit
    window.INIT_DATA = {
      points: __POINTS__,
      winds:  __WINDS__
    };
  </script>
</head>
<body>
  <header><h2>Lightning — Fixed Area (Emergency Response)</h2></header>
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
          <legend>Cyclone path</legend>
          <div class="row">
            <div>
              <label for="path-toggle">Show path</label>
              <select id="path-toggle">
                <option value="on">On</option>
                <option value="off">Off</option>
              </select>
            </div>
            <div>
              <label for="arrows-toggle">Arrows</label>
              <select id="arrows-toggle">
                <option value="on">On</option>
                <option value="off">Off</option>
              </select>
            </div>
          </div>
        </fieldset>

        <fieldset>
          <legend>Downloads</legend>
          <button id="download-points">Download filtered points (CSV)</button>
        </fieldset>
      </fieldset>
    </aside>

    <div id="map">
      <div class="summary" id="summary">
        <h4>Strike Summary</h4>
        <div class="stat"><span>Total (all data):</span><strong id="sum-total">0</strong></div>
        <div class="stat"><span>Visible (filters):</span><strong id="sum-visible">0</strong></div>
        <hr>
        <div class="stat"><span>&lt; 12 km:</span><strong id="sum-low">0</strong></div>
        <div class="stat"><span>12–14 km:</span><strong id="sum-med">0</strong></div>
        <div class="stat"><span>14–16 km:</span><strong id="sum-high">0</strong></div>
        <div class="stat"><span>&gt; 16 km:</span><strong id="sum-extreme">0</strong></div>
      </div>

      <div class="legend" id="legend">
        <h4>Legend</h4>
        <div class="legend-item"><span class="dot low"></span> <span>Low altitude (&lt; 12 km)</span></div>
        <div class="legend-item"><span class="dot med"></span> <span>Medium (12–14 km)</span></div>
        <div class="legend-item"><span class="dot high"></span> <span>High / Danger (14–16 km)</span></div>
        <div class="legend-item"><span class="dot extreme"></span> <span>Extreme (&gt; 16 km)</span></div>
        <div class="legend-item"><span class="cluster-badge">12</span> <span>Cluster: number = strike count</span></div>
        <div class="legend-item"><span class="wind-badge">W</span> <span>Wind report point</span></div>
        <div class="legend-item"><span class="path-swatch"></span> <span>Cyclone path (start ➜ end)</span></div>
      </div>
    </div>
  </div>

  <script>
    let map, clusterGroup, plainGroup, heatLayer, windLayer;
    let cyclonePathLayer = null, cycloneArrowLayer = null;
    let allMarkers = [];

    function riskColor(alt){ if (alt < 12000) return '#ffe633'; if (alt < 14000) return '#ffc300'; if (alt < 16000) return '#ff5733'; return '#c70039'; }
    function riskTier(alt){ if (alt < 12000) return 'low'; if (alt < 14000) return 'med'; if (alt < 16000) return 'high'; return 'extreme'; }
    function debounce(fn, ms){ let t; return function(){ clearTimeout(t); t = setTimeout(()=>fn.apply(this, arguments), ms); }; }
    function parseTime(val){
      if (!val) return null;
      if (!isNaN(val)) { const n=Number(val); if (n>1e10) return new Date(n); }
      const d=new Date(val); return isNaN(d.getTime())?null:d;
    }

    function initMap(){
      map = L.map('map').setView([30.7, -95.2], 8);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap contributors' }).addTo(map);
      clusterGroup = L.markerClusterGroup({ disableClusteringAtZoom: 12 });
      plainGroup = L.layerGroup();
      windLayer = L.layerGroup().addTo(map);
      setTimeout(()=>{ map.invalidateSize(); }, 300);
    }

    function buildPoints(rows){
      allMarkers = []; clusterGroup.clearLayers(); plainGroup.clearLayers();
      rows.forEach(r=>{
        const lat = parseFloat(r.lat ?? r.Lat ?? r.latitude ?? r.Latitude);
        const lon = parseFloat(r.lon ?? r.Lon ?? r.longitude ?? r.Longitude);
        const alt = parseFloat(r.alt ?? r.altitude ?? r.altitude_m ?? r.Alt ?? r.Altitude);
        const t = parseTime(r.time ?? r.Time ?? r.timestamp ?? r.Timestamp ?? r.datetime ?? r.Date);
        if (isNaN(lat) || isNaN(lon) || isNaN(alt)) return;
        const color = riskColor(alt), tier = riskTier(alt);
        const m = L.circleMarker([lat, lon], {
          radius: tier==='low'?3:tier==='med'?5:tier==='high'?7:9,
          color, fillColor: color, fillOpacity: 0.85, opacity: 1, weight: 1
        }).bindPopup(
          `<b>Altitude:</b> ${alt} m<br><b>Tier:</b> ${tier.toUpperCase()}<br>` +
          (t? `<b>Time:</b> ${t.toISOString()}<br>`:'' ) + `(${lat.toFixed(3)}, ${lon.toFixed(3)})`
        );
        allMarkers.push({marker: m, alt, time: t, lat, lon, tier});
      });
      allMarkers.forEach(o=>clusterGroup.addLayer(o.marker));
      if (!map.hasLayer(clusterGroup)) clusterGroup.addTo(map);
      document.getElementById('sum-total').textContent = allMarkers.length;
    }

    function buildWindMarkers(rows){
      windLayer.clearLayers();
      const seq = [];
      rows.forEach((r, idx)=>{
        const lat = parseFloat(r.Lat ?? r.lat ?? r.Latitude ?? r.latitude);
        const lon = parseFloat(r.Lon ?? r.lon ?? r.Longitude ?? r.longitude);
        const comments = r.Comments ?? r.Comment ?? r.Remark ?? r.Description ?? '';
        const t = parseTime(r.Time ?? r.time ?? r.Date ?? r.datetime ?? r.Timestamp);
        if (isNaN(lat) || isNaN(lon)) return;
        L.marker([lat, lon], {
          icon: L.divIcon({ className:'wind-icon', html:'<span style="color:blue; font-weight:700; font-size:20px;">W</span>', iconSize:[24,24], iconAnchor:[12,12] })
        }).bindPopup(
          (t? `<b>Time:</b> ${t.toISOString()}<br>`:'') +
          (comments ? `<b>Report:</b> ${comments}<br>`: '') +
          `(${lat.toFixed(3)}, ${lon.toFixed(3)})`
        ).addTo(windLayer);
        seq.push({lat, lon, t, idx});
      });

      const withTime = seq.filter(s => s.t instanceof Date && !isNaN(s.t));
      const noTime   = seq.filter(s => !(s.t instanceof Date) || isNaN(s.t));
      withTime.sort((a,b)=> a.t - b.t);
      const ordered = withTime.concat(noTime);
      if (ordered.length < 2) return;

      const latlngs = ordered.map(o => [o.lat, o.lon]);
      if (cyclonePathLayer){ map.removeLayer(cyclonePathLayer); cyclonePathLayer = null; }
      if (cycloneArrowLayer){ map.removeLayer(cycloneArrowLayer); cycloneArrowLayer = null; }

      cyclonePathLayer = L.polyline(latlngs, { color: '#0057b7', weight: 3, opacity: 0.95 }).addTo(map);
      const start = latlngs[0], end = latlngs[latlngs.length - 1];
      L.circleMarker(start, {radius:6, color:'#0057b7', fillColor:'#0057b7', fillOpacity:1}).bindPopup('Cyclone path start').addTo(windLayer);
      L.circleMarker(end, {radius:6, color:'#d32f2f', fillColor:'#d32f2f', fillOpacity:1}).bindPopup('Cyclone path end').addTo(windLayer);

      if (L.polylineDecorator){
        cycloneArrowLayer = L.polylineDecorator(cyclonePathLayer, {
          patterns: [{ offset: 25, repeat: 80, symbol: L.Symbol.arrowHead({ pixelSize: 10, polygon: false, pathOptions: { stroke: true, color: '#0057b7', weight: 2 }}) }]
        }).addTo(map);
      }

      map.fitBounds(cyclonePathLayer.getBounds(), { padding: [20,20] });
    }

    function pass
