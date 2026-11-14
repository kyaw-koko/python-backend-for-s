/**
 * ============================================================================
 * SPHEREx Real-World Epoch Difference Inspector (IRSA & Astropy)
 * ============================================================================
 * Fetches real SPHEREx epoch cutouts from IRSA TAP, computes Astropy epoch
 * differencing (Epoch B - Epoch A), and renders dipole contrast with zero-centered
 * coolwarm contrast stretching.
 */

let currentTarget = BENCHMARK_PRESETS[1]; // Barnard's Star (verified dipole)
let viewMode = 'diff'; // 'diff' | 'wipe' | 'epoch_single'
let wipeSplit = 0.5;
let currentSingleEpoch = 'b';

let diffImg = null;
let epochAImg = null;
let epochBImg = null;
let diffData = null;

// DOM Elements
const canvas = document.getElementById('diffCanvas');
const ctx = canvas.getContext('2d');
const btnViewDiff = document.getElementById('btnViewDiff');
const btnWipeSlider = document.getElementById('btnWipeSlider');
const btnToggleEpochs = document.getElementById('btnToggleEpochs');
const epochLabel = document.getElementById('epochLabel');
const wipeRow = document.getElementById('wipeRow');
const wipeSlider = document.getElementById('wipeSlider');
const wipeVal = document.getElementById('wipeVal');
const targetSelect = document.getElementById('targetSelect');
const loaderOverlay = document.getElementById('loaderOverlay');
const loaderText = document.getElementById('loaderText');

// Card Fields
const coordsBadge = document.getElementById('coordsBadge');
const dipoleBadge = document.getElementById('dipoleBadge');
const posPeakVal = document.getElementById('posPeakVal');
const negPeakVal = document.getElementById('negPeakVal');
const valContrast = document.getElementById('valContrast');
const valDeltaMJD = document.getElementById('valDeltaMJD');
const valCandidateStatus = document.getElementById('valCandidateStatus');

/**
 * Fetch real epoch differencing from FastAPI backend (/api/sky-patch?mode=diff)
 */
async function loadRealDiffData(target) {
  showLoader(true, `Querying IRSA TAP for multiple epochs of ${target.name}...`);
  diffData = null;
  diffImg = null;
  epochAImg = null;
  epochBImg = null;

  try {
    const url = `${API_BASE}/api/sky-patch?ra=${target.ra}&dec=${target.dec}&fov=${target.fov}&mode=diff&max_frames=4`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);

    const json = await res.json();

    if (json.status === 'success' && json.data && json.data.diff_frame) {
      diffData = json.data;
      loaderText.textContent = "Astropy WCS differencing complete. Decoding FITS images...";
      await loadImagesFromData(diffData);
      valCandidateStatus.textContent = "Real Astropy Dipole";
      valCandidateStatus.style.color = "#00e5ff";
    } else if (json.status === 'no_data') {
      renderNoData(target, json.message);
      return;
    } else {
      throw new Error("Invalid diff payload");
    }
  } catch (err) {
    console.error("Diff fetch failed:", err);
    renderError(err.message);
    valCandidateStatus.textContent = "Pipeline Error";
    valCandidateStatus.style.color = "#ff5252";
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

async function loadImagesFromData(d) {
  const p1 = new Promise(resolve => {
    const img = new Image();
    img.onload = () => { diffImg = img; resolve(); };
    img.onerror = () => resolve();
    img.src = d.diff_frame.data_url;
  });

  const p2 = new Promise(resolve => {
    if (!d.epoch_a || !d.epoch_a.data_url) return resolve();
    const img = new Image();
    img.onload = () => { epochAImg = img; resolve(); };
    img.onerror = () => resolve();
    img.src = d.epoch_a.data_url;
  });

  const p3 = new Promise(resolve => {
    if (!d.epoch_b || !d.epoch_b.data_url) return resolve();
    const img = new Image();
    img.onload = () => { epochBImg = img; resolve(); };
    img.onerror = () => resolve();
    img.src = d.epoch_b.data_url;
  });

  await Promise.all([p1, p2, p3]);
}

function updateTelemetry() {
  coordsBadge.textContent = `RA ${currentTarget.ra.toFixed(4)}° • DEC ${currentTarget.dec.toFixed(4)}° (TAN WCS)`;
  if (!diffData || !diffData.diff_frame) return;

  const contrast = diffData.diff_frame.dipole_contrast;
  const posPeak = diffData.diff_frame.positive_peak_mjy_sr;
  const negPeak = diffData.diff_frame.negative_peak_mjy_sr;
  const delta = diffData.delta_mjd;

  dipoleBadge.textContent = `DIPOLE CONTRAST: ${contrast.toFixed(3)} MJy/sr`;
  posPeakVal.textContent = `+${posPeak.toFixed(3)} MJy/sr`;
  negPeakVal.textContent = `${negPeak.toFixed(3)} MJy/sr`;
  valContrast.textContent = `${contrast.toFixed(3)} MJy/sr`;
  valDeltaMJD.textContent = `${delta.toFixed(2)} Days`;
}

/**
 * Main Render Function
 */
function render() {
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  if (viewMode === 'diff') {
    if (diffImg) {
      ctx.drawImage(diffImg, 0, 0, w, h);
      drawDipoleLegend(w, h);
    }
  } else if (viewMode === 'wipe') {
    renderWipeView(w, h);
  } else if (viewMode === 'epoch_single') {
    const img = currentSingleEpoch === 'a' ? epochAImg : epochBImg;
    if (img) {
      ctx.drawImage(img, 0, 0, w, h);
    }
  }

  drawWCSCompass(ctx, w - 48, h - 48, 28);
}

function drawDipoleLegend(w, h) {
  ctx.save();
  ctx.font = "12px 'Inter', sans-serif";
  ctx.fillStyle = "rgba(255, 100, 100, 0.9)";
  ctx.fillText("(+) Positive Peak: New Position (Epoch B)", 20, 36);

  ctx.fillStyle = "rgba(80, 160, 255, 0.9)";
  ctx.fillText("(-) Negative Trough: Past Position (Epoch A)", 20, 56);
  ctx.restore();
}

function renderWipeView(w, h) {
  const splitX = w * wipeSplit;

  // Left: Epoch A
  if (epochAImg) {
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, splitX, h);
    ctx.clip();
    ctx.drawImage(epochAImg, 0, 0, w, h);
    ctx.restore();
  }

  // Right: Epoch B
  if (epochBImg) {
    ctx.save();
    ctx.beginPath();
    ctx.rect(splitX, 0, w - splitX, h);
    ctx.clip();
    ctx.drawImage(epochBImg, 0, 0, w, h);
    ctx.restore();
  }

  // Divider Line
  ctx.strokeStyle = "#00e5ff";
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  ctx.moveTo(splitX, 0);
  ctx.lineTo(splitX, h);
  ctx.stroke();

  // Labels
  ctx.font = "13px 'Inter', sans-serif";
  ctx.fillStyle = "#ffffff";
  ctx.fillText("Epoch A (Past)", 20, 36);
  ctx.fillText("Epoch B (Current)", w - 140, 36);
}

function renderNoData(target, msg) {
  ctx.fillStyle = "#07090e";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.font = "16px 'Playfair Display', Georgia, serif";
  ctx.fillStyle = "#ff9100";
  ctx.fillText("No Multi-Epoch Cutouts Available for Differencing", 60, canvas.height / 2 - 10);
  ctx.font = "13px 'Inter', sans-serif";
  ctx.fillStyle = "#9ab0cf";
  ctx.fillText(msg, 60, canvas.height / 2 + 15);
}

function renderError(msg) {
  ctx.fillStyle = "#07090e";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.font = "16px 'Playfair Display', Georgia, serif";
  ctx.fillStyle = "#ff5252";
  ctx.fillText("Pipeline Error: Failed to Generate Epoch Differencing", 60, canvas.height / 2 - 10);
  ctx.font = "13px 'Inter', sans-serif";
  ctx.fillStyle = "#9ab0cf";
  ctx.fillText(msg, 60, canvas.height / 2 + 15);
}

// UI Controls
btnViewDiff.addEventListener('click', () => {
  viewMode = 'diff';
  btnViewDiff.classList.add('active');
  btnWipeSlider.classList.remove('active');
  btnToggleEpochs.classList.remove('active');
  wipeRow.style.display = 'none';
  render();
});

btnWipeSlider.addEventListener('click', () => {
  viewMode = 'wipe';
  btnWipeSlider.classList.add('active');
  btnViewDiff.classList.remove('active');
  btnToggleEpochs.classList.remove('active');
  wipeRow.style.display = 'flex';
  render();
});

btnToggleEpochs.addEventListener('click', () => {
  viewMode = 'epoch_single';
  currentSingleEpoch = currentSingleEpoch === 'a' ? 'b' : 'a';
  epochLabel.textContent = currentSingleEpoch === 'a' ? 'Show Epoch A (Past)' : 'Show Epoch B (Current)';
  btnToggleEpochs.classList.add('active');
  btnViewDiff.classList.remove('active');
  btnWipeSlider.classList.remove('active');
  wipeRow.style.display = 'none';
  render();
});

wipeSlider.addEventListener('input', (e) => {
  wipeSplit = parseInt(e.target.value, 10) / 100.0;
  wipeVal.textContent = `${Math.round(wipeSplit * 100)}% Split`;
  render();
});

targetSelect.addEventListener('change', (e) => {
  const chosen = BENCHMARK_PRESETS.find(p => p.id === e.target.value) || BENCHMARK_PRESETS[1];
  currentTarget = chosen;
  loadRealDiffData(currentTarget);
});

document.addEventListener('DOMContentLoaded', () => {
  loadRealDiffData(currentTarget);
});
