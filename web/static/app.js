/**
 * PilotBrief Aviation Web Deck — Live Sectional, Radar, Lightning & Historical Replay
 */

// State
let map;
let baseDarkLayer;
let baseSatelliteLayer;
let sectionalLayer;
let activeBasemap = "sectional"; // 'sectional', 'satellite', 'dark'

let nexradLayer;
let radarFramesLayer;
let sigmetsLayer;
let metarsLayer;
let routeLayer;
let ringsLayer;

// Custom Canvas Layer for High-Performance Lightning Rendering
let lightningCanvasLayer;
let lightningCanvas;
let lightningCtx;

let currentDep = "KRYN";
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
let lastStrikePollMs = 0;
let lightningPollInterval = null;

// Insights Telemetry State
let insightsInterval = null;
let lastPingMs = 0;

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
  // Bounding box strictly covering Contiguous US, Alaska, Hawaii, and Caribbean airspace
  const usBounds = L.latLngBounds(
    L.latLng(12.0, -179.0), // SW (covers Hawaii / Pacific approach)
    L.latLng(72.0, -55.0)   // NE (covers Alaska & Eastern US)
  );

  // Initialize map with canvas preference, strict bounds and smooth zooming
  map = L.map("map", {
    center: [32.1422, -111.1746],
    zoom: 8,
    minZoom: 4,
    maxZoom: 13,
    maxBounds: usBounds,
    maxBoundsViscosity: 1.0,
    zoomControl: false,
    preferCanvas: true,
    bounceAtZoomLimits: false
  });

  // Custom Zoom Control top-right
  L.control.zoom({ position: "topright" }).addTo(map);

  // 1. Dark Street Basemap (CartoDB Dark Matter)
  baseDarkLayer = L.tileLayer(
    "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    {
      minZoom: 4,
      maxZoom: 13,
      subdomains: "abcd",
      attribution: "&copy; OpenStreetMap &copy; CARTO"
    }
  );

  // 2. High-Res Satellite Basemap (ESRI World Imagery)
  baseSatelliteLayer = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    {
      minZoom: 4,
      maxZoom: 13,
      attribution: "Tiles &copy; Esri"
    }
  );

  // 3. FAA VFR Sectional Basemap (with native zoom fallback for smooth scaling)
  sectionalLayer = L.tileLayer(
    "https://tiles.arcgis.com/tiles/ssFJjBXIUyZDrSYZ/arcgis/rest/services/VFR_Sectional/MapServer/tile/{z}/{y}/{x}",
    {
      minZoom: 4,
      maxZoom: 13,
      minNativeZoom: 6,
      maxNativeZoom: 11,
      updateWhenIdle: true,
      updateWhenZooming: false,
      keepBuffer: 4,
      attribution: "FAA VFR Sectional &copy; ESRI / ArcGIS"
    }
  );

  // Default basemap setup: Base Dark underneath + Sectional on top
  baseDarkLayer.addTo(map);
  sectionalLayer.addTo(map);

  // 4. Interactive Radar Layer (Default to IEM Live WMS)
  nexradLayer = L.tileLayer.wms(
    "https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad/n0q.cgi",
    {
      layers: "nexrad-n0q-m05m",
      format: "image/png",
      transparent: true,
      opacity: 0.70,
      version: "1.1.1",
      zIndex: 10,
      attribution: "NEXRAD &copy; IEM / NOAA"
    }
  ).addTo(map);

  // Layer groups for vectors and markers
  sigmetsLayer = L.layerGroup().addTo(map);
  metarsLayer = L.layerGroup().addTo(map);
  ringsLayer = L.layerGroup().addTo(map);
  routeLayer = L.layerGroup().addTo(map);

  // Initialize Canvas Overlay for 60 FPS Lightning
  initLightningCanvas();

  // Map move event -> reload regional METARs & redraw canvas
  let moveTimeout;
  map.on("moveend", () => {
    clearTimeout(moveTimeout);
    moveTimeout = setTimeout(() => {
      if (document.getElementById("layer-metars").checked) {
        loadRegionalMetars();
      }
      renderLightningCanvas(getCurrentDisplayTimestamp());
    }, 350);
  });

  map.on("zoomend", () => {
    renderLightningCanvas(getCurrentDisplayTimestamp());
  });

  map.on("resize", () => {
    resizeLightningCanvas();
    renderLightningCanvas(getCurrentDisplayTimestamp());
  });

  // Setup UI Listeners
  setupEventListeners();

  // Initial Data Loads
  loadRadarFrames();
  loadLightningData(true);
  loadSigmets();
  loadRegionalMetars();
  plotRoute();

  // Auto-refresh lightning delta every 4 seconds
  lightningPollInterval = setInterval(() => loadLightningData(false), 4000);

  // Auto-refresh radar frames, sigmets, and METARs every 2 minutes
  setInterval(() => {
    loadRadarFrames();
    loadSigmets();
    loadRegionalMetars();
  }, 120000);

  // Animation pulse loop for live strikes
  requestAnimationFrame(animationPulseLoop);
}

// -------------------------------------------------------------
// BASEMAP CONTROLLER
// -------------------------------------------------------------

function setBasemap(type) {
  activeBasemap = type;
  const sectionalSlider = document.getElementById("sectional-blend-container");

  // Remove existing basemap layers
  if (map.hasLayer(baseDarkLayer)) map.removeLayer(baseDarkLayer);
  if (map.hasLayer(baseSatelliteLayer)) map.removeLayer(baseSatelliteLayer);
  if (map.hasLayer(sectionalLayer)) map.removeLayer(sectionalLayer);

  document.querySelectorAll(".basemap-opt").forEach(btn => btn.classList.remove("active"));

  if (type === "sectional") {
    document.getElementById("btn-basemap-sectional").classList.add("active");
    baseDarkLayer.addTo(map);
    sectionalLayer.setOpacity(parseFloat(document.getElementById("sectional-opacity").value) / 100.0);
    sectionalLayer.addTo(map);
    if (sectionalSlider) sectionalSlider.classList.remove("hidden");
  } else if (type === "satellite") {
    document.getElementById("btn-basemap-satellite").classList.add("active");
    baseSatelliteLayer.addTo(map);
    const op = parseFloat(document.getElementById("sectional-opacity").value) / 100.0;
    if (op > 0.05) {
      sectionalLayer.setOpacity(op);
      sectionalLayer.addTo(map);
    }
    if (sectionalSlider) sectionalSlider.classList.remove("hidden");
  } else if (type === "dark") {
    document.getElementById("btn-basemap-dark").classList.add("active");
    baseDarkLayer.addTo(map);
    const op = parseFloat(document.getElementById("sectional-opacity").value) / 100.0;
    if (op > 0.05) {
      sectionalLayer.setOpacity(op);
      sectionalLayer.addTo(map);
    }
    if (sectionalSlider) sectionalSlider.classList.remove("hidden");
  }
}

// -------------------------------------------------------------
// HIGH-PERFORMANCE CANVAS LIGHTNING RENDERER
// -------------------------------------------------------------

function initLightningCanvas() {
  const container = map.getPanes().overlayPane;
  lightningCanvas = document.createElement("canvas");
  lightningCanvas.style.position = "absolute";
  lightningCanvas.style.left = "0";
  lightningCanvas.style.top = "0";
  lightningCanvas.style.pointerEvents = "none";
  lightningCanvas.style.zIndex = "25";
  container.appendChild(lightningCanvas);
  lightningCtx = lightningCanvas.getContext("2d");
  resizeLightningCanvas();
}

function resizeLightningCanvas() {
  if (!map || !lightningCanvas) return;
  const size = map.getSize();
  lightningCanvas.width = size.x;
  lightningCanvas.height = size.y;
  const topLeft = map.containerPointToLayerPoint([0, 0]);
  L.DomUtil.setPosition(lightningCanvas, topLeft);
}

let pulsePhase = 0;
function animationPulseLoop() {
  pulsePhase = (pulsePhase + 0.04) % (Math.PI * 2);
  // Redraw if live mode is active and lightning layer is visible
  if (isLiveMode && document.getElementById("layer-lightning").checked && allStrikes.length > 0) {
    renderLightningCanvas(getCurrentDisplayTimestamp());
  }
  requestAnimationFrame(animationPulseLoop);
}

function renderLightningCanvas(targetTimestampMs) {
  if (!lightningCtx || !map) return;
  const size = map.getSize();

  // Reposition canvas relative to current map pane
  const topLeft = map.containerPointToLayerPoint([0, 0]);
  L.DomUtil.setPosition(lightningCanvas, topLeft);

  lightningCtx.clearRect(0, 0, size.x, size.y);

  if (!document.getElementById("layer-lightning").checked || allStrikes.length === 0) {
    return;
  }

  const mapBounds = map.getBounds();
  const windowMs = 45 * 60 * 1000; // 45 min visible strike window
  const minTime = targetTimestampMs - windowMs;

  const pulseScale = 1.0 + 0.35 * Math.sin(pulsePhase);
  const pulseAlpha = 0.75 + 0.25 * Math.cos(pulsePhase);

  for (let i = 0; i < allStrikes.length; i++) {
    const s = allStrikes[i];
    if (s.time_ms > targetTimestampMs || s.time_ms < minTime) continue;

    // Bounds check
    if (s.lat < mapBounds.getSouth() - 0.5 || s.lat > mapBounds.getNorth() + 0.5 ||
        s.lon < mapBounds.getWest() - 0.5 || s.lon > mapBounds.getEast() + 0.5) {
      continue;
    }

    const pt = map.latLngToContainerPoint([s.lat, s.lon]);
    if (pt.x < -20 || pt.x > size.x + 20 || pt.y < -20 || pt.y > size.y + 20) continue;

    const ageSec = Math.max(0, Math.round((targetTimestampMs - s.time_ms) / 1000));
    const ageMins = ageSec / 60.0;

    let color = "#FFE600";
    let radius = 4.5;
    let alpha = 0.9;

    if (ageMins < 2) {
      color = "#FFE600"; // Neon yellow
      radius = 5.5 * (isLiveMode ? pulseScale : 1.0);
      alpha = isLiveMode ? pulseAlpha : 1.0;

      // Draw outer glowing aura for live strikes
      lightningCtx.beginPath();
      lightningCtx.arc(pt.x, pt.y, radius * 2.2, 0, Math.PI * 2);
      lightningCtx.fillStyle = `rgba(255, 230, 0, ${0.25 * alpha})`;
      lightningCtx.fill();
    } else if (ageMins < 15) {
      color = "#FFA502"; // Amber orange
      radius = 4.0;
      alpha = 0.85;
    } else if (ageMins < 30) {
      color = "#FF4757"; // Coral red
      radius = 3.2;
      alpha = 0.70;
    } else {
      color = "#A55EEA"; // Purple/violet
      radius = 2.5;
      alpha = 0.55;
    }

    // Main strike dot
    lightningCtx.beginPath();
    lightningCtx.arc(pt.x, pt.y, radius, 0, Math.PI * 2);
    lightningCtx.fillStyle = color;
    lightningCtx.globalAlpha = alpha;
    lightningCtx.fill();

    // White core center for high contrast
    if (ageMins < 15) {
      lightningCtx.beginPath();
      lightningCtx.arc(pt.x, pt.y, radius * 0.45, 0, Math.PI * 2);
      lightningCtx.fillStyle = "#FFFFFF";
      lightningCtx.globalAlpha = 0.95;
      lightningCtx.fill();
    }
  }

  lightningCtx.globalAlpha = 1.0;
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

  document.getElementById("timeline-slider").value = index;

  const opacity = parseFloat(document.getElementById("radar-opacity").value) / 100.0;
  
  if (document.getElementById("layer-nexrad").checked) {
    if (frame.tile_url) {
      if (map.hasLayer(nexradLayer)) map.removeLayer(nexradLayer);
      
      if (!radarFramesLayer) {
        radarFramesLayer = L.tileLayer(frame.tile_url, {
          opacity: opacity,
          maxZoom: 12,
          minZoom: 4,
          zIndex: 10
        }).addTo(map);
      } else {
        radarFramesLayer.setUrl(frame.tile_url);
        if (!map.hasLayer(radarFramesLayer)) map.addLayer(radarFramesLayer);
      }
    } else if (frame.wms_url) {
      if (radarFramesLayer) map.removeLayer(radarFramesLayer);
      if (!map.hasLayer(nexradLayer)) map.addLayer(nexradLayer);
      nexradLayer.setParams({ LAYERS: frame.path || "nexrad-n0q-m05m" });
    }
  }

  isLiveMode = (index === radarFrames.length - 1);
  updateTimelineUI();

  // Render lightning strikes corresponding to this time window
  renderLightningCanvas(frame.time_ms);
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
// LIGHTNING STREAM & DELTA INGESTION
// -------------------------------------------------------------

async function loadLightningData(isInitial = false) {
  try {
    let url = "/api/weather/lightning";
    if (isInitial || lastStrikePollMs === 0) {
      url += "?window_mins=180";
    } else {
      url += `?since_ms=${lastStrikePollMs}`;
    }

    const res = await fetch(url);
    if (!res.ok) return;
    const data = await res.json();
    const newStrikes = data.strikes || [];

    if (isInitial || lastStrikePollMs === 0) {
      allStrikes = newStrikes;
    } else if (newStrikes.length > 0) {
      // Merge delta strikes without duplicate time_ms
      const existingTimes = new Set(allStrikes.slice(-300).map(s => s.time_ms));
      for (const s of newStrikes) {
        if (!existingTimes.has(s.time_ms)) {
          allStrikes.push(s);
        }
      }
      // Prune strikes older than 3.5 hours
      const cutoff = Date.now() - (3.5 * 3600 * 1000);
      if (allStrikes.length > 0 && allStrikes[0].time_ms < cutoff) {
        allStrikes = allStrikes.filter(s => s.time_ms >= cutoff);
      }
    }

    if (data.now_ms) {
      lastStrikePollMs = data.now_ms - 2000;
    }

    const stats = data.stats || {};
    if (stats.rate_per_min !== undefined) {
      document.getElementById("lightning-strike-rate").textContent = `${stats.rate_per_min}/min`;
    }

    renderLightningCanvas(getCurrentDisplayTimestamp());
  } catch (err) {
    console.error("Error fetching lightning strikes:", err);
  }
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
  const dep = document.getElementById("dep-input").value.trim().toUpperCase() || "KRYN";
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
// SERVER DEBUG INSIGHTS & TELEMETRY CONTROLLER
// -------------------------------------------------------------

async function updateTelemetryInsights() {
  const modal = document.getElementById("insights-modal");
  if (modal.classList.contains("hidden")) return;

  const t0 = performance.now();
  try {
    const res = await fetch("/api/system/insights");
    const t1 = performance.now();
    lastPingMs = Math.round(t1 - t0);
    document.getElementById("telemetry-latency").textContent = `${lastPingMs} ms ping`;

    if (!res.ok) return;
    const data = await res.json();

    // RAM
    const mem = data.memory || {};
    const usedMb = mem.used_ram_mb || 0;
    const totalMb = mem.total_ram_mb || 0;
    const pct = mem.used_percent || (totalMb ? Math.round((usedMb / totalMb) * 100) : 0);
    document.getElementById("telemetry-ram-text").textContent = `${(usedMb / 1024).toFixed(1)} / ${(totalMb / 1024).toFixed(1)} GB (${pct}%)`;
    const ramBar = document.getElementById("telemetry-ram-bar");
    ramBar.style.width = `${pct}%`;
    ramBar.className = `h-1.5 rounded-full telemetry-bar-fill ${pct > 85 ? 'bg-red-500' : pct > 70 ? 'bg-amber-500' : 'bg-emerald-500'}`;

    // Lightning Stream
    const lgt = data.lightning || {};
    const wsStatus = document.getElementById("telemetry-ws-status");
    if (lgt.connected) {
      wsStatus.textContent = "STREAMING";
      wsStatus.className = "px-1.5 py-0.2 rounded bg-emerald-500/20 text-emerald-400 font-bold border border-emerald-500/30";
    } else {
      wsStatus.textContent = "RECONNECTING";
      wsStatus.className = "px-1.5 py-0.2 rounded bg-amber-500/20 text-amber-400 font-bold border border-amber-500/30";
    }
    document.getElementById("telemetry-strike-rate").textContent = lgt.rate_per_min || 0;
    document.getElementById("telemetry-strike-buffer").textContent = `${lgt.total_buffered || 0} strikes`;
    document.getElementById("telemetry-ws-host").textContent = lgt.active_host || "Auto-Failover";

    // Caches & Requests
    const reqs = data.requests || {};
    document.getElementById("telemetry-req-rate").textContent = reqs.requests_per_min || 0;
    document.getElementById("telemetry-req-total").textContent = reqs.total_served || 0;

    const caches = data.caches || {};
    document.getElementById("telemetry-cache-metars").textContent = caches.regional_metars_count || 0;
    document.getElementById("telemetry-cache-sigmets").textContent = caches.sigmets_count || 0;

    // Server Info
    const srv = data.server || {};
    document.getElementById("telemetry-uptime").textContent = srv.uptime_formatted || "--";
    document.getElementById("telemetry-py-ver").textContent = `Python ${srv.python_version || ''}`;

  } catch (err) {
    console.error("Error updating telemetry:", err);
  }
}

// -------------------------------------------------------------
// EVENT LISTENERS & CONTROLS SETUP
// -------------------------------------------------------------

function setupEventListeners() {
  // Basemap Switchers
  document.getElementById("btn-basemap-sectional").addEventListener("click", () => setBasemap("sectional"));
  document.getElementById("btn-basemap-satellite").addEventListener("click", () => setBasemap("satellite"));
  document.getElementById("btn-basemap-dark").addEventListener("click", () => setBasemap("dark"));

  // Sectional Opacity Slider
  const sectOpacityInput = document.getElementById("sectional-opacity");
  sectOpacityInput.addEventListener("input", (e) => {
    const val = e.target.value;
    document.getElementById("sectional-opacity-val").textContent = `${val}%`;
    const op = val / 100.0;
    if (sectionalLayer) {
      sectionalLayer.setOpacity(op);
      if (op <= 0.05) {
        if (map.hasLayer(sectionalLayer)) map.removeLayer(sectionalLayer);
      } else {
        if (!map.hasLayer(sectionalLayer)) map.addLayer(sectionalLayer);
      }
    }
  });

  // Layer Toggles
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
      renderLightningCanvas(getCurrentDisplayTimestamp());
    } else {
      document.getElementById("lightning-legend-card").classList.add("hidden");
      if (lightningCtx && map) {
        const size = map.getSize();
        lightningCtx.clearRect(0, 0, size.x, size.y);
      }
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

  // Server Debug Insights Toggle
  const insightsModal = document.getElementById("insights-modal");
  const insightsBtn = document.getElementById("btn-toggle-insights");
  const closeInsightsBtn = document.getElementById("btn-close-insights");

  insightsBtn.addEventListener("click", () => {
    const isHidden = insightsModal.classList.toggle("hidden");
    if (!isHidden) {
      updateTelemetryInsights();
      if (!insightsInterval) insightsInterval = setInterval(updateTelemetryInsights, 2500);
    } else {
      if (insightsInterval) {
        clearInterval(insightsInterval);
        insightsInterval = null;
      }
    }
  });

  closeInsightsBtn.addEventListener("click", () => {
    insightsModal.classList.add("hidden");
    if (insightsInterval) {
      clearInterval(insightsInterval);
      insightsInterval = null;
    }
  });

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
    const dep = document.getElementById("dep-input").value.trim().toUpperCase() || "KRYN";
    const dest = document.getElementById("dest-input").value.trim().toUpperCase();
    const url = dest ? `/api/map/render?dep=${dep}&dest=${dest}` : `/api/map/render?dep=${dep}`;
    window.open(url, "_blank");
  });
}

// Start application
window.addEventListener("DOMContentLoaded", initMap);
