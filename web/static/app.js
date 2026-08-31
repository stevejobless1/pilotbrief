/**
 * PilotBrief Aviation Web Deck — Live Sectional, Radar, Lightning & Historical Replay
 */

// State
let map;
let sectionalLayer;
let nexradLayer;
let radarFramesLayer;
let sigmetsLayer;
let metarsLayer;
let lightningLayer;
let routeLayer;
let ringsLayer;

let currentDep = "KPAO";
let currentDest = null;

// Replay State
let radarFrames = [];
let currentFrameIndex = -1;
let isPlaying = false;
let playInterval = null;
let playSpeed = 1000; // ms per frame
let isLooping = true;
let isLiveMode = true;

// Lightning State
let allStrikes = [];
let lightningPollInterval = null;

// Clock updates
function updateClocks() {
  const now = new Date();
  const utcHours = String(now.getUTCHours()).padStart(2, "0");
  const utcMins = String(now.getUTCMinutes()).padStart(2, "0");
  const utcSecs = String(now.getUTCSeconds()).padStart(2, "0");
  document.getElementById("utc-clock").textContent = `${utcHours}:${utcMins}:${utcSecs} UTC`;

  const locHours = String(now.getHours()).padStart(2, "0");
  const locMins = String(now.getMinutes()).padStart(2, "0");
  const locSecs = String(now.getSeconds()).padStart(2, "0");
  document.getElementById("local-clock").textContent = `${locHours}:${locMins}:${locSecs} LOCAL`;
}
setInterval(updateClocks, 1000);
updateClocks();

// Initialize Map
function initMap() {
  // Center on KPAO / Bay Area by default with Canvas rendering for optimal speed
  map = L.map("map", {
    center: [37.4611, -122.115],
    zoom: 8,
    minZoom: 3,
    maxZoom: 13,
    zoomControl: false,
    preferCanvas: true
  });

  // Custom Zoom Control top-right
  L.control.zoom({ position: "topright" }).addTo(map);

  // 1. FAA VFR Sectional Basemap (Configured with native zoom scaling so full airspace loads smoothly)
  sectionalLayer = L.tileLayer(
    "https://tiles.arcgis.com/tiles/ssFJjBXIUyZDrSYZ/arcgis/rest/services/VFR_Sectional/MapServer/tile/{z}/{y}/{x}",
    {
      minZoom: 3,
      maxZoom: 13,
      minNativeZoom: 6,
      maxNativeZoom: 11,
      updateWhenIdle: true,
      updateWhenZooming: false,
      keepBuffer: 6,
      attribution: "FAA VFR Sectional &copy; ESRI / ArcGIS"
    }
  ).addTo(map);

  // 2. Interactive Radar Layer (Default to IEM Live WMS)
  nexradLayer = L.tileLayer.wms(
    "https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad/n0q.cgi",
    {
      layers: "nexrad-n0q-m05m",
      format: "image/png",
      transparent: true,
      opacity: 0.70,
      version: "1.1.1",
      attribution: "NEXRAD &copy; IEM / NOAA"
    }
  ).addTo(map);

  // Layer groups
  sigmetsLayer = L.layerGroup().addTo(map);
  metarsLayer = L.layerGroup().addTo(map);
  lightningLayer = L.layerGroup().addTo(map);
  ringsLayer = L.layerGroup().addTo(map);
  routeLayer = L.layerGroup().addTo(map);

  // Map move event -> reload regional METARs (debounced)
  let moveTimeout;
  map.on("moveend", () => {
    clearTimeout(moveTimeout);
    moveTimeout = setTimeout(() => {
      if (document.getElementById("layer-metars").checked) {
        loadRegionalMetars();
      }
      if (document.getElementById("layer-lightning").checked) {
        renderLightning(getCurrentDisplayTimestamp());
      }
    }, 250);
  });

  // Setup UI Listeners
  setupEventListeners();

  // Initial Data Loads
  loadRadarFrames();
  loadLightningData();
  loadSigmets();
  loadRegionalMetars();
  plotRoute();

  // Auto-refresh lightning every 8 seconds
  lightningPollInterval = setInterval(loadLightningData, 8000);

  // Auto-refresh radar frames and METARs every 2 minutes
  setInterval(() => {
    loadRadarFrames();
    loadSigmets();
    loadRegionalMetars();
  }, 120000);
}

// -------------------------------------------------------------
// RADAR FRAMES & TIMELINE REPLAY ENGINE
// -------------------------------------------------------------

async function loadRadarFrames() {
  try {
    const res = await fetch("/api/weather/radar-frames");
    if (!res.ok) return;
    const data = await res.json();
    radarFrames = data.frames || [];

    if (radarFrames.length > 0) {
      const slider = document.getElementById("timeline-slider");
      slider.max = radarFrames.length - 1;
      
      if (isLiveMode || currentFrameIndex === -1) {
        currentFrameIndex = radarFrames.length - 1;
        slider.value = currentFrameIndex;
      }
      
      updateTimelineUI();
    }
  } catch (err) {
    console.error("Error loading radar frames:", err);
  }
}

function setRadarFrame(index) {
  if (index < 0 || index >= radarFrames.length) return;
  currentFrameIndex = index;
  const frame = radarFrames[index];

  // Update slider input value
  document.getElementById("timeline-slider").value = index;

  // If frame has a tile_url, use TileLayer; otherwise fallback to WMS
  const opacity = parseFloat(document.getElementById("radar-opacity").value) / 100.0;
  
  if (document.getElementById("layer-nexrad").checked) {
    if (radarFramesLayer) {
      map.removeLayer(radarFramesLayer);
    }

    if (frame.tile_url) {
      if (map.hasLayer(nexradLayer)) map.removeLayer(nexradLayer);
      
      radarFramesLayer = L.tileLayer(frame.tile_url, {
        opacity: opacity,
        maxZoom: 12,
        minZoom: 3,
        zIndex: 2
      }).addTo(map);
    } else if (frame.wms_url) {
      if (radarFramesLayer) map.removeLayer(radarFramesLayer);
      if (!map.hasLayer(nexradLayer)) map.addLayer(nexradLayer);
      nexradLayer.setParams({ LAYERS: frame.path || "nexrad-n0q-m05m" });
    }
  }

  // Check if current frame is LIVE
  isLiveMode = (index === radarFrames.length - 1);
  updateTimelineUI();

  // Render lightning strikes corresponding to this time window
  renderLightning(frame.time_ms);
}

function getCurrentDisplayTimestamp() {
  if (currentFrameIndex >= 0 && currentFrameIndex < radarFrames.length) {
    return radarFrames[currentFrameIndex].time_ms;
  }
  return Date.now();
}

function updateTimelineUI() {
  if (currentFrameIndex < 0 || currentFrameIndex >= radarFrames.length) return;
  const frame = radarFrames[currentFrameIndex];
  const isLive = (currentFrameIndex === radarFrames.length - 1);

  const timeBadge = document.getElementById("timeline-time-badge");
  const liveIndicator = document.getElementById("live-indicator-badge");

  if (isLive) {
    timeBadge.textContent = `LIVE (${frame.utc_time})`;
    timeBadge.className = "px-2 py-0.5 rounded-lg bg-emerald-500/20 text-emerald-300 font-mono font-bold border border-emerald-500/40 text-xs";
    liveIndicator.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span> LIVE';
    liveIndicator.className = "px-2 py-0.5 text-[10px] font-bold rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 flex items-center gap-1";
  } else {
    const relMin = frame.relative_mins || 0;
    const relStr = relMin < 0 ? `${relMin}m` : `+${relMin}m`;
    timeBadge.textContent = `${frame.utc_time} (${relStr})`;
    timeBadge.className = "px-2 py-0.5 rounded-lg bg-amber-500/20 text-amber-300 font-mono font-bold border border-amber-500/40 text-xs";
    liveIndicator.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-amber-400"></span> REPLAY (${relStr})`;
    liveIndicator.className = "px-2 py-0.5 text-[10px] font-bold rounded-full bg-amber-500/20 text-amber-400 border border-amber-500/30 flex items-center gap-1";
  }

  document.getElementById("timeline-frame-label").textContent = `Frame: ${currentFrameIndex + 1} / ${radarFrames.length}`;
}

function togglePlayPause() {
  if (isPlaying) {
    pauseReplay();
  } else {
    startReplay();
  }
}

function startReplay() {
  if (radarFrames.length === 0) return;
  isPlaying = true;
  document.getElementById("play-pause-icon").className = "fa-solid fa-pause";
  document.getElementById("play-pause-text").textContent = "Pause";

  // If at end, loop back to start
  if (currentFrameIndex >= radarFrames.length - 1) {
    currentFrameIndex = 0;
  }

  playInterval = setInterval(() => {
    let nextIndex = currentFrameIndex + 1;
    if (nextIndex >= radarFrames.length) {
      if (isLooping) {
        nextIndex = 0;
      } else {
        pauseReplay();
        return;
      }
    }
    setRadarFrame(nextIndex);
  }, playSpeed);
}

function pauseReplay() {
  isPlaying = false;
  clearInterval(playInterval);
  playInterval = null;
  document.getElementById("play-pause-icon").className = "fa-solid fa-play";
  document.getElementById("play-pause-text").textContent = "Play Loop";
}

// -------------------------------------------------------------
// LIGHTNING STREAM & CANVAS RENDERER
// -------------------------------------------------------------

async function loadLightningData() {
  try {
    const res = await fetch("/api/weather/lightning?window_mins=180");
    if (!res.ok) return;
    const data = await res.json();
    allStrikes = data.strikes || [];

    // Update Strike rate stat
    const stats = data.stats || {};
    if (stats.rate_per_min !== undefined) {
      document.getElementById("lightning-strike-rate").textContent = `${stats.rate_per_min}/min`;
    }

    if (document.getElementById("layer-lightning").checked) {
      renderLightning(getCurrentDisplayTimestamp());
    }
  } catch (err) {
    console.error("Error fetching lightning strikes:", err);
  }
}

function renderLightning(targetTimestampMs) {
  if (!document.getElementById("layer-lightning").checked) {
    lightningLayer.clearLayers();
    return;
  }

  lightningLayer.clearLayers();
  if (allStrikes.length === 0) return;

  const mapBounds = map.getBounds();
  // Window of strikes visible at this frame: up to targetTimestampMs and within past 45 mins
  const windowMs = 45 * 60 * 1000;
  const minTime = targetTimestampMs - windowMs;

  allStrikes.forEach((s) => {
    if (s.time_ms > targetTimestampMs || s.time_ms < minTime) return;

    // Check map bounds with padding
    if (s.lat < mapBounds.getSouth() - 1 || s.lat > mapBounds.getNorth() + 1 ||
        s.lon < mapBounds.getWest() - 1 || s.lon > mapBounds.getEast() + 1) {
      return;
    }

    const ageSec = Math.max(0, Math.round((targetTimestampMs - s.time_ms) / 1000));
    const ageMins = ageSec / 60.0;

    let color = "#FFE600";
    let radius = 5;
    let opacity = 0.9;
    let className = "lightning-live-pulse";

    if (ageMins < 2) {
      color = "#FFE600"; // Neon yellow
      radius = 6;
      opacity = 1.0;
    } else if (ageMins < 15) {
      color = "#FFA502"; // Amber orange
      radius = 4.5;
      opacity = 0.85;
      className = "";
    } else if (ageMins < 30) {
      color = "#FF4757"; // Red
      radius = 3.5;
      opacity = 0.70;
      className = "";
    } else {
      color = "#A55EEA"; // Purple/violet
      radius = 3.0;
      opacity = 0.55;
      className = "";
    }

    // High performance circle marker
    const marker = L.circleMarker([s.lat, s.lon], {
      radius: radius,
      fillColor: color,
      fillOpacity: opacity,
      color: "#FFFFFF",
      weight: ageMins < 2 ? 1.5 : 0.8,
      className: className
    });

    const strikeDate = new Date(s.time_ms);
    const timeStr = `${String(strikeDate.getUTCHours()).padStart(2, '0')}:${String(strikeDate.getUTCMinutes()).padStart(2, '0')}:${String(strikeDate.getUTCSeconds()).padStart(2, '0')}Z`;

    marker.bindTooltip(`⚡ Lightning Strike<br>Time: ${timeStr} (${Math.round(ageMins)}m ago)`, {
      direction: "top",
      className: "leaflet-tooltip-metar",
      offset: [0, -4]
    });

    lightningLayer.addLayer(marker);
  });
}

// -------------------------------------------------------------
// SIGMET HAZARDS & REGIONAL METARS
// -------------------------------------------------------------

async function loadSigmets() {
  if (!document.getElementById("layer-sigmets").checked) {
    sigmetsLayer.clearLayers();
    return;
  }

  try {
    const res = await fetch("/api/weather/sigmets");
    if (!res.ok) return;
    const data = await res.json();
    sigmetsLayer.clearLayers();

    const features = data.features || [];
    document.getElementById("sigmet-count-badge").textContent = `${features.length} Active`;

    const container = document.getElementById("sigmet-list-container");
    if (features.length === 0) {
      container.innerHTML = '<div class="text-gray-500 text-center py-2">No active SIGMETs in area</div>';
    } else {
      container.innerHTML = "";
    }

    features.forEach((feat) => {
      const props = feat.properties || {};
      const geom = feat.geometry;
      if (!geom || geom.type !== "Polygon") return;

      const color = props.display_color || "#FF3838";
      const label = props.display_label || "⚠️ SIGMET";
      const topAlt = props.altitudeHi1 || props.top;
      const rawText = props.rawAirSigmet || props.rawText || "";
      const validTo = props.validTimeTo || "";

      const geoLayer = L.geoJSON(feat, {
        style: {
          color: color,
          weight: 2,
          opacity: 0.85,
          fillColor: color,
          fillOpacity: 0.25
        }
      });

      const popupHtml = `
        <div class="space-y-1.5 min-w-[220px]">
          <div class="flex items-center justify-between pb-1 border-b border-gray-700">
            <span class="font-bold text-xs" style="color: ${color}">${label}</span>
            ${topAlt ? `<span class="text-[10px] font-mono bg-black/40 px-1.5 py-0.5 rounded text-gray-300">Top FL${Math.round(topAlt/100)}</span>` : ""}
          </div>
          <div class="text-[11px] text-gray-300">${props.hazard || 'Hazard'}</div>
          ${validTo ? `<div class="text-[10px] text-gray-400 font-mono">Valid until: ${validTo}</div>` : ""}
          ${rawText ? `<div class="bg-black/50 p-1.5 rounded text-[9px] font-mono text-gray-300 break-words">${rawText}</div>` : ""}
        </div>
      `;

      geoLayer.bindPopup(popupHtml);
      sigmetsLayer.addLayer(geoLayer);

      const item = document.createElement("div");
      item.className = "bg-deck-panel p-2 rounded-lg border border-deck-border/60 flex flex-col gap-1";
      item.innerHTML = `
        <div class="flex items-center justify-between font-bold" style="color: ${color}">
          <span>${label}</span>
          ${topAlt ? `<span class="text-[10px] font-mono text-gray-400">FL${Math.round(topAlt/100)}</span>` : ""}
        </div>
        <div class="text-[10px] text-gray-300 line-clamp-2">${rawText || props.hazard}</div>
      `;
      container.appendChild(item);
    });

  } catch (err) {
    console.error("Error loading SIGMETs:", err);
  }
}

async function loadRegionalMetars() {
  if (!document.getElementById("layer-metars").checked) {
    metarsLayer.clearLayers();
    return;
  }

  const bounds = map.getBounds();
  const bbox = `${bounds.getSouth().toFixed(2)},${bounds.getWest().toFixed(2)},${bounds.getNorth().toFixed(2)},${bounds.getEast().toFixed(2)}`;

  try {
    const res = await fetch(`/api/weather/regional-metars?bbox=${bbox}`);
    if (!res.ok) return;
    const data = await res.json();
    metarsLayer.clearLayers();

    const stations = data.stations || [];
    stations.forEach((stn) => {
      const cat = (stn.fltcat || "VFR").toLowerCase();
      
      const customIcon = L.divIcon({
        className: "custom-div-icon",
        html: `<div class="metar-dot ${cat}"></div>`,
        iconSize: [14, 14],
        iconAnchor: [7, 7]
      });

      const marker = L.marker([stn.lat, stn.lon], { icon: customIcon });

      const windText = stn.wspd ? `${stn.wdir || '000'}@${stn.wspd}${stn.wgst ? 'G'+stn.wgst : ''}kt` : 'Calm';
      marker.bindTooltip(`${stn.icao} • ${windText}`, {
        permanent: false,
        direction: "top",
        className: "leaflet-tooltip-metar",
        offset: [0, -7]
      });

      const catColor = cat === 'vfr' ? '#00B894' : cat === 'mvfr' ? '#0984E3' : cat === 'ifr' ? '#D63031' : '#6C5CE7';
      const popupHtml = `
        <div class="space-y-2 min-w-[240px]">
          <div class="flex items-center justify-between pb-1.5 border-b border-gray-700">
            <div class="flex items-center gap-1.5">
              <span class="px-1.5 py-0.5 text-[10px] font-bold rounded text-black uppercase" style="background-color: ${catColor}">${stn.fltcat || 'VFR'}</span>
              <span class="font-black text-sm text-white">${stn.icao}</span>
            </div>
            <span class="text-[10px] text-gray-400 font-mono">${stn.name || ''}</span>
          </div>

          <div class="grid grid-cols-2 gap-1.5 text-[11px]">
            <div class="bg-black/40 p-1.5 rounded">
              <div class="text-gray-400 text-[9px]">WIND</div>
              <div class="font-bold text-white font-mono">${stn.wdir !== null ? `${stn.wdir}° @ ${stn.wspd} kt` : 'Calm'} ${stn.wgst ? `<span class="text-amber-400">G${stn.wgst}</span>` : ''}</div>
            </div>
            <div class="bg-black/40 p-1.5 rounded">
              <div class="text-gray-400 text-[9px]">VISIBILITY</div>
              <div class="font-bold text-white font-mono">${stn.visib !== null ? `${stn.visib} SM` : '10+ SM'}</div>
            </div>
            <div class="bg-black/40 p-1.5 rounded">
              <div class="text-gray-400 text-[9px]">TEMP / DEW</div>
              <div class="font-bold text-white font-mono">${stn.temp !== null ? `${stn.temp}°C` : '--'} / ${stn.dewp !== null ? `${stn.dewp}°C` : '--'}</div>
            </div>
            <div class="bg-black/40 p-1.5 rounded">
              <div class="text-gray-400 text-[9px]">ALTIMETER</div>
              <div class="font-bold text-white font-mono">${stn.altim ? `${stn.altim} inHg` : '--'}</div>
            </div>
          </div>

          ${stn.raw ? `<div class="bg-black/60 p-2 rounded text-[10px] font-mono text-gray-300 break-all leading-tight">${stn.raw}</div>` : ""}

          <div class="pt-1 flex justify-end">
            <button onclick="selectAirportAsDest('${stn.icao}')" class="px-2.5 py-1 bg-blue-600 hover:bg-blue-500 text-white rounded text-[10px] font-bold transition">
              Set as Destination ➔
            </button>
          </div>
        </div>
      `;

      marker.bindPopup(popupHtml);
      metarsLayer.addLayer(marker);
    });

  } catch (err) {
    console.error("Error loading METARs:", err);
  }
}

window.selectAirportAsDest = function(icao) {
  document.getElementById("dest-input").value = icao;
  plotRoute();
};

// -------------------------------------------------------------
// ROUTE PLOTTER & BRIEFING
// -------------------------------------------------------------

async function plotRoute() {
  const dep = document.getElementById("dep-input").value.trim().toUpperCase() || "KPAO";
  const dest = document.getElementById("dest-input").value.trim().toUpperCase() || "";
  currentDep = dep;
  currentDest = dest || null;

  try {
    const url = dest ? `/api/route?dep=${dep}&dest=${dest}` : `/api/route?dep=${dep}`;
    const res = await fetch(url);
    if (!res.ok) return;
    const data = await res.json();

    routeLayer.clearLayers();
    ringsLayer.clearLayers();

    const depData = data.dep;
    const destData = data.dest;
    const routeInfo = data.route;

    if (document.getElementById("layer-rings").checked && depData) {
      const ringNm = [25, 50, 75, 100];
      ringNm.forEach((nm) => {
        const meters = nm * 1852.0;
        const circle = L.circle([depData.lat, depData.lon], {
          radius: meters,
          color: "#0984E3",
          weight: 1.5,
          dashArray: "4, 6",
          opacity: 0.65,
          fill: false
        });

        const badgeLat = depData.lat + (nm / 60.0);
        const textIcon = L.divIcon({
          className: "custom-ring-badge",
          html: `<div class="bg-blue-600/80 text-white text-[9px] font-mono font-bold px-1 rounded shadow">${nm} NM</div>`,
          iconSize: [36, 14],
          iconAnchor: [18, 7]
        });
        const badgeMarker = L.marker([badgeLat, depData.lon], { icon: textIcon });

        ringsLayer.addLayer(circle);
        ringsLayer.addLayer(badgeMarker);
      });
    }

    const depIcon = L.divIcon({
      className: "custom-dep-icon",
      html: `<div class="w-5 h-5 rounded-full bg-blue-600 border-2 border-white shadow-lg flex items-center justify-center text-[10px] text-white font-bold">🛫</div>`,
      iconSize: [20, 20],
      iconAnchor: [10, 10]
    });
    const depMarker = L.marker([depData.lat, depData.lon], { icon: depIcon }).bindTooltip(`DEP: ${depData.icao}`, { permanent: true, direction: "bottom" });
    routeLayer.addLayer(depMarker);

    if (destData && routeInfo) {
      const destIcon = L.divIcon({
        className: "custom-dest-icon",
        html: `<div class="w-5 h-5 rounded-full bg-indigo-600 border-2 border-white shadow-lg flex items-center justify-center text-[10px] text-white font-bold">🛬</div>`,
        iconSize: [20, 20],
        iconAnchor: [10, 10]
      });
      const destMarker = L.marker([destData.lat, destData.lon], { icon: destIcon }).bindTooltip(`DEST: ${destData.icao}`, { permanent: true, direction: "bottom" });
      routeLayer.addLayer(destMarker);

      const flightLine = L.polyline(routeInfo.coordinates, {
        color: "#E056FD",
        weight: 3.5,
        opacity: 0.9,
        dashArray: "8, 6"
      });
      routeLayer.addLayer(flightLine);

      map.fitBounds(flightLine.getBounds(), { padding: [80, 80] });

      document.getElementById("card-dep-icao").textContent = dep;
      document.getElementById("card-dest-icao").textContent = dest;
      document.getElementById("route-nm").textContent = `${routeInfo.distance_nm} NM`;
      document.getElementById("route-bearing").textContent = `${routeInfo.true_course_deg}° T`;
      document.getElementById("route-details-row").classList.remove("hidden");
    } else {
      map.setView([depData.lat, depData.lon], 9);
      document.getElementById("card-dep-icao").textContent = dep;
      document.getElementById("card-dest-icao").textContent = "---";
      document.getElementById("route-details-row").classList.add("hidden");
    }

    loadAirportBrief(dep);

  } catch (err) {
    console.error("Error plotting route:", err);
  }
}

async function loadAirportBrief(icao) {
  try {
    const aptRes = await fetch(`/api/airports/${icao}`);
    if (aptRes.ok) {
      const apt = await aptRes.json();
      document.getElementById("dep-brief-title").textContent = `${apt.icao} (${apt.name || 'Airport'})`;

      const metar = apt.metar;
      if (metar) {
        document.getElementById("dep-cat-badge").textContent = metar.category || "VFR";
        document.getElementById("dep-cat-badge").className = `px-1.5 py-0.5 text-[10px] rounded font-mono font-bold text-black ${
          metar.category === 'VFR' ? 'bg-emerald-400' :
          metar.category === 'MVFR' ? 'bg-blue-400' :
          metar.category === 'IFR' ? 'bg-red-400' : 'bg-purple-400'
        }`;

        document.getElementById("dep-metar-time").textContent = metar.obs_time || "";
        document.getElementById("dep-wind-val").textContent = metar.wind_str || "Calm";
        document.getElementById("dep-ceil-vis").textContent = `${metar.ceiling_ft ? metar.ceiling_ft + 'ft' : 'Clear'} • ${metar.visibility_str}`;
        document.getElementById("dep-altim-val").textContent = `${metar.altimeter_inhg} inHg`;
        document.getElementById("dep-da-val").textContent = `${metar.density_altitude} ft`;
        document.getElementById("dep-raw-metar").textContent = metar.raw || "No METAR string available";
      }

      const rwyContainer = document.getElementById("runway-cards-container");
      rwyContainer.innerHTML = "";

      const runways = apt.runways || [];
      if (runways.length === 0) {
        rwyContainer.innerHTML = '<div class="text-gray-500 text-center py-1">No runway geometry data</div>';
      } else {
        runways.forEach((r) => {
          const isFavorable = r.is_favorable;
          const card = document.createElement("div");
          card.className = `p-2 rounded-lg border ${
            isFavorable ? 'bg-blue-950/40 border-blue-500/60 ring-1 ring-blue-500/40' : 'bg-deck-panel border-deck-border/50'
          } flex items-center justify-between text-xs`;

          card.innerHTML = `
            <div class="flex items-center gap-2">
              <span class="font-mono font-black text-sm ${isFavorable ? 'text-blue-400' : 'text-white'}">RWY ${r.ident}</span>
              ${isFavorable ? '<span class="px-1.5 py-0.2 rounded bg-emerald-500/20 text-emerald-400 text-[9px] font-bold border border-emerald-500/30">BEST</span>' : ''}
            </div>
            <div class="text-right font-mono text-[11px]">
              <div class="${r.headwind >= 0 ? 'text-emerald-400' : 'text-amber-400'} font-semibold">
                ${r.headwind > 0 ? `▲ ${Math.round(r.headwind)}kt Head` : r.tailwind > 0 ? `▼ ${Math.round(r.tailwind)}kt Tail` : '0kt Head'}
              </div>
              <div class="text-gray-400 text-[10px]">
                Cross: <span class="text-white">${Math.round(r.crosswind || 0)}kt ${r.crosswind_side || ''}</span>
              </div>
            </div>
          `;
          rwyContainer.appendChild(card);
        });
      }
    }

    const tafRes = await fetch(`/api/weather/taf?icao=${icao}`);
    const tafContainer = document.getElementById("taf-forecast-container");
    tafContainer.innerHTML = "";

    if (tafRes.ok) {
      const taf = await tafRes.json();
      document.getElementById("taf-station-badge").textContent = taf.station_used ? `via ${taf.station_used} (${taf.distance_nm}nm)` : "";

      const decoded = taf.decoded || {};
      const forecasts = decoded.forecasts || [];

      if (forecasts.length === 0) {
        tafContainer.innerHTML = '<div class="text-gray-500 text-center py-1">No forecast periods available</div>';
      } else {
        forecasts.slice(0, 5).forEach((fc) => {
          const item = document.createElement("div");
          item.className = "bg-deck-panel p-2 rounded-lg border border-deck-border/60 space-y-1";
          item.innerHTML = `
            <div class="flex items-center justify-between font-mono text-[10px] text-gray-400 border-b border-gray-700/50 pb-1">
              <span class="font-bold text-blue-400">${fc.change_type || 'FM'}</span>
              <span>${fc.valid_from ? fc.valid_from.substring(5, 16) : ''} ➔ ${fc.valid_to ? fc.valid_to.substring(5, 16) : ''}</span>
            </div>
            <div class="flex items-center justify-between pt-0.5">
              <span class="text-white font-mono">${fc.wind_str || 'Calm'}</span>
              <span class="text-gray-300 font-mono">${fc.visibility_str || '10+ SM'}</span>
            </div>
            <div class="text-gray-400 text-[10px]">${fc.clouds_str || 'Clear'}</div>
          `;
          tafContainer.appendChild(item);
        });
      }
    } else {
      tafContainer.innerHTML = '<div class="text-gray-500 text-center py-1">No TAF issued for this station</div>';
    }

  } catch (err) {
    console.error("Error loading airport brief:", err);
  }
}

// -------------------------------------------------------------
// EVENT LISTENERS & CONTROLS SETUP
// -------------------------------------------------------------

function setupEventListeners() {
  // Layer Toggles
  document.getElementById("layer-sectional").addEventListener("change", (e) => {
    if (e.target.checked) map.addLayer(sectionalLayer);
    else map.removeLayer(sectionalLayer);
  });

  document.getElementById("layer-nexrad").addEventListener("change", (e) => {
    if (e.target.checked) {
      if (radarFramesLayer) map.addLayer(radarFramesLayer);
      else map.addLayer(nexradLayer);
    } else {
      if (radarFramesLayer) map.removeLayer(radarFramesLayer);
      map.removeLayer(nexradLayer);
    }
  });

  document.getElementById("layer-lightning").addEventListener("change", (e) => {
    if (e.target.checked) {
      document.getElementById("lightning-legend-card").classList.remove("hidden");
      renderLightning(getCurrentDisplayTimestamp());
    } else {
      document.getElementById("lightning-legend-card").classList.add("hidden");
      lightningLayer.clearLayers();
    }
  });

  document.getElementById("layer-sigmets").addEventListener("change", (e) => {
    if (e.target.checked) loadSigmets();
    else sigmetsLayer.clearLayers();
  });

  document.getElementById("layer-metars").addEventListener("change", (e) => {
    if (e.target.checked) loadRegionalMetars();
    else metarsLayer.clearLayers();
  });

  document.getElementById("layer-rings").addEventListener("change", (e) => {
    if (e.target.checked) plotRoute();
    else ringsLayer.clearLayers();
  });

  // Radar Opacity Slider
  const opacityInput = document.getElementById("radar-opacity");
  opacityInput.addEventListener("input", (e) => {
    const val = e.target.value;
    document.getElementById("radar-opacity-val").textContent = `${val}%`;
    const op = val / 100.0;
    if (nexradLayer) nexradLayer.setOpacity(op);
    if (radarFramesLayer) radarFramesLayer.setOpacity(op);
  });

  // Timeline Slider Scrubber
  const timelineSlider = document.getElementById("timeline-slider");
  timelineSlider.addEventListener("input", (e) => {
    pauseReplay();
    const idx = parseInt(e.target.value);
    setRadarFrame(idx);
  });

  // Play / Pause Button
  document.getElementById("btn-play-pause").addEventListener("click", togglePlayPause);

  // Step Buttons
  document.getElementById("btn-step-prev").addEventListener("click", () => {
    pauseReplay();
    if (currentFrameIndex > 0) {
      setRadarFrame(currentFrameIndex - 1);
    }
  });

  document.getElementById("btn-step-next").addEventListener("click", () => {
    pauseReplay();
    if (currentFrameIndex < radarFrames.length - 1) {
      setRadarFrame(currentFrameIndex + 1);
    }
  });

  // Snap Live Button
  document.getElementById("btn-snap-live").addEventListener("click", () => {
    pauseReplay();
    if (radarFrames.length > 0) {
      setRadarFrame(radarFrames.length - 1);
    }
  });

  // Loop Toggle
  const loopBtn = document.getElementById("btn-loop-toggle");
  loopBtn.addEventListener("click", () => {
    isLooping = !isLooping;
    if (isLooping) {
      loopBtn.className = "px-2.5 h-8 rounded-xl bg-deck border border-blue-500/50 text-blue-400 font-bold text-[11px] flex items-center gap-1 transition";
    } else {
      loopBtn.className = "px-2.5 h-8 rounded-xl bg-deck border border-deck-border text-gray-400 font-bold text-[11px] flex items-center gap-1 transition";
    }
  });

  // Speed Buttons (1x, 2x, 4x)
  const setSpeed = (spd, activeId) => {
    playSpeed = spd;
    document.querySelectorAll(".speed-btn").forEach((b) => {
      b.className = "px-1.5 py-0.5 rounded text-gray-400 hover:text-white font-bold text-[10px] speed-btn";
    });
    document.getElementById(activeId).className = "px-1.5 py-0.5 rounded bg-blue-600 text-white font-bold text-[10px] speed-btn active";
    if (isPlaying) {
      clearInterval(playInterval);
      startReplay();
    }
  };

  document.getElementById("speed-1x").addEventListener("click", () => setSpeed(1000, "speed-1x"));
  document.getElementById("speed-2x").addEventListener("click", () => setSpeed(500, "speed-2x"));
  document.getElementById("speed-4x").addEventListener("click", () => setSpeed(250, "speed-4x"));

  // Route Form
  document.getElementById("btn-update-route").addEventListener("click", plotRoute);
  document.getElementById("dep-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") plotRoute();
  });
  document.getElementById("dest-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") plotRoute();
  });

  document.getElementById("btn-clear-route").addEventListener("click", () => {
    document.getElementById("dest-input").value = "";
    plotRoute();
  });

  // Briefing Drawer Toggle
  const drawer = document.getElementById("briefing-drawer");
  document.getElementById("btn-toggle-drawer").addEventListener("click", () => {
    drawer.classList.toggle("translate-x-full");
  });
  document.getElementById("btn-close-drawer").addEventListener("click", () => {
    drawer.classList.add("translate-x-full");
  });

  // Sectional HD Export Button
  document.getElementById("btn-render-map").addEventListener("click", () => {
    const dep = document.getElementById("dep-input").value.trim().toUpperCase() || "KPAO";
    const dest = document.getElementById("dest-input").value.trim().toUpperCase();
    const url = dest ? `/api/map/render?dep=${dep}&dest=${dest}` : `/api/map/render?dep=${dep}`;
    window.open(url, "_blank");
  });
}

// Start application
window.addEventListener("DOMContentLoaded", initMap);
