import os
import pandas as pd
import numpy as np
import streamlit as st
import pydeck as pdk
from datetime import datetime, timezone

# ----------------------------
# Page config
# ----------------------------
st.set_page_config(page_title="Beryl Lightning Explorer", layout="wide")
st.title("⚡ Beryl Lightning Explorer (ENTLN pulses)")

with st.sidebar:
    st.header("Data")
    st.write(
        "Upload one or more ENTLN CSVs with columns like "
        "`timestamp, latitude, longitude, peakcurrent, icheight, type`."
    )
    files = st.file_uploader("CSV files", type=["csv"], accept_multiple_files=True)
    st.caption("Tip: apply filters for time, class (IC/CG), polarity, bbox, and |peakcurrent|.")
    st.divider()
    st.subheader("Map Options")
    map_mode = st.radio(
        "Layer",
        ["Points", "Hexbin", "Heatmap", "3D Columns"],
        index=0,
        help="Switch between scatter points, hexagon density, heatmap, and 3D columns (extruded by icheight).",
    )
    use_dark = st.checkbox("Dark basemap", value=False)
    map_zoom = st.slider("Map zoom", 3.0, 9.0, value=5.0, step=0.5)
    pitch = st.slider("Map pitch", 0, 60, value=30, step=5)
    st.caption("3D modes look best with higher pitch.")

# ----------------------------
# Data loader
# ----------------------------
@st.cache_data(show_spinner=False)
def load_csvs(file_objs):
    if not file_objs:
        return pd.DataFrame()
    dfs = []
    for f in file_objs:
        try:
            df = pd.read_csv(f)
            df["source_file"] = getattr(f, "name", "uploaded.csv")
            dfs.append(df)
        except Exception as e:
            st.warning(f"Failed to read {getattr(f, 'name', 'file')}: {e}")
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True)

    # Required columns
    for col in ["timestamp", "latitude", "longitude"]:
        if col not in df.columns:
            st.error(f"Missing required column `{col}`")
            return pd.DataFrame()

    # Parse timestamp → UTC
    def _parse_ts(x):
        try:
            return datetime.fromisoformat(str(x).replace("Z", "")).replace(tzinfo=timezone.utc)
        except Exception:
            return pd.NaT

    df["time_utc"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    if df["time_utc"].isna().all():
        df["time_utc"] = df["timestamp"].map(_parse_ts)
    df = df.dropna(subset=["time_utc"]).copy()

    # Numeric cleanups
    for c in ["peakcurrent", "icheight", "type", "numbersensors", "majoraxis", "minoraxis", "bearing"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Heuristics from ENTLN-like schema
    if "peakcurrent" in df.columns:
        df["polarity"] = np.where(df["peakcurrent"] >= 0, "Positive", "Negative")
    else:
        df["polarity"] = "Unknown"

    if "type" in df.columns:
        # 0=CG, 1=IC (common ENTLN convention — adjust if your files differ)
        df["class"] = df["type"].map({0: "CG", 1: "IC"}).astype("string").fillna("Unknown")
    else:
        df["class"] = "Unknown"

    # Convenience columns
    df["hour"] = df["time_utc"].dt.floor("h")
    df["minute"] = df["time_utc"].dt.floor("min")

    return df

df = load_csvs(files)
if df.empty:
    st.info("Upload CSVs to begin.")
    st.stop()

# ----------------------------
# Global ranges & summary
# ----------------------------
min_t, max_t = df["time_utc"].min(), df["time_utc"].max()
min_lat, max_lat = float(df["latitude"].min()), float(df["latitude"].max())
min_lon, max_lon = float(df["longitude"].min()), float(df["longitude"].max())

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total pulses", f"{len(df):,}")
m2.metric("Time span (UTC)", f"{min_t:%Y-%m-%d %H:%M} → {max_t:%Y-%m-%d %H:%M}")
m3.metric("Lat range", f"{min_lat:.2f} … {max_lat:.2f}")
m4.metric("Lon range", f"{min_lon:.2f} … {max_lon:.2f}")

st.divider()
st.subheader("Filters")

# --- Time slider (robust tz handling: slider uses NAIVE, filter uses UTC-aware)
def _naive(ts):
    if hasattr(ts, "tz") and ts.tz is not None:
        return ts.tz_convert("UTC").tz_localize(None).to_pydatetime()
    return pd.Timestamp(ts).to_pydatetime()

t_start, t_end = st.slider(
    "Time window (UTC)",
    min_value=_naive(min_t),
    max_value=_naive(max_t),
    value=(_naive(min_t), _naive(max_t)),
    format="YYYY-MM-DD HH:mm",
)

c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
with c1:
    classes = sorted(df["class"].unique().tolist())
    class_sel = st.multiselect("Class", classes, default=classes)
with c2:
    pols = sorted(df["polarity"].unique().tolist())
    pol_sel = st.multiselect("Polarity", pols, default=pols)
with c3:
    peak_abs_max = float(np.nanmax(np.abs(df.get("peakcurrent", pd.Series([0])))))
    peak_kA = st.slider("Abs(peakcurrent) ≥ (kA)", 0.0, max(1.0, peak_abs_max / 1000.0), value=0.0, step=0.1)
with c4:
    agg_choice = st.radio("Playback bucket", ["Minute", "Hour", "Day"], horizontal=True, index=1)

b1, b2, b3, b4 = st.columns(4)
with b1:
    lat_min_f = st.number_input("Lat min", value=min_lat, step=0.1)
with b2:
    lat_max_f = st.number_input("Lat max", value=max_lat, step=0.1)
with b3:
    lon_min_f = st.number_input("Lon min", value=min_lon, step=0.1)
with b4:
    lon_max_f = st.number_input("Lon max", value=max_lon, step=0.1)

def _to_utc(x):
    ts = pd.Timestamp(x)
    return ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")

mask = (
    (df["time_utc"] >= _to_utc(t_start)) &
    (df["time_utc"] <= _to_utc(t_end)) &
    (df["latitude"].between(lat_min_f, lat_max_f)) &
    (df["longitude"].between(lon_min_f, lon_max_f)) &
    (df["class"].isin(class_sel)) &
    (df["polarity"].isin(pol_sel))
)
if "peakcurrent" in df.columns:
    mask &= (np.abs(df["peakcurrent"]) >= (peak_kA * 1000.0))

view = df.loc[mask].copy()
st.success(f"Filtered pulses: {len(view):,}")

# ----------------------------
# Playback bucketing
# ----------------------------
if agg_choice == "Minute":
    view["bucket"] = view["time_utc"].dt.floor("min")
elif agg_choice == "Hour":
    view["bucket"] = view["time_utc"].dt.floor("h")
else:
    view["bucket"] = view["time_utc"].dt.floor("d")

frames = sorted(view["bucket"].dropna().unique())
if frames:
    frame_idx = st.slider(
        "Frame", 0, max(0, len(frames) - 1), value=0, step=1,
        help="Scrub through time buckets to inspect evolution."
    )
    frame_time = frames[frame_idx]
    frame_view = view.loc[view["bucket"] == frame_time].copy()
else:
    frame_time = None
    frame_view = view.copy()

# IMPORTANT: avoid tz objects in Deck data — use a string for tooltips
frame_view["time_str"] = frame_view["time_utc"].dt.strftime("%Y-%m-%d %H:%M:%S UTC")

st.caption(f"Current frame: {frame_time}" if frame_time is not None else "Current frame: (all)")

# ----------------------------
# Time series (counts)
# ----------------------------
st.subheader("Temporal structure")
ts = view.groupby("bucket").size().rename("count").reset_index()
st.line_chart(ts.set_index("bucket"))

# ----------------------------
# Map
# ----------------------------
st.subheader("Map")

if frame_view.empty:
    st.info("No data in this frame with the chosen filters.")
    st.stop()

# Center + style
center_lat = float(frame_view["latitude"].median()) if not frame_view.empty else (min_lat + max_lat) / 2
center_lon = float(frame_view["longitude"].median()) if not frame_view.empty else (min_lon + max_lon) / 2
map_style = "mapbox://styles/mapbox/dark-v11" if use_dark else "mapbox://styles/mapbox/light-v10"
init_view = pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=map_zoom, pitch=pitch)

# Marker sizes by |I|
if "peakcurrent" in frame_view.columns:
    size = np.clip(np.log1p(np.abs(frame_view["peakcurrent"])).replace([np.inf, -np.inf], np.nan).fillna(0), 1, None)
else:
    size = pd.Series(5, index=frame_view.index)

# Robust color mapping (no fillna(list))
color_map = {"IC": [0, 128, 255, 140], "CG": [255, 80, 0, 160]}
default_color = [180, 180, 180, 140]
class_series = frame_view["class"].astype("string").fillna("Unknown")
mapped = class_series.map(color_map)
color = mapped.apply(lambda v: v if isinstance(v, list) else default_color)

layers = []

if map_mode == "Points":
    layers.append(
        pdk.Layer(
            "ScatterplotLayer",
            data=frame_view.assign(size=size, color=list(color)),
            get_position=["longitude", "latitude"],
            get_color="color",
            get_radius="size*500",
            pickable=True,
            radius_min_pixels=1,
            auto_highlight=True,
        )
    )

elif map_mode == "Hexbin":
    layers.append(
        pdk.Layer(
            "HexagonLayer",
            data=frame_view,
            get_position=["longitude", "latitude"],
            elevation_scale=10,
            elevation_range=[0, 1000],
            extruded=True,
            opacity=0.6,
            coverage=1,
            radius=10000,  # meters
            pickable=True,
        )
    )

elif map_mode == "Heatmap":
    layers.append(
        pdk.Layer(
            "HeatmapLayer",
            data=frame_view,
            get_position=["longitude", "latitude"],
            # NOTE: removed aggregation=pdk.types.String("MEAN") to avoid serialization issues
        )
    )

elif map_mode == "3D Columns":
    # Extrude by icheight (scaled). Fallback for missing heights.
    height_km = (frame_view.get("icheight", pd.Series([0] * len(frame_view), index=frame_view.index)) / 1000.0)
    height_km = height_km.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    scaled_h = (height_km * 10000.0).clip(0, 20000)  # scale for visibility
    layers.append(
        pdk.Layer(
            "ColumnLayer",
            data=frame_view.assign(col_elev=scaled_h, color=list(color)),
            get_position=["longitude", "latitude"],
            get_elevation="col_elev",
            elevation_scale=1,
            radius=3000,  # meters
            get_fill_color="color",
            pickable=True,
            extruded=True,
        )
    )

tooltip = {
    # use time_str (plain string) to avoid tz-aware objects in deck JSON
    "text": "{class} | {polarity}\n{time_str}\nI: {peakcurrent} A\nh: {icheight} m"
}

st.pydeck_chart(
    pdk.Deck(
        map_style=map_style,
        initial_view_state=init_view,
        layers=layers,
        tooltip=tooltip,
    )
)

# ----------------------------
# Distributions
# ----------------------------
st.subheader("Distributions")
d1, d2, d3 = st.columns(3)
with d1:
    if "peakcurrent" in view.columns:
        st.caption("Peak current (kA)")
        st.bar_chart((view["peakcurrent"] / 1000.0).dropna())
with d2:
    if "icheight" in view.columns:
        st.caption("In-cloud height (km)")
        st.bar_chart((view["icheight"] / 1000.0).dropna())
with d3:
    st.caption("Class share")
    st.bar_chart(view["class"].value_counts())

# ----------------------------
# Download filtered subset
# ----------------------------
st.subheader("Download filtered data")

@st.cache_data
def to_csv_bytes(_df):
    return _df.to_csv(index=False).encode("utf-8")

st.download_button(
    "Download CSV (filtered)",
    data=to_csv_bytes(view),
    file_name="beryl_filtered.csv",
    mime="text/csv",
)

st.caption("© Streamlit app for Tropical Cyclone Beryl lightning pulses (ENTLN).")
