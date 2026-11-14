"""
Planet X and SPHEREx - Production REST API Backend.
FastAPI Application serving astronomical data processing pipelines:
1. Benchmark targets presets: /api/targets/presets
2. Sky patch processing (epochs blinker, coadd, diff): /api/sky-patch
3. On-the-fly 102-band spectrophotometry: /api/spectrum
4. Health check: /api/health
"""

import logging
import os
import pickle
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import uvicorn
from astropy.coordinates import SkyCoord
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from catalog_overlay import cross_match_catalogs
from image_processor import (
    build_reference_wcs,
    process_coadd_mode,
    process_diff_mode,
    process_epochs_mode,
)
from irsa_client import fetch_cutouts_parallel, normalize_detector_name, query_spherex_tap

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("spherex.api")

app = FastAPI(
    title="Planet X and SPHEREx Astronomical Backend Engine",
    description="Headless data-processing engine and REST API for SPHEREx MEF FITS processing, blinking animations, coadds, diffs, 102-band spectrophotometry, and multi-mission catalog overlays.",
    version="1.0.0",
)

# Enable CORS for all frontends (HTML5, Canvas, WebGL, React, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Persistent Disk & Fast In-Memory Cutout Cache
# ---------------------------------------------------------------------------
DISK_CACHE_DIR = Path(".cache_spherex")
DISK_CACHE_DIR.mkdir(exist_ok=True)

CUTOUT_CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_MAX_ENTRIES = 50


def get_cache_key(ra: float, dec: float, fov: float, detector: Optional[str]) -> str:
    norm_det = normalize_detector_name(detector) or "ALL"
    return f"{ra:.5f}_{dec:.5f}_{fov:.4f}_{norm_det}"


def get_cached_cutouts(cache_key: str) -> Optional[List[Dict[str, Any]]]:
    # 1. Fast in-memory check
    entry = CUTOUT_CACHE.get(cache_key)
    if entry and (time.time() - entry["timestamp"] < 86400):  # 24 hour TTL
        return entry["cutouts"]

    # 2. Persistent disk check
    disk_file = DISK_CACHE_DIR / f"cutouts_{cache_key}.pkl"
    if disk_file.exists():
        try:
            with open(disk_file, "rb") as f:
                disk_data = pickle.load(f)
            CUTOUT_CACHE[cache_key] = disk_data
            logger.info(f"Loaded persistent disk cutouts for {cache_key}")
            return disk_data["cutouts"]
        except Exception as e:
            logger.warning(f"Error reading disk cache {disk_file}: {e}")

    return None


def store_cached_cutouts(cache_key: str, cutouts: List[Dict[str, Any]]):
    if len(CUTOUT_CACHE) >= CACHE_MAX_ENTRIES:
        # Evict oldest entry
        oldest_key = min(CUTOUT_CACHE.keys(), key=lambda k: CUTOUT_CACHE[k]["timestamp"])
        del CUTOUT_CACHE[oldest_key]
    
    data = {
        "timestamp": time.time(),
        "cutouts": cutouts,
    }
    CUTOUT_CACHE[cache_key] = data

    # Persist to local disk so restarts don't lose data
    disk_file = DISK_CACHE_DIR / f"cutouts_{cache_key}.pkl"
    try:
        with open(disk_file, "wb") as f:
            pickle.dump(data, f)
    except Exception as e:
        logger.warning(f"Failed writing disk cutouts cache {disk_file}: {e}")


# ---------------------------------------------------------------------------
# 5 Official Benchmark Targets Reference
# ---------------------------------------------------------------------------
BENCHMARK_TARGETS = [
    {
        "id": "barnards-star",
        "name": "Barnard's Star",
        "ra": 269.452075,
        "dec": 4.693391,
        "recommended_fov": 0.05,
        "target_type": "High Proper-Motion Star",
        "description": "Highest known proper-motion star (10.3 arcsec/yr). Superb for verifying epoch difference dipoles and blinking.",
        "optimal_detector": "SPHEREx-D1",
        "proper_motion": {
            "pmra_mas_yr": -800.2,
            "pmdec_mas_yr": 10362.5,
            "total_pm_mas_yr": 10393.4,
        },
    },
    {
        "id": "wise-0855",
        "name": "WISE 0855-0714",
        "ra": 133.795129,
        "dec": -7.245147,
        "recommended_fov": 0.04,
        "target_type": "Coldest Sub-stellar Brown Dwarf",
        "description": "Fourth closest system to the Sun. Sub-zero Celsius temperature (250 K), prominent in SPHEREx Band 5 & 6.",
        "optimal_detector": "SPHEREx-D6",
        "proper_motion": {
            "pmra_mas_yr": -8132.0,
            "pmdec_mas_yr": 542.0,
            "total_pm_mas_yr": 8150.0,
        },
    },
    {
        "id": "eps-indi-b",
        "name": "Epsilon Indi B",
        "ra": 331.068125,
        "dec": -56.782806,
        "recommended_fov": 0.05,
        "target_type": "Binary Brown Dwarf Pair",
        "description": "Benchmark T1+T6 brown dwarf binary pair at 3.6 pc with deep water and methane absorption bands across 1-5 um.",
        "optimal_detector": "SPHEREx-D3",
        "proper_motion": {
            "pmra_mas_yr": 3961.4,
            "pmdec_mas_yr": -2538.3,
            "total_pm_mas_yr": 4705.0,
        },
    },
    {
        "id": "rw-aurigae",
        "name": "RW Aurigae",
        "ra": 76.956542,
        "dec": 30.401417,
        "recommended_fov": 0.05,
        "target_type": "T Tauri Variable Star",
        "description": "Young eruptive variable star system surrounded by a circumstellar dust disk undergoing periodic dimming events.",
        "optimal_detector": "SPHEREx-D2",
        "proper_motion": {
            "pmra_mas_yr": 1.2,
            "pmdec_mas_yr": -21.4,
            "total_pm_mas_yr": 21.4,
        },
    },
    {
        "id": "r-draconis",
        "name": "R Draconis",
        "ra": 248.108875,
        "dec": 66.754167,
        "recommended_fov": 0.05,
        "target_type": "Mira-type Pulsating Variable Star",
        "description": "Long-period oxygen-rich AGB pulsating giant star located near the North Ecliptic Pole with frequent coverage.",
        "optimal_detector": "SPHEREx-D4",
        "proper_motion": {
            "pmra_mas_yr": 8.7,
            "pmdec_mas_yr": 12.1,
            "total_pm_mas_yr": 14.9,
        },
    },
]


@app.get("/")
def api_root():
    """Root endpoint detailing API capabilities."""
    return {
        "engine": "Planet X and SPHEREx Backend Engine",
        "version": "1.0.0",
        "status": "online",
        "endpoints": {
            "presets": "/api/targets/presets",
            "sky_patch": "/api/sky-patch?ra={deg}&dec={deg}&fov={deg}&mode=epochs|coadd|diff",
            "spectrum": "/api/spectrum?ra={deg}&dec={deg}&fov={deg}",
            "health": "/api/health",
        },
        "mission_bands": [
            {"detector": "SPHEREx-D1", "wavelength_range_um": "0.75 - 1.09", "resolving_power_R": 39},
            {"detector": "SPHEREx-D2", "wavelength_range_um": "1.10 - 1.62", "resolving_power_R": 41},
            {"detector": "SPHEREx-D3", "wavelength_range_um": "1.63 - 2.41", "resolving_power_R": 41},
            {"detector": "SPHEREx-D4", "wavelength_range_um": "2.42 - 3.82", "resolving_power_R": 35},
            {"detector": "SPHEREx-D5", "wavelength_range_um": "3.83 - 4.41", "resolving_power_R": 112},
            {"detector": "SPHEREx-D6", "wavelength_range_um": "4.42 - 5.00", "resolving_power_R": 128},
        ],
    }


@app.get("/api/health")
def health_check():
    """System health check and cache stats."""
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "cached_targets": len(CUTOUT_CACHE),
        "irsa_tap_url": "https://irsa.ipac.caltech.edu/TAP",
    }


@app.get("/api/targets/presets")
def get_presets():
    """
    Return predefined coordinates, descriptions, and proper-motion benchmarks
    for the 5 official targets.
    """
    return {
        "count": len(BENCHMARK_TARGETS),
        "targets": BENCHMARK_TARGETS,
    }


@app.get("/api/sky-patch")
def get_sky_patch(
    ra: float = Query(..., description="Target Right Ascension in degrees (0 to 360)"),
    dec: float = Query(..., description="Target Declination in degrees (-90 to 90)"),
    fov: float = Query(0.05, ge=0.005, le=0.5, description="Field of View in degrees"),
    detector: Optional[str] = Query(None, description="Optional detector array filter (SPHEREx-D1 to D6)"),
    mode: Literal["epochs", "coadd", "diff"] = Query("epochs", description="Processing mode: epochs, coadd, or diff"),
    colormap: str = Query("viridis", description="Matplotlib colormap (viridis, plasma, magma, coolwarm, gray)"),
    stretch: str = Query("asinh", description="Contrast stretch (asinh or linear)"),
    max_frames: int = Query(15, ge=1, le=100, description="Maximum number of cutouts to process"),
    include_catalogs: bool = Query(True, description="Whether to include Gaia DR3 & AllWISE catalog overlays"),
):
    """
    Fetch and process SPHEREx cutouts on a common North-Up tangential (TAN) WCS grid.
    Returns Base64-encoded PNG frames, MJD timestamps, wavelengths, and catalog overlays.
    """
    start_time = time.time()
    norm_detector = normalize_detector_name(detector)
    target_coord = SkyCoord(ra=ra, dec=dec, unit="deg", frame="icrs")

    # Check Cache
    cache_key = get_cache_key(ra, dec, fov, norm_detector)
    cached = get_cached_cutouts(cache_key)

    if cached is not None and len(cached) >= 2:
        logger.info(f"Using cached cutouts for {cache_key} ({len(cached)} items)")
        cutouts = cached[:max_frames]
        total_found = len(cached)
    else:
        # Query TAP
        try:
            tap_rows = query_spherex_tap(ra, dec, fov_deg=fov, detector=norm_detector, limit=max_frames * 2)
            total_found = len(tap_rows)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"IRSA TAP query failed: {exc}")

        if not tap_rows:
            return {
                "status": "no_data",
                "message": f"No SPHEREx cutouts found at RA={ra:.5f}, Dec={dec:.5f} with FOV={fov:.4f}.",
                "target": {"ra": ra, "dec": dec, "fov": fov, "detector": norm_detector, "mode": mode},
                "frames": [],
                "catalog_overlay": None,
            }

        # Parallel Fetch
        cutouts = fetch_cutouts_parallel(
            tap_rows,
            target_coord=target_coord,
            max_workers=8,
            remove_zodi=True,
            limit=max_frames,
        )

        if not cutouts:
            raise HTTPException(status_code=504, detail="Failed to download/parse cutouts from IRSA IBE.")

        store_cached_cutouts(cache_key, cutouts)

    # Build reference North-Up TAN WCS grid
    ref_wcs, shape_out = build_reference_wcs(ra, dec, fov_deg=fov, pixel_scale_arcsec=6.2)

    # Process requested mode
    processed_payload: Dict[str, Any] = {}
    if mode == "epochs":
        frames = process_epochs_mode(cutouts, ref_wcs, shape_out, colormap=colormap, stretch=stretch)
        processed_payload = {"frames": frames, "frame_count": len(frames)}
    elif mode == "coadd":
        coadd_result = process_coadd_mode(cutouts, ref_wcs, shape_out, colormap=colormap, stretch=stretch)
        processed_payload = coadd_result
    elif mode == "diff":
        if len(cutouts) < 2:
            raise HTTPException(status_code=400, detail="Epoch diff mode requires at least 2 distinct exposures.")
        diff_colormap = "coolwarm" if colormap == "viridis" else colormap
        diff_result = process_diff_mode(cutouts, ref_wcs, shape_out, colormap=diff_colormap, stretch=stretch)
        processed_payload = diff_result

    # Catalog Overlays (Gaia DR3 + AllWISE)
    catalog_data = None
    if include_catalogs:
        try:
            catalog_data = cross_match_catalogs(ra, dec, fov, ref_wcs, shape_out, pixel_scale_arcsec=6.2)
        except Exception as cat_err:
            logger.warning(f"Catalog cross-match warning: {cat_err}")
            catalog_data = {"error": str(cat_err), "gaia_sources": [], "allwise_sources": []}

    elapsed = round(time.time() - start_time, 2)

    return {
        "status": "success",
        "mode": mode,
        "target": {
            "ra": ra,
            "dec": dec,
            "fov": fov,
            "detector": norm_detector or "SPHEREx-All",
        },
        "wcs_grid": {
            "width": shape_out[1],
            "height": shape_out[0],
            "pixel_scale_arcsec": 6.2,
            "projection": "TAN",
            "orientation": "North-Up East-Left",
        },
        "data": processed_payload,
        "catalog_overlay": catalog_data,
        "metrics": {
            "total_cutouts_in_irsa": total_found,
            "cutouts_processed": len(cutouts),
            "execution_time_seconds": elapsed,
        },
    }


@app.get("/api/spectrum")
def get_spectrum(
    ra: float = Query(..., description="Target Right Ascension in degrees"),
    dec: float = Query(..., description="Target Declination in degrees"),
    fov: float = Query(0.05, ge=0.005, le=0.2, description="Cutout size in degrees for photometry"),
    detector: Optional[str] = Query(None, description="Optional detector array filter"),
    aperture_radius_arcsec: float = Query(6.2, ge=2.0, le=30.0, description="Aperture radius in arcseconds"),
    max_cutouts: int = Query(60, ge=1, le=200, description="Maximum cutouts to sample for spectral extraction"),
):
    """
    Extract on-the-fly 102-band spectrophotometric spectral flux array.
    Converts MJy/sr to uJy flux density with error propagation from VARIANCE
    and contamination bitmasks from FLAGS.
    """
    from spectra_extractor import extract_full_spectrum

    start_time = time.time()
    norm_detector = normalize_detector_name(detector)
    target_coord = SkyCoord(ra=ra, dec=dec, unit="deg", frame="icrs")

    # Check Cache
    cache_key = get_cache_key(ra, dec, fov, norm_detector)
    cached = get_cached_cutouts(cache_key)

    if cached is not None and len(cached) >= 3:
        cutouts = cached[:max_cutouts]
    else:
        try:
            tap_rows = query_spherex_tap(ra, dec, fov_deg=fov, detector=norm_detector, limit=max_cutouts)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"IRSA TAP query failed: {exc}")

        if not tap_rows:
            return {
                "status": "no_data",
                "message": f"No SPHEREx data available at RA={ra:.5f}, Dec={dec:.5f}",
                "spectrum_102_band": [],
                "raw_photometry": [],
            }

        cutouts = fetch_cutouts_parallel(
            tap_rows,
            target_coord=target_coord,
            max_workers=8,
            remove_zodi=True,
            limit=max_cutouts,
        )

        if not cutouts:
            raise HTTPException(status_code=504, detail="Failed to fetch cutouts for spectrum extraction.")

        store_cached_cutouts(cache_key, cutouts)

    # Extract spectrophotometry
    spectral_dataset = extract_full_spectrum(
        cutouts,
        target_coord=target_coord,
        aperture_radius_arcsec=aperture_radius_arcsec,
    )

    elapsed = round(time.time() - start_time, 2)

    return {
        "status": "success",
        "target": {"ra": ra, "dec": dec, "aperture_radius_arcsec": aperture_radius_arcsec},
        "spectrum_102_band": spectral_dataset["binned_102_band"],
        "raw_photometry": spectral_dataset["raw_points"],
        "summary": spectral_dataset["summary"],
        "execution_time_seconds": elapsed,
    }


# Mount project root as static folder so frontend HTML/JS/CSS can be served directly
app.mount("/static", StaticFiles(directory=".", html=True), name="static")


if __name__ == "__main__":
    # Bound strictly to 127.0.0.1 (localhost) to prevent external Wi-Fi scanners/probes
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
