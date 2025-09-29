# app.py
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
        help="Upload the exported .dat file. We'll parse numeric rows and auto-detect lat/lon/alt (and time if present)."
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
    return re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", t or "") is not None

def parse_lma_dat(file) -> pd.DataFrame | None:
    """
    Heuristic parser for HLMA/LMA .dat exports.
    - Reads lines, keeps rows that start with numeric and contain >=4 numeric tokens.
    - Builds a numeric matrix and infers lat, lon, alt, time columns.
    Returns DataFrame with columns: lat, lon, alt (meters), time (str, optional).
    """
    try:
        text = file.read().decode("ascii", errors="replace")
    except Exception:
        text = file.read().decode("utf-8", errors="replace")

    rows = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        toks = s.split()
        if len(toks) < 4:
            continue
        if not _is_float_token(toks[0]):
            # header or comment
            continue
        num_toks = [t for t in toks if _is_float_token(t)]
        if len(num_toks) < 4:
            continue
        rows.append([float(t) if _is_float_token(t) else None for t in toks])

    if not rows:
        st.error("Could not find numeric data rows in the .dat file.")
        return None

    # pad to same width
    max_cols = max(len(r) for r in rows)
    rows = [r + [None]*(max_cols - len(r)) for r in rows]
    df = pd.DataFrame(rows, columns=[f"c{i+1}" for i in range(max_cols)])

    # infer lat/lon candidates
    def in_range(series, lo, hi, min_ratio=0.9):
        s = series.dropna()
        if s.empty:
            return False
        return (s.between(lo, hi).mean() >= min_ratio) and (s.std() > 1e-6)

    lat_cands = [c for c in df.columns if in_range(df[c], -90, 90)]
    lon_cands = [c for c in df.columns if in_range(df[c], -180, 180)]

    # If multiple candidates, pick pair that correlates (rough heuristic: not identical and reasonable variance)
    lat_col = lat_cands[0] if lat_cands else None
    lon_col = lon_cands[0] if lon_cands else None

    # altitude: positive, likely < 30 km if in meters (30000), or < 30 if km
    alt_cands = []
    for c in df.columns:
        s = df[c].dropna()
        if s.empty: 
            continue
        pos_ratio = (s > 0).mean()
        if pos_ratio >= 0.95:
            alt_cands.append(c)
    # prefer column with median between ~100 and ~30000 (meters)
    def alt_score(series):
        s = series.dropna()
        med = s.median()
        if 50 <= med <= 30000: return 2
        if 0.05 <= med <= 50:  return 1  # likely km; will convert
        return 0

    alt_col = None
    best = (-1, None)
    for c in alt_cands:
        sc = alt_score(df[c])
        if sc > best[0]:
            best = (sc, c)
    alt_col = best[1]

    # time: monotonic-ish increasing for many rows (seconds)
    time_col = None
    best_time_score = -1
    for c in df.columns:
        s = df[c].dropna().astype(float)
        if len(s) < 10:
            continue
        diffs = s.diff().dropna()
        if len(diffs) == 0:
            continue
        inc_ratio = (diffs >= 0).mean()
        if inc_ratio > best_time_score and inc_ratio >= 0.6:
            best_time_score = inc_ratio
            time_col = c

    if not (lat_col and lon_col and alt_col):
        st.error(f"Failed to infer columns. Found lat candidates={lat_cands}, lon candidates={lon_cands}, alt candidates={alt_cands}.")
        return None

    out = pd.DataFrame({
        "lat": df[lat_col],
        "lon": df[lon_col],
        "alt": df[alt_col],
    }).dropna(subset=["lat", "lon", "alt"]).copy()

    # Convert altitude to meters if it looks like km
    # If 80% of values are < 50, treat as km and convert
    s = out["alt"].dropna()
    if not s.empty and (s < 50).mean() >= 0.8:
        out["alt"] = out["alt"] * 1000.0

    # time column to ISO-friendly string if present
    if time_col:
        # keep as numeric seconds; JS will try to parse as Date(..) if > 1e10 epoch ms, so leave a string
        out["time"] = df[time_col].astype(str)
    else:
        out["time"] = ""

    return out

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
    }).dropna(subset=["Lat", "Lon"])
    return out

points_df = parse_lma_dat(upl_points) if upl_points else None
winds_df  = load_wind_df(upl_winds) if upl_winds else None

def df_to_js_records(df):
    return df.to_json(orient="records") if (df is not None and not df.empty) else "[]"

points_js = df_to_js_records(points_df)
winds_js  = df_to_js_records(winds_df)

# ─────────────────────────── HTML (no external file links) ───────────────────────────
html_tpl = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Lightning Map</title>

  <!-- Leaflet + plugins (no external data links) -->
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
          <div class="footer-note">Path is built from storm/wind report coordinates in time order if available.</div>
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
        <div class="legend-note">Heatmap shows density hotspots (toggle in sidebar).</div>
      </div>
    </div>
  </div>

  <script>
    let map, clusterGroup, plainGroup, heatLayer, windLayer;
    let cyclonePathLayer = null, cycloneArrowLayer = null;
    let allMarkers = [];
    let didFitToPathOnce = false;

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

      // Cyclone path
      const pathToggle = document.getElementById('path-toggle')?.value !== 'off';
      const arrowsToggle = document.getElementById('arrows-toggle')?.value !== 'off';

      if (!pathToggle || seq.length < 2) return;

      if (window.cyclonePathLayer){ map.removeLayer(window.cyclonePathLayer); window.cyclonePathLayer = null; }
      if (window.cycloneArrowLayer){ map.removeLayer(window.cycloneArrowLayer); window.cycloneArrowLayer = null; }

      const withTime = seq.filter(s => s.t instanceof Date && !isNaN(s.t));
      const noTime   = seq.filter(s => !(s.t instanceof Date) || isNaN(s.t));
      withTime.sort((a,b)=> a.t - b.t);
      const ordered = withTime.concat(noTime);
      const latlngs = ordered.map(o => [o.lat, o.lon]);

      window.cyclonePathLayer = L.polyline(latlngs, { color: '#0057b7', weight: 3, opacity: 0.95 }).addTo(map);

      const start = latlngs[0], end = latlngs[latlngs.length - 1];
      L.circleMarker(start, {radius:6, color:'#0057b7', fillColor:'#0057b7', fillOpacity:1})
        .bindPopup('<b>Cyclone path start</b>').addTo(windLayer);
      L.circleMarker(end, {radius:6, color:'#d32f2f', fillColor:'#d32f2f', fillOpacity:1})
        .bindPopup('<b>Cyclone path end</b>').addTo(windLayer);

      if (arrowsToggle && L.polylineDecorator){
        window.cycloneArrowLayer = L.polylineDecorator(window.cyclonePathLayer, {
          patterns: [
            { offset: 25, repeat: 80, symbol: L.Symbol.arrowHead({ pixelSize: 10, polygon: false, pathOptions: { stroke: true, color: '#0057b7', weight: 2 }}) }
          ]
        }).addTo(map);
      }

      if (!didFitToPathOnce){
        map.fitBounds(window.cyclonePathLayer.getBounds(), { padding: [20,20] });
        didFitToPathOnce = true;
      }
    }

    function passAltitude(alt, range){
      if (range==='lt12') return alt<12000;
      if (range==='12-14') return alt>=12000 && alt<14000;
      if (range==='14-16') return alt>=14000 && alt<16000;
      if (range==='gt16') return alt>=16000;
      return true;
    }
    function passRecent(t, mins){
      if (!mins || mins<=0 || !t) return true;
      const now = new Date(), cutoff = new Date(now.getTime() - mins*60*1000);
      return t >= cutoff;
    }

    function applyFilters(){
      const altRange = document.getElementById('altitude-filter').value;
      const cluster = document.getElementById('cluster-toggle').value === 'on';
      const heat = document.getElementById('heat-toggle').value === 'on';
      const mins = parseInt(document.getElementById('recent-mins').value || '0', 10);

      clusterGroup.clearLayers(); plainGroup.clearLayers();
      if (heatLayer){ map.removeLayer(heatLayer); heatLayer = null; }

      const ptsForHeat = []; let visible=0, cLow=0, cMed=0, cHigh=0, cExtreme=0;

      allMarkers.forEach(o=>{
        const ok = passAltitude(o.alt, altRange) && passRecent(o.time, mins);
        if (ok){
          visible++;
          if (o.tier==='low') cLow++; else if (o.tier==='med') cMed++; else if (o.tier==='high') cHigh++; else cExtreme++;
          if (cluster) clusterGroup.addLayer(o.marker); else plainGroup.addLayer(o.marker);
          ptsForHeat.push([o.lat, o.lon, 0.5 + Math.min(1, Math.max(0, (o.alt-10000)/8000)) ]);
        }
      });

      if (cluster){ if (!map.hasLayer(clusterGroup)) map.addLayer(clusterGroup); if (map.hasLayer(plainGroup)) map.removeLayer(plainGroup); }
      else { if (!map.hasLayer(plainGroup)) map.addLayer(plainGroup); if (map.hasLayer(clusterGroup)) map.removeLayer(clusterGroup); }

      if (heat){ heatLayer = L.heatLayer(ptsForHeat, { radius: 25, blur: 20, maxZoom: 11 }); heatLayer.addTo(map); }

      document.getElementById('sum-visible').textContent = visible;
      document.getElementById('sum-low').textContent = cLow;
      document.getElementById('sum-med').textContent = cMed;
      document.getElementById('sum-high').textContent = cHigh;
      document.getElementById('sum-extreme').textContent = cExtreme;

      setTimeout(()=>{ map.invalidateSize(); }, 150);
    }

    function downloadCSV(rows, filename) {
      const csvContent = rows.map(r => r.map(x => (typeof x === 'string' && x.includes(',')) ? ('"' + x.replace(/"/g,'""') + '"') : x).join(",")).join("\\n");
      const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob); const a = document.createElement('a');
      a.href = url; a.download = filename; document.body.appendChild(a); a.click(); document.body.removeChild(a);
    }
    document.getElementById('download-points').addEventListener('click', ()=>{
      const altRange = document.getElementById('altitude-filter').value;
      const mins = parseInt(document.getElementById('recent-mins').value || '0', 10);
      const rows = [["lat","lon","altitude_m","tier","time_iso"]];
      allMarkers.forEach(o=>{ if (passAltitude(o.alt, altRange) && passRecent(o.time, mins)) rows.push([o.lat, o.lon, o.alt, o.tier, o.time? o.time.toISOString(): ""]); });
      downloadCSV(rows, "filtered_points.csv");
    });

    function reloadData(){
      // All data is injected (no external links). Just (re)build layers from window.INIT_DATA.
      const altRows = window.INIT_DATA.points || [];
      const windRows = window.INIT_DATA.winds || [];
      buildPoints(altRows); buildWindMarkers(windRows); applyFilters();
    }

    // Listeners
    document.getElementById('altitude-filter').addEventListener('change', debounce(applyFilters, 150));
    document.getElementById('cluster-toggle').addEventListener('change', applyFilters);
    document.getElementById('heat-toggle').addEventListener('change', applyFilters);
    document.getElementById('recent-mins').addEventListener('change', debounce(applyFilters, 150));
    document.getElementById('path-toggle').addEventListener('change', reloadData);
    document.getElementById('arrows-toggle').addEventListener('change', reloadData);

    window.addEventListener('load', ()=>{ initMap(); reloadData(); });
  </script>
</body>
</html>
"""

# Inject JSON safely (no f-strings)
html_final = html_tpl.replace("__POINTS__", df_to_js_records(points_df)).replace("__WINDS__", df_to_js_records(winds_df))

st_html(html_final, height=900, scrolling=True)
