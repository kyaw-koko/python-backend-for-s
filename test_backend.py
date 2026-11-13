"""
Unit and Integration Test Suite for Planet X and SPHEREx Backend Engine.
Tests all modules, processing modes, algorithms, and FastAPI endpoints.
"""

import sys
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.wcs import WCS
from fastapi.testclient import TestClient

from main import app, BENCHMARK_TARGETS
from irsa_client import (
    build_tap_query,
    normalize_detector_name,
    VALID_DETECTORS,
    DETECTOR_WAVELENGTH_RANGES,
)
from image_processor import (
    build_reference_wcs,
    stretch_and_encode_png,
    process_epochs_mode,
    process_coadd_mode,
    process_diff_mode,
)
from spectra_extractor import (
    decode_flags,
    compute_pixel_solid_angle_sr,
    extract_photometry_single,
    synthesize_102_band_spectrum,
    extract_full_spectrum,
)
from catalog_overlay import project_sources_to_wcs

client = TestClient(app)


def test_detector_normalization():
    print("Testing detector normalization...")
    assert normalize_detector_name("d1") == "SPHEREx-D1"
    assert normalize_detector_name("D6") == "SPHEREx-D6"
    assert normalize_detector_name("SPHEREx-D3") == "SPHEREx-D3"
    assert normalize_detector_name(None) is None
    print("  -> Passed!")


def test_tap_query_builder():
    print("Testing TAP query builder...")
    q = build_tap_query(269.452075, 4.693391, fov_deg=0.05, detector="SPHEREx-D1", limit=10)
    assert "spherex.artifact" in q
    assert "spherex.plane" in q
    assert "SPHEREx-D1" in q
    assert "269.4520750" in q
    assert "TOP 10" in q
    print("  -> Passed!")


def test_reference_wcs():
    print("Testing reference North-Up TAN WCS builder...")
    wcs, shape = build_reference_wcs(269.452075, 4.693391, fov_deg=0.05, pixel_scale_arcsec=6.2)
    assert wcs.wcs.ctype[0] == "RA---TAN"
    assert wcs.wcs.ctype[1] == "DEC--TAN"
    assert wcs.wcs.cdelt[0] < 0  # East is Left
    assert wcs.wcs.cdelt[1] > 0  # North is Up
    assert shape[0] == shape[1]
    assert shape[0] >= 16
    print(f"  -> Reference grid shape: {shape}, passed!")


def test_stretch_and_encode_png():
    print("Testing astronomical contrast stretching & Base64 PNG encoding...")
    img = np.random.normal(loc=1.0, scale=0.2, size=(32, 32)).astype(np.float32)
    img[16, 16] = 20.0  # Star

    res = stretch_and_encode_png(img, stretch_type="asinh", colormap="viridis")
    assert "base64" in res
    assert "data_url" in res
    assert res["data_url"].startswith("data:image/png;base64,")
    assert res["width"] == 32
    assert res["height"] == 32
    assert res["vmax"] > res["vmin"]

    # Test diff mode stretch (symmetrical around zero)
    diff_img = np.random.normal(loc=0.0, scale=0.1, size=(32, 32)).astype(np.float32)
    diff_img[10, 10] = -5.0  # Negative dipole
    diff_img[20, 20] = 5.0   # Positive dipole
    res_diff = stretch_and_encode_png(diff_img, stretch_type="asinh", colormap="coolwarm", is_diff=True)
    assert res_diff["base64"] is not None
    print("  -> Passed!")


def test_image_processor_modes():
    print("Testing image processor modes (epochs, coadd, diff)...")
    ref_wcs, shape_out = build_reference_wcs(269.452, 4.693, fov_deg=0.05)

    # Build mock cutouts with realistic WCS
    cutouts = []
    for i in range(4):
        w = WCS(naxis=2)
        w.wcs.crpix = [15.0, 15.0]
        w.wcs.cdelt = [-6.2 / 3600.0, 6.2 / 3600.0]
        w.wcs.crval = [269.452, 4.693]
        w.wcs.ctype = ["RA---TAN", "DEC--TAN"]

        data = np.full((30, 30), 0.1 * (i + 1), dtype=np.float32)
        data[15, 15] = 10.0 + i

        det = f"SPHEREx-D{(i % 2) + 1}"
        cutouts.append({
            "image": data,
            "spatial_wcs": w,
            "obs_mjd_start": 60900.0 + i * 10.0,
            "obs_mjd_end": 60900.0 + i * 10.0 + 0.001,
            "detector_band": det,
            "wavelength_um": 0.85 + i * 0.4,
            "bandwidth_um": 0.02,
        })

    # Test epochs mode
    epochs = process_epochs_mode(cutouts, ref_wcs, shape_out)
    assert len(epochs) == 4
    assert epochs[0]["frame_index"] == 0
    assert epochs[0]["image_base64"] is not None

    # Test coadd mode
    coadd = process_coadd_mode(cutouts, ref_wcs, shape_out)
    assert "detector_coadds" in coadd
    assert len(coadd["detector_coadds"]) == 2  # D1 and D2
    assert coadd["overall_coadd"] is not None

    # Test diff mode
    diff = process_diff_mode(cutouts, ref_wcs, shape_out)
    assert "diff_frame" in diff
    assert "epoch_a" in diff
    assert "epoch_b" in diff
    assert diff["diff_frame"]["image_base64"] is not None
    print("  -> Passed!")


def test_spectra_extractor():
    print("Testing spectra extractor & 102-band SED synthesizer...")
    target = SkyCoord(ra=269.452, dec=4.693, unit="deg")
    ref_wcs, _ = build_reference_wcs(269.452, 4.693, 0.05)

    cutouts = []
    for i in range(6):
        w = WCS(naxis=2)
        w.wcs.crpix = [15.0, 15.0]
        w.wcs.cdelt = [-6.2 / 3600.0, 6.2 / 3600.0]
        w.wcs.crval = [269.452, 4.693]
        w.wcs.ctype = ["RA---TAN", "DEC--TAN"]

        data = np.full((30, 30), 0.05, dtype=np.float32)
        data[14, 14] = 5.0
        var = np.full((30, 30), 0.01, dtype=np.float32)
        flags = np.zeros((30, 30), dtype=np.int32)
        flags[14, 14] = 8  # SOURCE_DETECTED

        cutouts.append({
            "image": data,
            "variance": var,
            "flags": flags,
            "spatial_wcs": w,
            "obs_mjd_start": 60900.0 + i,
            "detector_band": f"SPHEREx-D{i + 1}",
            "wavelength_um": 0.8 + i * 0.7,
            "bandwidth_um": 0.02,
        })

    full_spec = extract_full_spectrum(cutouts, target, aperture_radius_arcsec=6.2)
    assert "raw_points" in full_spec
    assert "binned_102_band" in full_spec
    assert len(full_spec["binned_102_band"]) == 102
    assert len(full_spec["raw_points"]) == 6

    # Verify flux conversion
    first_pt = full_spec["raw_points"][0]
    assert first_pt["flux_uJy"] > 0
    assert first_pt["flux_err_uJy"] > 0
    assert first_pt["snr"] > 0
    assert "SOURCE_DETECTED" in first_pt["flag_labels"]
    print(f"  -> Extracted flux: {first_pt['flux_uJy']} uJy, SNR: {first_pt['snr']}, passed!")


def test_catalog_projection():
    print("Testing catalog projection & proper motion vectors...")
    ref_wcs, shape_out = build_reference_wcs(269.452075, 4.693391, fov_deg=0.05)
    mock_sources = [
        {
            "catalog": "Gaia DR3",
            "designation": "Barnard Star",
            "ra": 269.452075,
            "dec": 4.693391,
            "pmra_mas_yr": -800.2,
            "pmdec_mas_yr": 10362.5,
            "pm_total_mas_yr": 10393.4,
            "mag_g": 9.51,
        }
    ]
    projected = project_sources_to_wcs(mock_sources, ref_wcs, shape_out)
    assert len(projected) == 1
    p = projected[0]
    assert 0.4 <= p["norm_x"] <= 0.6  # Near center
    assert 0.4 <= p["norm_y"] <= 0.6
    assert abs(p["delta_y_pix"]) > 1.0  # Noticeable proper motion in pixels
    print(f"  -> Projected: norm_x={p['norm_x']}, norm_y={p['norm_y']}, delta_y_pix={p['delta_y_pix']}, passed!")


def test_api_endpoints():
    print("Testing FastAPI REST endpoints...")

    # GET /
    r_root = client.get("/")
    assert r_root.status_code == 200
    assert r_root.json()["engine"] == "Planet X and SPHEREx Backend Engine"

    # GET /api/health
    r_health = client.get("/api/health")
    assert r_health.status_code == 200
    assert r_health.json()["status"] == "healthy"

    # GET /api/targets/presets
    r_presets = client.get("/api/targets/presets")
    assert r_presets.status_code == 200
    targets = r_presets.json()["targets"]
    assert len(targets) == 5
    ids = [t["id"] for t in targets]
    assert "barnards-star" in ids
    assert "wise-0855" in ids
    assert "eps-indi-b" in ids
    assert "rw-aurigae" in ids
    assert "r-draconis" in ids
    print(f"  -> Verified {len(targets)} benchmark targets!")


def run_all_tests():
    print("\n=======================================================")
    print("STARTING TEST SUITE FOR SPHEREx BACKEND ENGINE")
    print("=======================================================\n")
    test_detector_normalization()
    test_tap_query_builder()
    test_reference_wcs()
    test_stretch_and_encode_png()
    test_image_processor_modes()
    test_spectra_extractor()
    test_catalog_projection()
    test_api_endpoints()
    print("\n=======================================================")
    print("ALL CORE UNIT & INTEGRATION TESTS PASSED (100%)")
    print("=======================================================\n")


if __name__ == "__main__":
    run_all_tests()
