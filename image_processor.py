"""
SPHEREx Image Processor Module.
Handles:
1. North-Up Tangential (TAN) WCS reprojection with reproject_interp.
2. 3 Processing Modes:
   - epochs: Time-sorted individual frames for blinking animations.
   - coadd: Mean/median stacks grouped by detector array (D1-D6) to improve SNR.
   - diff: Epoch subtraction (Epoch_B - Epoch_A) to highlight moving objects (dipoles).
3. Astronomical contrast stretching (AsinhStretch + ZScaleInterval) & Base64 PNG encoding.
"""

import base64
import io
import logging
import warnings
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
import matplotlib.cm as cm
import numpy as np
from astropy.stats import sigma_clipped_stats
from astropy.visualization import AsinhStretch, ImageNormalize, LinearStretch, ZScaleInterval
from astropy.wcs import FITSFixedWarning, WCS
from PIL import Image
from reproject import reproject_interp
from scipy.ndimage import maximum_filter

# Suppress benign warnings
warnings.filterwarnings("ignore", category=FITSFixedWarning)
warnings.filterwarnings("ignore", message=".*Inconsistent SIP distortion.*")
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*All-NaN slice.*")

logger = logging.getLogger("spherex.image_processor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def build_reference_wcs(
    ra_deg: float,
    dec_deg: float,
    fov_deg: float = 0.05,
    pixel_scale_arcsec: float = 6.2,
) -> Tuple[WCS, Tuple[int, int]]:
    """
    Construct a standard North-Up, East-Left tangential (TAN) WCS projection grid.
    
    SPHEREx native spatial resolution is ~6.2 arcsec/pixel.
    """
    scale_deg = pixel_scale_arcsec / 3600.0
    npix = int(round(fov_deg / scale_deg))
    # Constrain canvas dimension between 16 and 512 pixels for performant web rendering
    npix = max(16, min(512, npix))

    w = WCS(naxis=2)
    # 1-based CRPIX centered on image
    center_pix = (npix + 1.0) / 2.0
    w.wcs.crpix = [center_pix, center_pix]
    w.wcs.cdelt = np.array([-scale_deg, scale_deg])  # East is Left (-RA), North is Up (+Dec)
    w.wcs.crval = [float(ra_deg), float(dec_deg)]
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]

    shape_out = (npix, npix)
    return w, shape_out


def reproject_cutout_image(
    image_data: np.ndarray,
    input_wcs: WCS,
    target_wcs: WCS,
    shape_out: Tuple[int, int],
) -> np.ndarray:
    """
    Reproject a 2D cutout onto the target WCS grid using reproject_interp with bicubic spline.
    Fills out-of-bounds or non-finite pixels with NaN.
    """
    try:
        reprojected, _ = reproject_interp(
            (image_data, input_wcs),
            target_wcs,
            shape_out=shape_out,
            order="bicubic",
        )
        return reprojected.astype(np.float32)
    except Exception as exc:
        logger.warning(f"reproject_interp failed: {exc}. Returning NaN grid.")
        return np.full(shape_out, np.nan, dtype=np.float32)


def detect_star_peaks(
    image_array: np.ndarray,
    min_snr: float = 3.5,
    max_stars: int = 50,
) -> List[Dict[str, Any]]:
    """
    Automatically detect point sources (stars/dwarfs) above the local background
    using 3x3 local maximum filtering and sigma-clipped noise estimation.
    """
    arr = np.copy(image_array).astype(np.float32)
    valid = arr[np.isfinite(arr) & (arr > -1.0)]
    if valid.size < 16:
        return []

    try:
        mean_bkg, med_bkg, std_bkg = sigma_clipped_stats(valid, sigma=2.5)
        if std_bkg <= 0 or not np.isfinite(std_bkg):
            return []

        threshold = med_bkg + min_snr * std_bkg
        max_filt = maximum_filter(arr, size=3)
        peaks_mask = (arr == max_filt) & (arr >= threshold) & np.isfinite(arr)
        y_indices, x_indices = np.where(peaks_mask)

        H, W = arr.shape
        stars: List[Dict[str, Any]] = []
        for x, y in zip(x_indices, y_indices):
            peak_val = float(arr[y, x])
            snr = round(float((peak_val - med_bkg) / std_bkg), 1)
            stars.append({
                "x_pix": int(x),
                "y_pix": int(y),
                "norm_x": round(float(x) / max(1, W - 1), 4),
                "norm_y": round(float(y) / max(1, H - 1), 4),
                "peak_flux": round(peak_val, 4),
                "snr": snr,
            })

        stars.sort(key=lambda s: s["snr"], reverse=True)
        return stars[:max_stars]
    except Exception as exc:
        logger.warning(f"Star detection failed: {exc}")
        return []


def stretch_and_encode_png(
    image_array: np.ndarray,
    stretch_type: str = "asinh",
    colormap: str = "magma",
    invert: bool = False,
    asinh_a: float = 0.08,
    contrast: float = 0.25,
    is_diff: bool = False,
    clean_background: bool = True,
) -> Dict[str, Any]:
    """
    Apply robust background noise floor, anomaly filtering, astronomical contrast
    stretching (AsinhStretch), and encode into Base64 PNG ready for HTML5 <canvas> display.
    """
    arr = np.copy(image_array).astype(np.float32)
    # Reject extreme negative values and NaNs
    nan_mask = ~np.isfinite(arr) | (arr < -1.0)
    arr[nan_mask] = np.nan
    valid_pixels = arr[~nan_mask]

    if valid_pixels.size == 0:
        # Blank image fallback
        blank = np.zeros((arr.shape[0], arr.shape[1], 4), dtype=np.uint8)
        img = Image.fromarray(blank, mode="RGBA")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return {
            "base64": raw_b64,
            "data_url": f"data:image/png;base64,{raw_b64}",
            "vmin": 0.0,
            "vmax": 1.0,
            "width": arr.shape[1],
            "height": arr.shape[0],
        }

    # Determine display scale limits
    if is_diff:
        # Symmetrical stretch centered at zero to clearly display positive/negative dipoles
        diff_median = float(np.median(valid_pixels))
        abs_dev = np.abs(valid_pixels - diff_median)
        p99 = float(np.percentile(abs_dev, 99.0))
        if p99 <= 0:
            p99 = 1.0
        vmin = diff_median - p99
        vmax = diff_median + p99
    elif clean_background:
        # Robust astronomical background subtraction & noise suppression
        try:
            mean_bkg, med_bkg, std_bkg = sigma_clipped_stats(valid_pixels, sigma=2.5)
            # Push all sky noise fluctuations below the background noise floor to vmin
            vmin = float(med_bkg)
            vmax = float(min(np.max(valid_pixels), med_bkg + 18.0 * std_bkg))
            if vmax <= vmin:
                vmax = vmin + 1.0
        except Exception:
            vmin = float(np.percentile(valid_pixels, 15.0))
            vmax = float(np.percentile(valid_pixels, 99.5))
    else:
        # Standard astronomical ZScale interval
        try:
            zscale = ZScaleInterval(contrast=contrast)
            vmin, vmax = zscale.get_limits(valid_pixels)
        except Exception:
            vmin = float(np.percentile(valid_pixels, 1.0))
            vmax = float(np.percentile(valid_pixels, 99.0))

    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmin >= vmax:
        vmin = float(np.nanmin(valid_pixels))
        vmax = float(np.nanmax(valid_pixels))
        if vmin >= vmax:
            vmax = vmin + 1e-4

    # Apply stretch
    if stretch_type.lower() == "linear":
        stretch_obj = LinearStretch()
    else:
        stretch_obj = AsinhStretch(a=asinh_a)

    norm = ImageNormalize(vmin=vmin, vmax=vmax, stretch=stretch_obj, clip=True)
    norm_data = norm(arr)
    # Zero-out NaNs
    norm_data[nan_mask] = 0.0

    if invert:
        norm_data = 1.0 - norm_data

    # Map to colormap
    try:
        cmap = matplotlib.colormaps[colormap]
    except KeyError:
        cmap = matplotlib.colormaps.get("magma", matplotlib.colormaps["viridis"])

    rgba_floats = cmap(norm_data)
    rgba_bytes = (rgba_floats * 255).astype(np.uint8)

    # Set NaN pixels to transparent
    rgba_bytes[nan_mask, 3] = 0

    img = Image.fromarray(rgba_bytes, mode="RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    return {
        "base64": raw_b64,
        "data_url": f"data:image/png;base64,{raw_b64}",
        "vmin": float(vmin),
        "vmax": float(vmax),
        "width": int(arr.shape[1]),
        "height": int(arr.shape[0]),
    }


def process_epochs_mode(
    cutouts: List[Dict[str, Any]],
    target_wcs: WCS,
    shape_out: Tuple[int, int],
    colormap: str = "magma",
    stretch: str = "asinh",
) -> List[Dict[str, Any]]:
    """
    Mode 'epochs': Generate time-sorted individual frames for blinking animations.
    """
    frames: List[Dict[str, Any]] = []

    for idx, cutout in enumerate(cutouts):
        reprojected = reproject_cutout_image(
            cutout["image"],
            cutout["spatial_wcs"],
            target_wcs,
            shape_out,
        )

        encoded = stretch_and_encode_png(
            reprojected,
            stretch_type=stretch,
            colormap=colormap,
            is_diff=False,
            clean_background=True,
        )

        stars = detect_star_peaks(reprojected, min_snr=3.5)

        frames.append({
            "frame_index": idx,
            "obs_mjd": cutout["obs_mjd_start"],
            "obs_mjd_end": cutout["obs_mjd_end"],
            "detector": cutout["detector_band"],
            "wavelength_um": cutout["wavelength_um"],
            "bandwidth_um": cutout["bandwidth_um"],
            "image_base64": encoded["base64"],
            "data_url": encoded["data_url"],
            "vmin": encoded["vmin"],
            "vmax": encoded["vmax"],
            "width": shape_out[1],
            "height": shape_out[0],
            "mean_flux": float(np.nanmean(reprojected)) if np.any(np.isfinite(reprojected)) else 0.0,
            "detected_stars": stars,
        })

    return frames


def process_coadd_mode(
    cutouts: List[Dict[str, Any]],
    target_wcs: WCS,
    shape_out: Tuple[int, int],
    colormap: str = "viridis",
    stretch: str = "asinh",
) -> Dict[str, Any]:
    """
    Mode 'coadd': Mean/median stack grouped by detector array (D1-D6) + overall mission coadd.
    Improves Signal-to-Noise Ratio (SNR).
    """
    # Group reprojected cutouts by detector band
    detector_groups: Dict[str, List[np.ndarray]] = {}
    detector_wavelengths: Dict[str, List[float]] = {}
    all_reprojected: List[np.ndarray] = []

    for cutout in cutouts:
        reprojected = reproject_cutout_image(
            cutout["image"],
            cutout["spatial_wcs"],
            target_wcs,
            shape_out,
        )
        band = cutout["detector_band"]
        detector_groups.setdefault(band, []).append(reprojected)
        detector_wavelengths.setdefault(band, []).append(cutout["wavelength_um"])
        all_reprojected.append(reprojected)

    detector_stacks: List[Dict[str, Any]] = []

    # Sort detector groups canonically D1 -> D6
    sorted_bands = sorted(detector_groups.keys())
    for band in sorted_bands:
        stack_cube = np.stack(detector_groups[band], axis=0)
        # Median stack ignores cosmic rays and transient artifacts
        coadd_array = np.nanmedian(stack_cube, axis=0)

        encoded = stretch_and_encode_png(
            coadd_array,
            stretch_type=stretch,
            colormap=colormap,
            is_diff=False,
        )

        mean_wave = float(np.mean(detector_wavelengths[band]))
        detector_stacks.append({
            "detector": band,
            "frames_stacked": len(detector_groups[band]),
            "mean_wavelength_um": round(mean_wave, 4),
            "image_base64": encoded["base64"],
            "data_url": encoded["data_url"],
            "vmin": encoded["vmin"],
            "vmax": encoded["vmax"],
            "width": shape_out[1],
            "height": shape_out[0],
        })

    # Overall full coadd
    overall_stack: Optional[Dict[str, Any]] = None
    if all_reprojected:
        full_cube = np.stack(all_reprojected, axis=0)
        full_coadd_array = np.nanmedian(full_cube, axis=0)
        full_encoded = stretch_and_encode_png(
            full_coadd_array,
            stretch_type=stretch,
            colormap=colormap,
            is_diff=False,
        )
        overall_stack = {
            "detector": "SPHEREx-All",
            "frames_stacked": len(all_reprojected),
            "image_base64": full_encoded["base64"],
            "data_url": full_encoded["data_url"],
            "vmin": full_encoded["vmin"],
            "vmax": full_encoded["vmax"],
            "width": shape_out[1],
            "height": shape_out[0],
        }

    return {
        "detector_coadds": detector_stacks,
        "overall_coadd": overall_stack,
        "total_input_frames": len(cutouts),
    }


def process_diff_mode(
    cutouts: List[Dict[str, Any]],
    target_wcs: WCS,
    shape_out: Tuple[int, int],
    colormap: str = "coolwarm",
    stretch: str = "asinh",
) -> Dict[str, Any]:
    """
    Mode 'diff': Epoch subtraction (Epoch_B - Epoch_A) to highlight moving objects (dipoles)
    such as Planet X candidates, brown dwarfs, or high proper-motion stars.
    """
    if len(cutouts) < 2:
        raise ValueError(f"Epoch differencing requires at least 2 distinct exposures, got {len(cutouts)}")

    # Sort strictly by MJD
    sorted_cutouts = sorted(cutouts, key=lambda c: c["obs_mjd_start"])

    # Split into two temporal halves (Epoch A vs Epoch B)
    mid_idx = max(1, len(sorted_cutouts) // 2)
    epoch_a_cutouts = sorted_cutouts[:mid_idx]
    epoch_b_cutouts = sorted_cutouts[mid_idx:]

    # Reproject and median stack Epoch A
    reproj_a = [
        reproject_cutout_image(c["image"], c["spatial_wcs"], target_wcs, shape_out)
        for c in epoch_a_cutouts
    ]
    stack_a = np.nanmedian(np.stack(reproj_a, axis=0), axis=0)

    # Reproject and median stack Epoch B
    reproj_b = [
        reproject_cutout_image(c["image"], c["spatial_wcs"], target_wcs, shape_out)
        for c in epoch_b_cutouts
    ]
    stack_b = np.nanmedian(np.stack(reproj_b, axis=0), axis=0)

    # Difference: Epoch B - Epoch A
    diff_image = stack_b - stack_a

    # Dipole analysis
    valid_diff = diff_image[np.isfinite(diff_image)]
    pos_peak = float(np.nanmax(valid_diff)) if valid_diff.size > 0 else 0.0
    neg_peak = float(np.nanmin(valid_diff)) if valid_diff.size > 0 else 0.0
    dipole_contrast = pos_peak - neg_peak

    # Encode images
    encoded_diff = stretch_and_encode_png(
        diff_image,
        stretch_type=stretch,
        colormap=colormap,
        is_diff=True,
    )
    encoded_a = stretch_and_encode_png(
        stack_a,
        stretch_type="asinh",
        colormap="viridis",
        is_diff=False,
    )
    encoded_b = stretch_and_encode_png(
        stack_b,
        stretch_type="asinh",
        colormap="viridis",
        is_diff=False,
    )

    return {
        "diff_frame": {
            "image_base64": encoded_diff["base64"],
            "data_url": encoded_diff["data_url"],
            "vmin": encoded_diff["vmin"],
            "vmax": encoded_diff["vmax"],
            "width": shape_out[1],
            "height": shape_out[0],
            "positive_peak_mjy_sr": pos_peak,
            "negative_peak_mjy_sr": neg_peak,
            "dipole_contrast": dipole_contrast,
        },
        "epoch_a": {
            "obs_mjd_start": epoch_a_cutouts[0]["obs_mjd_start"],
            "obs_mjd_end": epoch_a_cutouts[-1]["obs_mjd_end"],
            "frames_stacked": len(epoch_a_cutouts),
            "image_base64": encoded_a["base64"],
            "data_url": encoded_a["data_url"],
        },
        "epoch_b": {
            "obs_mjd_start": epoch_b_cutouts[0]["obs_mjd_start"],
            "obs_mjd_end": epoch_b_cutouts[-1]["obs_mjd_end"],
            "frames_stacked": len(epoch_b_cutouts),
            "image_base64": encoded_b["base64"],
            "data_url": encoded_b["data_url"],
        },
        "delta_mjd": float(epoch_b_cutouts[0]["obs_mjd_start"] - epoch_a_cutouts[-1]["obs_mjd_end"]),
    }
