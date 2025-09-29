import streamlit as st
from streamlit.components.v1 import html as st_html
import pandas as pd
import io

st.set_page_config(page_title="Lightning — Fixed Area", layout="wide")
st.markdown("#### HLMA Website")

# --- Uploaders ---
col1, col2 = st.columns(2)
with col1:
    upl_points = st.file_uploader(
        "Upload Lightning Points CSV (lat, lon, alt[, time])",
        type=["csv"],
        help="Columns expected (case/alias ok): lat/latitude, lon/longitude, alt/altitude[_m], time/timestamp/datetime"
    )
with col2:
    upl_winds = st.file_uploader(
        "Upload Storm/Wind Reports CSV (Lat, Lon[, Comments])",
        type=["csv"],
        help="Columns expected (case/alias ok): Lat/Latitude, Lon/Longitude, Comments/Remark"
    )

# --- Helpers to normalize columns ---
def _find_col(cols, aliases):
    cols_lower = {c.lower(): c for c in cols}
    for a in aliases:
        if a.lower() in cols_lower:
            return cols_lower[a.lower()]
    return None

def load_points_df(file):
    if not file:
        return None
    df = pd.read_csv(file)
    lat_c = _find_col(df.columns, ["lat", "latitude", "y", "Lat", "Latitude"])
    lon_c = _find_col(df.columns, ["lon", "longitude", "x", "Lon", "Longitude"])
    alt_c = _find_col(df.columns, ["alt", "altitude", "altitude_m", "alt_m", "z"])
    time_c = _find_col(df.columns, ["time", "timestamp", "datetime", "date", "Time", "Timestamp"])
    if not (lat_c and lon_c and alt_c):
        st.warning("Points CSV is missing required columns (need lat, lon, alt).")
        return None
    out = pd.DataFrame({
        "lat": pd.to_numeric(df[lat_c], errors="coerce"),
        "lon": pd.to_numeric(df[lon_c], errors="coerce"),
        "alt": pd.to_numeric(df[alt_c], errors="coerce"),
    })
    if time_c:
        out["time"] = df[time_c].astype(str)
    else:
        out["time"] = ""
    out = out.dropna(subset=["lat", "lon", "alt"])
    return out

def load_wind_df(file):
    if not file:
        return None
    df = pd.read_csv(file)
    lat_c = _find_col(df.columns, ["Lat", "Latitude", "lat", "latitude", "y"])
    lon_c = _find_col(df.columns, ["Lon", "Longitude", "lon", "longitude", "x"])
    com_c = _find_col(df.columns, ["Comments", "Comment", "Remark", "Remarks", "Desc", "Description"])
    if not (lat_c and lon_c):
        st.info("Storm report CSV has no Lat/Lon columns; wind layer will be empty.")
        return None
    out = pd.DataFrame({
        "Lat": pd.to_numeric(df[lat_c], errors="coerce"),
        "Lon": pd.to_numeric(df[lon_c], errors="coerce"),
        "Comments": df[com_c].astype(str) if com_c else "",
    }).dropna(subset=["Lat", "Lon"])
    return out

points_df = load_points_df(upl_points) if upl_points else None
winds_df  = load_wind_df(upl_winds) if upl_winds else None

# Convert to JSON (compact) for injection into the HTML
def df_to_js_records(df):
    if df is None or df.empty:
        return "[]"
    # Build a minimal CSV then parse in JS? Not needed—just dump to JSON-like string safely.
    # We'll do a very compact JSON to keep component size reasonable.
    return df.to_json(orient="records")

points_js = df_to_js_records(points_df)
winds_js  = df_to_js_records(winds_df)

# --- HTML/JS App (uses INIT_DATA if provided; else falls back to GitHub CSVs) ---
st_html(f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Lightning Map</title>

  <!-- Leaflet -->
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

  <!-- Leaflet.markercluster -->
  <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css" />
  <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css" />
  <script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>

  <!-- Leaflet.heat -->
  <script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js"></script>

  <!-- PapaParse (fallback fetch only) -->
  <script src="https://cdnjs.cloudflare.com/ajax/libs/PapaParse/5.3.0/papaparse.min.js"></script>

  <style>
    :root {{ --sidebar-w: 320px; }}
    html, body {{ height: 100%; }}
    body {{ margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; }}
    header {{ padding: 12px 16px; background:#0d47a1; color:white; }}
    header h2 {{ margin: 0; font-size: 18px; }}
    #app {{ display:flex; min-height: calc(100vh - 48px); }}
    #sidebar {{
      width: var(--sidebar-w);
      padding: 12px;
      box-shadow: 2px 0 6px rgba(0,0,0,0.08);
      z-index: 999;
      background: #fff;
    }}
    #map {{ flex:1; width: 100%; height: 80vh; min-height: 600px; position: relative; }}
    fieldset {{ border:1px solid #e0e0e0; border-radius:8px; margin-bottom:12px; }}
    legend {{ padding:0 6px; font-weight:600; }}
    label {{ display:block; margin:8px 0 4px; font-size: 13px; }}
    select, input[type="number"] {{ width:100%; }}
    .row {{ display:flex; gap:8px; }}
    .row > div {{ flex:1; }}
    .summary {{
      position: absolute; top: 16px; left: 16px; background: rgba(255,255,255,0.95);
      padding:10px; border-radius:8px; box-shadow:0 2px 6px rgba(0,0,0,0.15); font-size:12px;
      min-width: 220px; z-index: 500;
    }}
    .summary h4 {{ margin: 0 0 6px 0; font-size: 13px; }}
    .stat {{ display:flex; justify-content: space-between; margin: 2px 0; }}
    .legend {{
      position: absolute; bottom: 16px; left: 16px; background: rgba(255,255,255,0.97);
      padding:12px; border-radius:8px; box-shadow:0 2px 6px rgba(0,0,0,0.15); font-size:12px;
      z-index: 500; min-width: 240px;
    }}
    .legend h4 {{ margin: 0 0 8px 0; font-size: 13px; }}
    .legend-item {{ display:flex; align-items:center; gap:10px; margin:6px 0; }}
    .dot {{ width: 12px; height: 12px; border-radius: 50%; display:inline-block; border:1px solid rgba(0,0,0,0.25); }}
    .dot.low {{ background:#ffe633; }}
    .dot.med {{ background:#ffc300; }}
    .dot.high {{ background:#ff5733; }}
    .dot.extreme {{ background:#c70039; }}
    .cluster-badge {{
      display:inline-flex; align-items:center; justify-content:center;
      width: 22px; height: 22px; border-radius: 50%;
      background:#1976d2; color:#fff; font-weight:700; font-size:11px; border:1px solid rgba(0,0,0,0.2);
    }}
    .wind-badge {{
      display:inline-block; color:blue; font-weight:700; font-size:16px; line-height:1;
    }}
    .legend-note {{ color:#555; font-size:11px; margin-top:6px; }}
    button {{ cursor:pointer; padding:8px 10px; border:1px solid #d0d0d0; background:#fafafa; border-radius:8px; }}
    button:hover {{ background:#f0f0f0; }}
    .footer-note {{ font-size: 11px; color:#666; margin-top:8px; }}
    @media (max-width: 980px){{
      #app {{ flex-direction: column; }}
      #sidebar {{ width: auto; box-shadow: none; border-bottom:1px solid #eee; }}
      #map {{ height: 70vh; min-height: 420px; }}
      .summary {{ position: static; margin: 8px; }}
      .legend {{ left: auto; right: 16px; }}
    }}
  </style>

  <!-- Inject uploaded data here -->
  <script>
    window.INIT_DATA = {{
      points: {points_js},
      winds:  {winds_js}
    }};
    // Flags for availability
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
          </div>
          <div class="footer-note" id="refresh-note">Auto-refresh is off.</div>
        </fieldset>

        <fieldset>
          <legend>Downloads</legend>
          <button id="download-points">Download filtered points (CSV)</button>
        </fieldset>

        <div class="footer-note">
          If no uploads provided, falls back to GitHub sample CSVs. Tiles © OpenStreetMap.
        </div>
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
        <div class="legend-item"><span class="wind-badge">W</span> <span>Wind report</span></div>
        <div class="legend-note">Heatmap shows density hotspots (toggle in sidebar).</div>
      </div>
    </div>
  </div>

  <script>
    let map, clusterGroup, plainGroup, heatLayer, windLayer;
    let allMarkers = [];
    let refreshTimer = null;

    function riskColor(alt){{ if (alt < 12000) return '#ffe633'; if (alt < 14000) return '#ffc300'; if (alt < 16000) return '#ff5733'; return '#c70039'; }}
    function riskTier(alt){{ if (alt < 12000) return 'low'; if (alt < 14000) return 'med'; if (alt < 16000) return 'high'; return 'extreme'; }}
    function debounce(fn, ms){{ let t; return function(){{ clearTimeout(t); t = setTimeout(()=>fn.apply(this, arguments), ms); }}; }}
    function parseTime(val){{ if (!val) return null; if (!isNaN(val)) {{ const n=Number(val); if (n>1e10) return new Date(n); }} const d=new Date(val); return isNaN(d.getTime())?null:d; }}

    // Data loaders: prefer injected INIT_DATA, else fallback to GitHub CSVs
    function loadAltitudeData(){{
      if (window.HAS_POINTS) return Promise.resolve(window.INIT_DATA.points);
      return new Promise((resolve, reject)=>{{
        Papa.parse('https://raw.githubusercontent.com/kyo330/HLMA/main/filtered_LYLOUT_230924_210000_0600.csv', {{
          download:true, header:true, complete: (res)=>resolve(res.data), error: reject
        }});
      }});
    }}
    function loadWindData(){{
      if (window.HAS_WINDS) return Promise.resolve(window.INIT_DATA.winds);
      return new Promise((resolve, reject)=>{{
        Papa.parse('https://raw.githubusercontent.com/kyo330/HLMA/main/230924_rpts_wind.csv', {{
          download:true, header:true, complete: (res)=>resolve(res.data), error: reject
        }});
      }});
    }}

    function initMap(){{
      map = L.map('map').setView([30.7, -95.2], 8);
      L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ attribution: '© OpenStreetMap contributors' }}).addTo(map);
      clusterGroup = L.markerClusterGroup({{ disableClusteringAtZoom: 12 }});
      plainGroup = L.layerGroup();
      windLayer = L.layerGroup().addTo(map);
      setTimeout(()=>{{ map.invalidateSize(); }}, 300);
    }}

    function buildPoints(rows){{
      allMarkers = []; clusterGroup.clearLayers(); plainGroup.clearLayers();
      rows.forEach(r=>{{
        // accept both injected schema and fallback schema
        const lat = parseFloat(r.lat ?? r.Lat ?? r.latitude ?? r.Latitude);
        const lon = parseFloat(r.lon ?? r.Lon ?? r.longitude ?? r.Longitude);
        const alt = parseFloat(r.alt ?? r.altitude ?? r.altitude_m ?? r.Alt ?? r.Altitude);
        const t = parseTime(r.time ?? r.Time ?? r.timestamp ?? r.Timestamp ?? r.datetime ?? r.Date);
        if (isNaN(lat) || isNaN(lon) || isNaN(alt)) return;
        const color = riskColor(alt), tier = riskTier(alt);
        const m = L.circleMarker([lat, lon], {{
          radius: tier==='low'?3:tier==='med'?5:tier==='high'?7:9,
          color, fillColor: color, fillOpacity: 0.85, opacity: 1, weight: 1
        }}).bindPopup(
          `<b>Altitude:</b> ${{alt}} m<br><b>Tier:</b> ${{tier.toUpperCase()}}<br>` +
          (t? `<b>Time:</b> ${{t.toISOString()}}<br>`:'' ) + `(${{lat.toFixed(3)}}, ${{lon.toFixed(3)}})`
        );
        allMarkers.push({{marker: m, alt, time: t, lat, lon, tier}});
      }});
      allMarkers.forEach(o=>clusterGroup.addLayer(o.marker));
      if (!map.hasLayer(clusterGroup)) clusterGroup.addTo(map);
      document.getElementById('sum-total').textContent = allMarkers.length;
    }}

    function buildWindMarkers(rows){{
      windLayer.clearLayers();
      rows.forEach(r=>{{
        const lat = parseFloat(r.Lat ?? r.lat ?? r.Latitude ?? r.latitude);
        const lon = parseFloat(r.Lon ?? r.lon ?? r.Longitude ?? r.longitude);
        const comments = r.Comments ?? r.Comment ?? r.Remark ?? r.Description ?? '';
        if (isNaN(lat) || isNaN(lon)) return;
        L.marker([lat, lon], {{
          icon: L.divIcon({{ className:'wind-icon', html:'<span style="color:blue; font-weight:700; font-size:20px;">W</span>', iconSize:[24,24], iconAnchor:[12,12] }})
        }}).bindPopup(`<b>Wind Event:</b> ${{comments || 'N/A'}}<br>(${{lat.toFixed(3)}}, ${{lon.toFixed(3)}})`).addTo(windLayer);
      }});
    }}

    function passAltitude(alt, range){{
      if (range==='lt12') return alt<12000;
      if (range==='12-14') return alt>=12000 && alt<14000;
      if (range==='14-16') return alt>=14000 && alt<16000;
      if (range==='gt16') return alt>=16000;
      return true;
    }}
    function passRecent(t, mins){{
      if (!mins || mins<=0 || !t) return true;
      const now = new Date(), cutoff = new Date(now.getTime() - mins*60*1000);
      return t >= cutoff;
    }}

    function applyFilters(){{
      const altRange = document.getElementById('altitude-filter').value;
      const cluster = document.getElementById('cluster-toggle').value === 'on';
      const heat = document.getElementById('heat-toggle').value === 'on';
      const mins = parseInt(document.getElementById('recent-mins').value || '0', 10);

      clusterGroup.clearLayers(); plainGroup.clearLayers();
      if (heatLayer){{ map.removeLayer(heatLayer); heatLayer = null; }}

      const ptsForHeat = []; let visible=0, cLow=0, cMed=0, cHigh=0, cExtreme=0;

      allMarkers.forEach(o=>{{
        const ok = passAltitude(o.alt, altRange) && passRecent(o.time, mins);
        if (ok){{
          visible++;
          if (o.tier==='low') cLow++; else if (o.tier==='med') cMed++; else if (o.tier==='high') cHigh++; else cExtreme++;
          if (cluster) clusterGroup.addLayer(o.marker); else plainGroup.addLayer(o.marker);
          ptsForHeat.push([o.lat, o.lon, 0.5 + Math.min(1, Math.max(0, (o.alt-10000)/8000)) ]);
        }}
      }});

      if (cluster){{ if (!map.hasLayer(clusterGroup)) map.addLayer(clusterGroup); if (map.hasLayer(plainGroup)) map.removeLayer(plainGroup); }}
      else {{ if (!map.hasLayer(plainGroup)) map.addLayer(plainGroup); if (map.hasLayer(clusterGroup)) map.removeLayer(clusterGroup); }}

      if (heat){{ heatLayer = L.heatLayer(ptsForHeat, {{ radius: 25, blur: 20, maxZoom: 11 }}); heatLayer.addTo(map); }}

      document.getElementById('sum-visible').textContent = visible;
      document.getElementById('sum-low').textContent = cLow;
      document.getElementById('sum-med').textContent = cMed;
      document.getElementById('sum-high').textContent = cHigh;
      document.getElementById('sum-extreme').textContent = cExtreme;

      setTimeout(()=>{{ map.invalidateSize(); }}, 150);
    }}

    function downloadCSV(rows, filename) {{
      const csvContent = rows.map(r => r.map(x => (typeof x === 'string' && x.includes(',')) ? ('"' + x.replace(/"/g,'""') + '"') : x).join(",")).join("\\n");
      const blob = new Blob([csvContent], {{ type: "text/csv;charset=utf-8;" }});
      const url = URL.createObjectURL(blob); const a = document.createElement('a');
      a.href = url; a.download = filename; document.body.appendChild(a); a.click(); document.body.removeChild(a);
    }}
    document.getElementById('download-points').addEventListener('click', ()=>{
      const altRange = document.getElementById('altitude-filter').value;
      const mins = parseInt(document.getElementById('recent-mins').value || '0', 10);
      const rows = [["lat","lon","altitude_m","tier","time_iso"]];
      allMarkers.forEach(o=>{{ if (passAltitude(o.alt, altRange) && passRecent(o.time, mins)) rows.push([o.lat, o.lon, o.alt, o.tier, o.time? o.time.toISOString(): ""]); }});
      downloadCSV(rows, "filtered_points.csv");
    });

    function setAutoRefresh(enabled, seconds){{
      const note = document.getElementById('refresh-note');
      if (refreshTimer){{ clearInterval(refreshTimer); refreshTimer = null; }}
      if (!enabled){{ note.textContent = 'Auto-refresh is off.'; return; }}
      note.textContent = 'Auto-refresh every ' + seconds + ' seconds.';
      // With local uploads there's nothing to "re-fetch" unless page reloads; keep for fallback demo
      refreshTimer = setInterval(()=>{{ reloadData(); }}, Math.max(30, seconds) * 1000);
    }}

    function reloadData(){{
      Promise.all([loadAltitudeData(), loadWindData()]).then(([altRows, windRows])=>{
        buildPoints(altRows || []); buildWindMarkers(windRows || []); applyFilters();
      }).catch(err=>{{ console.error('Data reload error:', err); }});
    }}

    document.getElementById('altitude-filter').addEventListener('change', debounce(applyFilters, 150));
    document.getElementById('cluster-toggle').addEventListener('change', applyFilters);
    document.getElementById('heat-toggle').addEventListener('change', applyFilters);
    document.getElementById('recent-mins').addEventListener('change', debounce(applyFilters, 150));
    document.getElementById('auto-refresh').addEventListener('change', ()=>{{ const on = document.getElementById('auto-refresh').value==='on'; const sec = parseInt(document.getElementById('refresh-sec').value||'120',10); setAutoRefresh(on, sec); }});
    document.getElementById('refresh-sec').addEventListener('change', ()=>{{ const on = document.getElementById('auto-refresh').value==='on'; const sec = parseInt(document.getElementById('refresh-sec').value||'120',10); setAutoRefresh(on, sec); }});

    window.addEventListener('load', ()=>{{ initMap(); reloadData(); }});
  </script>
</body>
</html>
''', height=900, scrolling=True)
