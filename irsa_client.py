"""
SPHEREx IRSA Client & Parallel Cutout Fetcher Module.
Queries IPAC/IRSA TAP services for SPHEREx Multi-Extension FITS (MEF) cutouts,
downloads cutouts in parallel using ThreadPoolExecutor with retry logic,
subtracts ZODI background (HDU 4) from IMAGE (HDU 1), and extracts target
wavelength and bandwidth using alternate WCS key="W" (WCS-WAVE HDU 6).
"""

import concurrent.futures
import io
import logging
import re
import ssl
import time
import warnings
from typing import Any, Dict, List, Optional, Tuple

import astropy.units as u
import certifi
import numpy as np
import requests
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.utils.data import conf
from astropy.wcs import FITSFixedWarning, WCS
from astroquery.ipac.irsa import Irsa

# Suppress benign WCS SIP warnings from SPHEREx Level 2 headers
warnings.filterwarnings("ignore", category=FITSFixedWarning)
warnings.filterwarnings("ignore", message=".*Inconsistent SIP distortion.*")

# Configure remote timeout for astropy & requests
conf.remote_timeout = 120

logger = logging.getLogger("spherex.irsa_client")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Known SPHEREx detector bands
VALID_DETECTORS = {
    "D1": "SPHEREx-D1",
    "SPHEREX-D1": "SPHEREx-D1",
    "D2": "SPHEREx-D2",
    "SPHEREX-D2": "SPHEREx-D2",
    "D3": "SPHEREx-D3",
    "SPHEREX-D3": "SPHEREx-D3",
    "D4": "SPHEREx-D4",
    "SPHEREX-D4": "SPHEREx-D4",
    "D5": "SPHEREx-D5",
    "SPHEREX-D5": "SPHEREx-D5",
    "D6": "SPHEREx-D6",
    "SPHEREX-D6": "SPHEREx-D6",
    "ALL": "SPHEREx-All",
    "SPHEREX-ALL": "SPHEREx-All",
}

# Nominal detector wavelength coverage ranges (micrometers)
DETECTOR_WAVELENGTH_RANGES = {
    "SPHEREx-D1": (0.75, 1.09),
    "SPHEREx-D2": (1.10, 1.62),
    "SPHEREx-D3": (1.63, 2.41),
    "SPHEREx-D4": (2.42, 3.82),
    "SPHEREx-D5": (3.83, 4.41),
    "SPHEREx-D6": (4.42, 5.00),
}


def normalize_detector_name(detector: Optional[str]) -> Optional[str]:
    """Normalize input detector string to canonical 'SPHEREx-D1'...'SPHEREx-D6' format."""
    if not detector:
        return None
    cleaned = detector.strip().upper()
    return VALID_DETECTORS.get(cleaned, detector)


def build_tap_query(
    ra_deg: float,
    dec_deg: float,
    fov_deg: float = 0.05,
    detector: Optional[str] = None,
    limit: Optional[int] = None,
) -> str:
    """
    Construct ADQL query against `spherex.artifact` and `spherex.plane` on IRSA TAP.
    
    Generates dynamic IBE cutout URLs:
    https://irsa.ipac.caltech.edu/{a.uri}?center={ra},{dec}d&size={fov}
    """
    norm_detector = normalize_detector_name(detector)
    detector_clause = f"AND p.energy_bandpassname = '{norm_detector}'" if norm_detector else ""
    limit_clause = f"TOP {int(limit)}" if limit and limit > 0 else ""

    query = f"""
    SELECT {limit_clause}
        'https://irsa.ipac.caltech.edu/' || a.uri || '?center=' || CAST({ra_deg:.7f} AS VARCHAR) || ',' || CAST({dec_deg:.7f} AS VARCHAR) || 'd&size=' || CAST({fov_deg:.5f} AS VARCHAR) AS cutout_url,
        'https://irsa.ipac.caltech.edu/' || a.uri AS full_mef_url,
        p.time_bounds_lower AS obs_mjd_start,
        p.time_bounds_upper AS obs_mjd_end,
        p.energy_bandpassname AS detector_band,
        p.provenance_version AS version
    FROM spherex.artifact a
    JOIN spherex.plane p ON a.planeid = p.planeid
    WHERE 1 = CONTAINS(POINT('ICRS', {ra_deg:.7f}, {dec_deg:.7f}), p.poly)
    {detector_clause}
    ORDER BY p.time_bounds_lower ASC
    """
    return query.strip()


def query_spherex_tap(
    ra_deg: float,
    dec_deg: float,
    fov_deg: float = 0.05,
    detector: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Execute IRSA TAP query and return list of metadata rows for matching cutouts.
    """
    query = build_tap_query(ra_deg, dec_deg, fov_deg, detector, limit)
    logger.info(f"Executing IRSA TAP query for RA={ra_deg:.5f}, Dec={dec_deg:.5f}, FOV={fov_deg:.4f}, detector={detector}")

    try:
        table_result = Irsa.query_tap(query).to_table()
        rows: List[Dict[str, Any]] = []
        for row in table_result:
            rows.append({
                "cutout_url": str(row["cutout_url"]).strip(),
                "full_mef_url": str(row["full_mef_url"]).strip(),
                "obs_mjd_start": float(row["obs_mjd_start"]),
                "obs_mjd_end": float(row["obs_mjd_end"]),
                "detector_band": str(row["detector_band"]).strip(),
                "version": str(row["version"]).strip() if "version" in row.colnames else "DR1",
            })
        logger.info(f"IRSA TAP returned {len(rows)} cutouts.")
        return rows
    except Exception as exc:
        logger.error(f"Error querying IRSA TAP: {exc}", exc_info=True)
        raise RuntimeError(f"Failed to query IRSA TAP: {exc}") from exc


def download_fits_bytes(url: str, timeout: int = 60, max_retries: int = 3) -> bytes:
    """
    Download FITS data into memory with exponential backoff and certifi SSL context.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": "SPHEREx-IRSA-Pipeline/1.0 (NASA/IPAC)"})

    last_error: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            response = session.get(url, timeout=timeout, verify=certifi.where())
            response.raise_for_status()
            if len(response.content) < 2880:
                raise ValueError(f"Downloaded content too small for FITS: {len(response.content)} bytes")
            return response.content
        except Exception as err:
            last_error = err
            wait_time = 1.5 ** attempt
            logger.warning(f"Download attempt {attempt}/{max_retries} failed for {url}: {err}. Retrying in {wait_time:.1f}s...")
            time.sleep(wait_time)

    raise ConnectionError(f"Failed to download cutout from {url} after {max_retries} attempts: {last_error}")


def process_single_cutout(
    cutout_meta: Dict[str, Any],
    target_coord: SkyCoord,
    remove_zodi: bool = True,
    timeout: int = 60,
    max_retries: int = 3,
) -> Optional[Dict[str, Any]]:
    """
    Fetch and parse a single Multi-Extension FITS (MEF) cutout.
    
    1. Reads HDU 1 ('IMAGE') surface brightness (MJy/sr).
    2. Subtracts HDU 4 ('ZODI') if present and remove_zodi is True.
    3. Extracts HDU 2 ('FLAGS') bitmask and HDU 3 ('VARIANCE') noise map.
    4. Evaluates exact target wavelength (um) and bandwidth (um) using WCS key='W' (HDU 6 'WCS-WAVE').
    """
    url = cutout_meta["cutout_url"]
    try:
        raw_bytes = download_fits_bytes(url, timeout=timeout, max_retries=max_retries)
        with fits.open(io.BytesIO(raw_bytes)) as hdul:
            if "IMAGE" not in hdul:
                logger.error(f"Missing 'IMAGE' HDU in cutout {url}")
                return None

            img_hdu = hdul["IMAGE"]
            img_data = img_hdu.data.astype(np.float32)
            header = img_hdu.header.copy()

            # Spatial WCS & target pixel coordinates
            spatial_wcs = WCS(header)
            x_pix, y_pix = spatial_wcs.world_to_pixel(target_coord)
            x_pix_f = float(x_pix)
            y_pix_f = float(y_pix)

            # HDU 4: ZODI background model subtraction
            zodi_subtracted = False
            zodi_mean = 0.0
            if remove_zodi and "ZODI" in hdul and hdul["ZODI"].data is not None:
                zodi_data = hdul["ZODI"].data.astype(np.float32)
                zodi_mean = float(np.nanmean(zodi_data))
                img_data = img_data - zodi_data
                zodi_subtracted = True

            # HDU 2: FLAGS 32-bit bitmask
            flags_data = None
            if "FLAGS" in hdul and hdul["FLAGS"].data is not None:
                flags_data = hdul["FLAGS"].data.astype(np.int32)

            # HDU 3: VARIANCE noise map (MJy/sr)^2
            variance_data = None
            if "VARIANCE" in hdul and hdul["VARIANCE"].data is not None:
                variance_data = hdul["VARIANCE"].data.astype(np.float32)

            # HDU 6: WCS-WAVE spectral coordinate extraction
            wavelength_um = None
            bandwidth_um = None
            try:
                spectral_wcs = WCS(header=header, fobj=hdul, key="W")
                w_val, bw_val = spectral_wcs.pixel_to_world_values(x_pix_f, y_pix_f)
                if np.isfinite(w_val) and w_val > 0:
                    wavelength_um = float(w_val)
                    bandwidth_um = float(bw_val) if np.isfinite(bw_val) else 0.02
            except Exception as wcs_err:
                logger.debug(f"WCS key='W' lookup failed for {url}: {wcs_err}. Using nominal bandpass.")

            # Fallback to nominal detector range if WCS key='W' was not available
            detector_band = cutout_meta.get("detector_band", "SPHEREx-D1")
            if wavelength_um is None or not np.isfinite(wavelength_um):
                band_range = DETECTOR_WAVELENGTH_RANGES.get(detector_band, (1.0, 1.5))
                wavelength_um = float((band_range[0] + band_range[1]) / 2.0)
                bandwidth_um = float((band_range[1] - band_range[0]) / 17.0)

            return {
                "image": img_data,
                "flags": flags_data,
                "variance": variance_data,
                "header": header,
                "spatial_wcs": spatial_wcs,
                "target_pixel": (x_pix_f, y_pix_f),
                "wavelength_um": wavelength_um,
                "bandwidth_um": bandwidth_um,
                "obs_mjd_start": cutout_meta["obs_mjd_start"],
                "obs_mjd_end": cutout_meta["obs_mjd_end"],
                "detector_band": detector_band,
                "version": cutout_meta.get("version", "DR1"),
                "cutout_url": url,
                "zodi_subtracted": zodi_subtracted,
                "zodi_mean": zodi_mean,
            }

    except Exception as exc:
        logger.error(f"Failed processing cutout {url}: {exc}", exc_info=True)
        return None


def fetch_cutouts_parallel(
    tap_rows: List[Dict[str, Any]],
    target_coord: SkyCoord,
    max_workers: int = 8,
    remove_zodi: bool = True,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch Multi-Extension FITS (MEF) cutouts concurrently using ThreadPoolExecutor(max_workers=8).
    
    Returns list of successfully processed cutout dictionaries sorted by obs_mjd_start.
    """
    if limit and limit > 0:
        tap_rows = tap_rows[:limit]

    logger.info(f"Fetching {len(tap_rows)} cutouts in parallel with {max_workers} workers...")

    results: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {
            executor.submit(
                process_single_cutout,
                row,
                target_coord,
                remove_zodi,
            ): row["cutout_url"]
            for row in tap_rows
        }

        for future in concurrent.futures.as_completed(future_to_url):
            url = future_to_url[future]
            try:
                cutout_dict = future.result()
                if cutout_dict is not None:
                    results.append(cutout_dict)
            except Exception as err:
                logger.warning(f"Worker exception for cutout {url}: {err}")

    # Sort results chronologically by MJD
    results.sort(key=lambda item: item["obs_mjd_start"])
    logger.info(f"Successfully processed {len(results)} of {len(tap_rows)} cutouts.")
    return results
