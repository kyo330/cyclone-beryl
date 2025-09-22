
import os
import io
import zipfile
import pandas as pd
import numpy as np
import streamlit as st
import pydeck as pdk
from datetime import datetime, timezone

st.set_page_config(page_title="Beryl Lightning Explorer", layout="wide")

st.title("⚡ Beryl Lightning Explorer (ENTLN pulses)")

with st.sidebar:
    st.header("Data")
    st.write("Upload one or more CSVs exported from ENTLN (columns like `timestamp, latitude, longitude, peakcurrent, icheight, type`).")
    files = st.file_uploader("CSV files", type=["csv"], accept_multiple_files=True)
    tz_choice = st.selectbox("Display times in", ["UTC", "Local (browser offset)"], index=0)
    st.caption("Tip: Use the Filters section to focus on a day, polarity, or region.")

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
    # Standardize schema
    # Required cols
    required = ["timestamp", "latitude", "longitude"]
    for col in required:
        if col not in df.columns:
            st.error(f"Missing required column `{col}`")
            return pd.DataFrame()
    # Parse time
    def _parse_ts(x):
        try:
            return datetime.fromisoformat(str(x).replace("Z","")).replace(tzinfo=timezone.utc)
        except Exception:
            return pd.NaT
    df["time_utc"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    if df["time_utc"].isna().all():
        df["time_utc"] = df["timestamp"].map(_parse_ts)
    df = df.dropna(subset=["time_utc"]).copy()
    # Minor cleanups
    for c in ["peakcurrent","icheight","type","numbersensors","majoraxis","minoraxis","bearing"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    # Polarity (approx from peakcurrent sign)
    if "peakcurrent" in df.columns:
        df["polarity"] = np.where(df["peakcurrent"]>=0, "Positive", "Negative")
    else:
        df["polarity"] = "Unknown"
    # Flash class (heuristic from 'type' if present)
    if "type" in df.columns:
        df["class"] = df["type"].map({0:"CG",1:"IC"}).fillna("Unknown")
    else:
        df["class"] = "Unknown"
    # Hours and date
    df["date"] = df["time_utc"].dt.date
    df["hour"] = df["time_utc"].dt.floor("h")
    return df

df = load_csvs(files)

if df.empty:
    st.info("Upload CSVs to begin. (You can start with the three example CSVs you mentioned.)")
    st.stop()

# Global bounds & time range
min_t, max_t = df["time_utc"].min(), df["time_utc"].max()
min_lat, max_lat = float(df["latitude"].min()), float(df["latitude"].max())
min_lon, max_lon = float(df["longitude"].min()), float(df["longitude"].max())

colA, colB, colC, colD = st.columns(4)
colA.metric("Total pulses", f"{len(df):,}")
colB.metric("Time span (UTC)", f"{min_t:%Y-%m-%d %H:%M} → {max_t:%Y-%m-%d %H:%M}")
colC.metric("Lat range", f"{min_lat:.2f} … {max_lat:.2f}")
colD.metric("Lon range", f"{min_lon:.2f} … {max_lon:.2f}")

st.divider()
st.subheader("Filters")

c1, c2, c3, c4 = st.columns([2,2,2,2])
with c1:
    t_start, t_end = st.slider(
        "Time window (UTC)",
        min_value=min_t.to_pydatetime(),
        max_value=max_t.to_pydatetime(),
        value=(min_t.to_pydatetime(), max_t.to_pydatetime()),
        format="YYYY-MM-DD HH:mm",
    )
with c2:
    classes = sorted(df["class"].unique().tolist())
    class_sel = st.multiselect("Class", classes, default=classes)
with c3:
    pols = sorted(df["polarity"].unique().tolist())
    pol_sel = st.multiselect("Polarity", pols, default=pols)
with c4:
    peak_abs_max = float(np.nanmax(np.abs(df.get("peakcurrent", pd.Series([0])))))
    peak_range = st.slider("Abs(peakcurrent) ≥ (kA)", 0.0, max(1.0, peak_abs_max/1000.0), value=0.0, step=0.1)

bb1, bb2, bb3, bb4 = st.columns(4)
with bb1:
    lat_min_f = st.number_input("Lat min", value=min_lat, step=0.1)
with bb2:
    lat_max_f = st.number_input("Lat max", value=max_lat, step=0.1)
with bb3:
    lon_min_f = st.number_input("Lon min", value=min_lon, step=0.1)
with bb4:
    lon_max_f = st.number_input("Lon max", value=max_lon, step=0.1)

mask = (
    (df["time_utc"] >= pd.Timestamp(t_start, tz="UTC")) &
    (df["time_utc"] <= pd.Timestamp(t_end, tz="UTC")) &
    (df["latitude"].between(lat_min_f, lat_max_f)) &
    (df["longitude"].between(lon_min_f, lon_max_f)) &
    (df["class"].isin(class_sel)) &
    (df["polarity"].isin(pol_sel))
)
if "peakcurrent" in df.columns:
    mask &= (np.abs(df["peakcurrent"]) >= (peak_range*1000.0))

view = df.loc[mask].copy()

st.success(f"Filtered pulses: {len(view):,}")

# ================== Time Series ==================
st.subheader("Temporal structure")
ts_res = st.radio("Aggregate by", ["Minute","Hour","Day"], horizontal=True, index=1)
ts_key = {"Minute":"min","Hour":"hour","Day":"date"}[ts_res]
if ts_key == "min":
    view["bucket"] = view["time_utc"].dt.floor("min")
elif ts_key == "hour":
    view["bucket"] = view["time_utc"].dt.floor("h")
else:
    view["bucket"] = view["time_utc"].dt.floor("d")

agg = view.groupby("bucket").size().rename("count").reset_index()
st.line_chart(agg.set_index("bucket"))

# ================== Map Views ==================
st.subheader("Maps")

# Center map
center_lat = float(view["latitude"].median()) if not view.empty else (min_lat + max_lat)/2
center_lon = float(view["longitude"].median()) if not view.empty else (min_lon + max_lon)/2
init_view = pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=5, pitch=30)

# Scatter layer
if "peakcurrent" in view.columns:
    # size by log abs current
    size = np.clip(np.log1p(np.abs(view["peakcurrent"])).fillna(0), 1, None)
else:
    size = pd.Series(5, index=view.index)
color = view["class"].map({"IC":[0,128,255,140],"CG":[255,80,0,160]}).fillna([180,180,180,140])

scatter = pdk.Layer(
    "ScatterplotLayer",
    data=view.assign(size=size, color=list(color)),
    get_position=["longitude","latitude"],
    get_color="color",
    get_radius="size*500",
    pickable=True,
    radius_min_pixels=1,
    auto_highlight=True,
)

# Heatmap layer
heat = pdk.Layer(
    "HeatmapLayer",
    data=view,
    get_position=["longitude","latitude"],
    aggregation=pdk.types.String("MEAN"),
)

st.pydeck_chart(pdk.Deck(
    map_style="mapbox://styles/mapbox/light-v10",
    initial_view_state=init_view,
    layers=[scatter, heat],
    tooltip={"text":"{class} | {polarity}\n{time_utc}\nI: {peakcurrent} A\nh: {icheight} m"},
))

# ================== Distributions ==================
st.subheader("Distributions")
d1, d2, d3 = st.columns(3)

with d1:
    if "peakcurrent" in view.columns:
        st.caption("Peak current (kA)")
        st.bar_chart((view["peakcurrent"]/1000.0).dropna())

with d2:
    if "icheight" in view.columns:
        st.caption("In-cloud height (km)")
        st.bar_chart((view["icheight"]/1000.0).dropna())

with d3:
    st.caption("Class share")
    st.bar_chart(view["class"].value_counts())

# ================== Download ==================
st.subheader("Download filtered data")
@st.cache_data
def to_csv_bytes(_df):
    return _df.to_csv(index=False).encode("utf-8")

st.download_button("Download CSV (filtered)", data=to_csv_bytes(view), file_name="beryl_filtered.csv", mime="text/csv")

st.caption("© Analysis app generated for Tropical Cyclone Beryl lightning pulses (ENTLN).")
