/**
 * ============================================================================
 * SPHEREx Real-World 102-Band Spectrophotometry SED Viewer (Astropy & Photutils)
 * ============================================================================
 * Fetches real aperture photometry across all SPHEREx MEF cutouts from IRSA,
 * converts surface brightness (MJy/sr) to flux density (uJy) via pixel solid angle,
 * and extracts photometric uncertainties from HDU 3 (VARIANCE).
 */

let currentTarget = BENCHMARK_PRESETS[1]; // Barnard's Star
let spectrum102 = [];
let summaryData = null;
let useLogScale = true;
let showMolecules = true;
let filterCleanOnly = false;
let hoveredPoint = null;

// DOM Elements
const canvas = document.getElementById('spectrumCanvas');
const ctx = canvas.getContext('2d');
const btnToggleLog = document.getElementById('btnToggleLog');
const btnToggleMolecules = document.getElementById('btnToggleMolecules');
const btnFilterClean = document.getElementById('btnFilterClean');
const logLabel = document.getElementById('logLabel');
const channelBanner = document.getElementById('channelBanner');
const targetSelect = document.getElementById('targetSelect');
const loaderOverlay = document.getElementById('loaderOverlay');
const loaderText = document.getElementById('loaderText');

// Card Elements
const coordsBadge = document.getElementById('coordsBadge');
const channelBadge = document.getElementById('channelBadge');
const valChannels = document.getElementById('valChannels');
const valPeakFlux = document.getElementById('valPeakFlux');
const valMeanSNR = document.getElementById('valMeanSNR');

// Detector boundaries (0.75 - 5.00 um)
const DETECTOR_BANDS = [
  { name: "D1", wMin: 0.75, wMax: 1.09, color: "rgba(68, 138, 255, 0.08)", border: "#448aff" },
  { name: "D2", wMin: 1.10, wMax: 1.62, color: "rgba(0, 229, 255, 0.08)", border: "#00e5ff" },
  { name: "D3", wMin: 1.63, wMax: 2.41, color: "rgba(0, 230, 118, 0.08)", border: "#00e676" },
  { name: "D4", wMin: 2.42, wMax: 3.82, color: "rgba(255, 235, 59, 0.08)", border: "#ffeb3b" },
  { name: "D5", wMin: 3.83, wMax: 4.41, color: "rgba(255, 145, 0, 0.08)", border: "#ff9100" },
  { name: "D6", wMin: 4.42, wMax: 5.00, color: "rgba(255, 82, 82, 0.08)", border: "#ff5252" }
];

// Molecular lines
const MOLECULES = [
  { name: "CH₄", wave: 1.66 },
  { name: "H₂O Vapor", wave: 1.90 },
  { name: "CH₄", wave: 2.30 },
  { name: "H₂O Ice", wave: 3.05 },
  { name: "CO₂", wave: 4.27 },
  { name: "CO", wave: 4.67 }
];

/**
 * Fetch real spectrophotometry from backend (/api/spectrum)
 */
async function loadRealSpectrumData(target) {
  showLoader(true, `Querying IRSA cutouts & extracting photometry for ${target.name}...`);
  spectrum102 = [];
  summaryData = null;

  try {
    const url = `${API_BASE}/api/spectrum?ra=${target.ra}&dec=${target.dec}&fov=${target.fov}&aperture_radius_arcsec=6.2&max_cutouts=40`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);

    const json = await res.json();

    if (json.status === 'success' && json.spectrum_102_band) {
      spectrum102 = json.spectrum_102_band;
      summaryData = json.summary;
    } else if (json.status === 'no_data') {
      renderNoData(target, json.message);
      return;
    }
  } catch (err) {
    console.error("Spectrum extraction error:", err);
    renderError(err.message);
  } finally {
    showLoader(false);
    updateTelemetry();
    render();
  }
}

function showLoader(show, text = "") {
  if (loaderOverlay) {
    loaderOverlay.classList.toggle('active', show);
    if (text) loaderText.textContent = text;
  }
}

function updateTelemetry() {
  coordsBadge.textContent = `TARGET: ${currentTarget.name.toUpperCase()} (0.75 - 5.00 μm)`;
  const valid = spectrum102.filter(p => p.flux_uJy !== null && p.flux_uJy > 0);
  valChannels.textContent = `${valid.length} / ${spectrum102.length}`;

  const peak = valid.length > 0 ? Math.max(...valid.map(p => p.flux_uJy)) : 0;
  valPeakFlux.textContent = `${peak.toFixed(1)} μJy`;

  const meanSnr = valid.length > 0 ? (valid.reduce((s, p) => s + (p.snr || 0), 0) / valid.length) : 0;
  valMeanSNR.textContent = meanSnr.toFixed(1);
}

/**
 * Main Render Function
 */
function render() {
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  const margin = { top: 40, right: 35, bottom: 65, left: 75 };
  const plotW = w - margin.left - margin.right;
  const plotH = h - margin.top - margin.bottom;

  // Background
  ctx.fillStyle = "#07090e";
  ctx.fillRect(margin.left, margin.top, plotW, plotH);

  const minWave = 0.75;
  const maxWave = 5.00;

  function toX(wave) {
    return margin.left + ((wave - minWave) / (maxWave - minWave)) * plotW;
  }

  // 1. Shaded Detector Bands
  DETECTOR_BANDS.forEach(b => {
    const x1 = toX(b.wMin);
    const x2 = toX(b.wMax);
    ctx.fillStyle = b.color;
    ctx.fillRect(x1, margin.top, x2 - x1, plotH);

    ctx.strokeStyle = "rgba(111, 140, 184, 0.25)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x2, margin.top);
    ctx.lineTo(x2, margin.top + plotH);
    ctx.stroke();

    ctx.font = "11px 'Inter', sans-serif";
    ctx.fillStyle = b.border;
    ctx.fillText(b.name, x1 + (x2 - x1) / 2 - 8, margin.top + 16);
  });

  let validPoints = spectrum102.filter(p => p.flux_uJy !== null && p.flux_uJy > 0);
  if (filterCleanOnly) {
    validPoints = validPoints.filter(p => !p.flag_labels || p.flag_labels.includes("CLEAN") || p.flag_labels.includes("SOURCE_DETECTED"));
  }

  if (validPoints.length === 0) {
    ctx.font = "14px 'Inter', sans-serif";
    ctx.fillStyle = "#9ab0cf";
    ctx.fillText("No active exposures in this sky region have been observed in these bands yet.", margin.left + 40, h / 2);
    drawAxes(margin, plotW, plotH, 1, 100);
    return;
  }

  const minFlux = Math.max(0.1, Math.min(...validPoints.map(p => p.flux_uJy - (p.flux_err_uJy || 0))));
  const maxFlux = Math.max(...validPoints.map(p => p.flux_uJy + (p.flux_err_uJy || 0))) * 1.2;

  function toY(flux) {
    const f = Math.max(0.1, flux);
    if (useLogScale) {
      const logMin = Math.log10(minFlux);
      const logMax = Math.log10(maxFlux);
      const logF = Math.log10(f);
      return margin.top + plotH - ((logF - logMin) / (logMax - logMin)) * plotH;
    } else {
      return margin.top + plotH - ((f - minFlux) / (maxFlux - minFlux)) * plotH;
    }
  }

  // 2. Molecular Absorption Reference Lines
  if (showMolecules) {
    MOLECULES.forEach(m => {
      const mx = toX(m.wave);
      ctx.strokeStyle = "rgba(255, 64, 129, 0.45)";
      ctx.setLineDash([3, 4]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(mx, margin.top + 24);
      ctx.lineTo(mx, margin.top + plotH);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.font = "10px 'Inter', sans-serif";
      ctx.fillStyle = "rgba(255, 128, 171, 0.85)";
      ctx.fillText(m.name, mx - 14, margin.top + 34);
    });
  }

  // 3. Connect valid points with line
  ctx.beginPath();
  ctx.strokeStyle = "#00e5ff";
  ctx.lineWidth = 2.0;
  validPoints.forEach((pt, idx) => {
    const x = toX(pt.wavelength_um);
    const y = toY(pt.flux_uJy);
    if (idx === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // 4. Data Points & $1\sigma$ Error Bars from VARIANCE
  validPoints.forEach(pt => {
    const x = toX(pt.wavelength_um);
    const y = toY(pt.flux_uJy);
    const err = pt.flux_err_uJy || 0;
    const yTop = toY(pt.flux_uJy + err);
    const yBot = toY(pt.flux_uJy - err);

    // Error Bar
    ctx.strokeStyle = "rgba(0, 229, 255, 0.45)";
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(x, yTop);
    ctx.lineTo(x, yBot);
    ctx.stroke();

    // Node
    ctx.beginPath();
    ctx.arc(x, y, pt === hoveredPoint ? 5 : 2.5, 0, Math.PI * 2);
    ctx.fillStyle = pt === hoveredPoint ? "#ffffff" : "#00e5ff";
    ctx.fill();
  });

  drawAxes(margin, plotW, plotH, minFlux, maxFlux);
}

function drawAxes(margin, plotW, plotH, minFlux, maxFlux) {
  const w = canvas.width;
  const h = canvas.height;

  ctx.strokeStyle = "rgba(111, 140, 184, 0.35)";
  ctx.lineWidth = 1;
  ctx.strokeRect(margin.left, margin.top, plotW, plotH);

  // X-Ticks
  ctx.font = "11px 'Inter', sans-serif";
  ctx.fillStyle = "#9ab0cf";
  for (let wStep = 1.0; wStep <= 5.0; wStep += 0.5) {
    const tx = margin.left + ((wStep - 0.75) / (5.00 - 0.75)) * plotW;
    ctx.beginPath();
    ctx.moveTo(tx, margin.top + plotH);
    ctx.lineTo(tx, margin.top + plotH + 5);
    ctx.stroke();
    ctx.fillText(`${wStep.toFixed(1)} μm`, tx - 14, margin.top + plotH + 20);
  }

  // Titles
  ctx.font = "12px 'Inter', sans-serif";
  ctx.fillStyle = "#eef3fc";
  ctx.fillText("Wavelength (μm) [SPHEREx 0.75 - 5.00 μm All-Sky Spectrophotometry]", margin.left + plotW / 2 - 160, h - 16);
}

function renderNoData(target, msg) {
  ctx.fillStyle = "#07090e";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.font = "16px 'Playfair Display', Georgia, serif";
  ctx.fillStyle = "#ff9100";
  ctx.fillText("No FITS Cutouts in IRSA for Photometry Extraction", 60, canvas.height / 2 - 10);
  ctx.font = "13px 'Inter', sans-serif";
  ctx.fillStyle = "#9ab0cf";
  ctx.fillText(msg, 60, canvas.height / 2 + 15);
}

function renderError(msg) {
  ctx.fillStyle = "#07090e";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.font = "16px 'Playfair Display', Georgia, serif";
  ctx.fillStyle = "#ff5252";
  ctx.fillText("Pipeline Error: Failed to Extract Photometry", 60, canvas.height / 2 - 10);
  ctx.font = "13px 'Inter', sans-serif";
  ctx.fillStyle = "#9ab0cf";
  ctx.fillText(msg, 60, canvas.height / 2 + 15);
}

// UI Controls
btnToggleLog.addEventListener('click', () => {
  useLogScale = !useLogScale;
  logLabel.textContent = useLogScale ? "Logarithmic Flux Scale" : "Linear Flux Scale";
  btnToggleLog.classList.toggle('active', useLogScale);
  render();
});

btnToggleMolecules.addEventListener('click', () => {
  showMolecules = !showMolecules;
  btnToggleMolecules.classList.toggle('active', showMolecules);
  render();
});

btnFilterClean.addEventListener('click', () => {
  filterCleanOnly = !filterCleanOnly;
  btnFilterClean.classList.toggle('active', filterCleanOnly);
  render();
});

targetSelect.addEventListener('change', (e) => {
  const chosen = BENCHMARK_PRESETS.find(p => p.id === e.target.value) || BENCHMARK_PRESETS[1];
  currentTarget = chosen;
  loadRealSpectrumData(currentTarget);
});

document.addEventListener('DOMContentLoaded', () => {
  loadRealSpectrumData(currentTarget);
});
