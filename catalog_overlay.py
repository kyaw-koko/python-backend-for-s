"""
SPHEREx Multi-Mission Catalog Cross-Match Module.
Queries Gaia DR3 and AllWISE via astroquery within the requested FOV.
Transforms celestial coordinates into normalized pixel coordinates (x, y)
on the reference North-Up TAN WCS grid and computes proper motion vectors.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.wcs import WCS
from astroquery.ipac.irsa import Irsa
from astroquery.vizier import Vizier

logger = logging.getLogger("spherex.catalog_overlay")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def query_gaia_dr3(
    target_coord: SkyCoord,
    radius: u.Quantity,
    max_rows: int = 150,
) -> List[Dict[str, Any]]:
    """
    Query Gaia DR3 sources within cone radius.
    First tries IRSA 'gaia_dr3_source', falls back to Vizier 'I/355/gaiadr3'.
    """
    results: List[Dict[str, Any]] = []

    # 1. Try IRSA
    try:
        table = Irsa.query_region(target_coord, catalog="gaia_dr3_source", radius=radius)
        if table is not None and len(table) > 0:
            for row in table[:max_rows]:
                ra = float(row["ra"])
                dec = float(row["dec"])
                pmra = float(row["pmra"]) if ("pmra" in row.colnames and np.isfinite(row["pmra"])) else 0.0
                pmdec = float(row["pmdec"]) if ("pmdec" in row.colnames and np.isfinite(row["pmdec"])) else 0.0
                g_mag = float(row["phot_g_mean_mag"]) if ("phot_g_mean_mag" in row.colnames and np.isfinite(row["phot_g_mean_mag"])) else None
                bp_mag = float(row["phot_bp_mean_mag"]) if ("phot_bp_mean_mag" in row.colnames and np.isfinite(row["phot_bp_mean_mag"])) else None
                rp_mag = float(row["phot_rp_mean_mag"]) if ("phot_rp_mean_mag" in row.colnames and np.isfinite(row["phot_rp_mean_mag"])) else None
                parallax = float(row["parallax"]) if ("parallax" in row.colnames and np.isfinite(row["parallax"])) else None
                desig = str(row["designation"]).strip() if "designation" in row.colnames else f"Gaia DR3 {row.get('source_id', '')}"

                results.append({
                    "catalog": "Gaia DR3",
                    "designation": desig,
                    "ra": ra,
                    "dec": dec,
                    "pmra_mas_yr": pmra,
                    "pmdec_mas_yr": pmdec,
                    "pm_total_mas_yr": float(np.sqrt(pmra**2 + pmdec**2)),
                    "mag_g": g_mag,
                    "mag_bp": bp_mag,
                    "mag_rp": rp_mag,
                    "parallax_mas": parallax,
                })
            logger.info(f"Retrieved {len(results)} Gaia DR3 sources via IRSA.")
            return results
    except Exception as exc:
        logger.warning(f"IRSA Gaia DR3 query encountered issue: {exc}. Trying VizieR fallback...")

    # 2. Fallback to Vizier
    try:
        v = Vizier(columns=["Source", "RA_ICRS", "DE_ICRS", "pmRA", "pmDE", "Gmag", "BPmag", "RPmag", "Plx"], row_limit=max_rows)
        v_res = v.query_region(target_coord, radius=radius, catalog="I/355/gaiadr3")
        if v_res and len(v_res) > 0:
            vt = v_res[0]
            for row in vt:
                ra = float(row["RA_ICRS"])
                dec = float(row["DE_ICRS"])
                pmra = float(row["pmRA"]) if ("pmRA" in row.colnames and np.isfinite(row["pmRA"])) else 0.0
                pmdec = float(row["pmDE"]) if ("pmDE" in row.colnames and np.isfinite(row["pmDE"])) else 0.0
                g_mag = float(row["Gmag"]) if ("Gmag" in row.colnames and np.isfinite(row["Gmag"])) else None
                bp_mag = float(row["BPmag"]) if ("BPmag" in row.colnames and np.isfinite(row["BPmag"])) else None
                rp_mag = float(row["RPmag"]) if ("RPmag" in row.colnames and np.isfinite(row["RPmag"])) else None
                plx = float(row["Plx"]) if ("Plx" in row.colnames and np.isfinite(row["Plx"])) else None

                results.append({
                    "catalog": "Gaia DR3",
                    "designation": f"Gaia DR3 {row['Source']}",
                    "ra": ra,
                    "dec": dec,
                    "pmra_mas_yr": pmra,
                    "pmdec_mas_yr": pmdec,
                    "pm_total_mas_yr": float(np.sqrt(pmra**2 + pmdec**2)),
                    "mag_g": g_mag,
                    "mag_bp": bp_mag,
                    "mag_rp": rp_mag,
                    "parallax_mas": plx,
                })
            logger.info(f"Retrieved {len(results)} Gaia DR3 sources via VizieR fallback.")
            return results
    except Exception as v_exc:
        logger.warning(f"Vizier Gaia DR3 fallback failed: {v_exc}")

    return results


def query_allwise(
    target_coord: SkyCoord,
    radius: u.Quantity,
    max_rows: int = 150,
) -> List[Dict[str, Any]]:
    """
    Query AllWISE infrared source catalog within cone radius.
    First tries IRSA 'allwise_p3as_psd', falls back to Vizier 'II/328/allwise'.
    """
    results: List[Dict[str, Any]] = []

    # 1. Try IRSA
    try:
        table = Irsa.query_region(target_coord, catalog="allwise_p3as_psd", radius=radius)
        if table is not None and len(table) > 0:
            for row in table[:max_rows]:
                ra = float(row["ra"])
                dec = float(row["dec"])
                pmra = float(row["pmra"]) if ("pmra" in row.colnames and np.isfinite(row["pmra"])) else 0.0
                pmdec = float(row["pmdec"]) if ("pmdec" in row.colnames and np.isfinite(row["pmdec"])) else 0.0
                w1 = float(row["w1mpro"]) if ("w1mpro" in row.colnames and np.isfinite(row["w1mpro"])) else None
                w2 = float(row["w2mpro"]) if ("w2mpro" in row.colnames and np.isfinite(row["w2mpro"])) else None
                w3 = float(row["w3mpro"]) if ("w3mpro" in row.colnames and np.isfinite(row["w3mpro"])) else None
                w4 = float(row["w4mpro"]) if ("w4mpro" in row.colnames and np.isfinite(row["w4mpro"])) else None
                desig = str(row["designation"]).strip() if "designation" in row.colnames else "WISE Source"

                results.append({
                    "catalog": "AllWISE",
                    "designation": desig,
                    "ra": ra,
                    "dec": dec,
                    "pmra_mas_yr": pmra,
                    "pmdec_mas_yr": pmdec,
                    "pm_total_mas_yr": float(np.sqrt(pmra**2 + pmdec**2)),
                    "w1_mag": w1,
                    "w2_mag": w2,
                    "w3_mag": w3,
                    "w4_mag": w4,
                })
            logger.info(f"Retrieved {len(results)} AllWISE sources via IRSA.")
            return results
    except Exception as exc:
        logger.warning(f"IRSA AllWISE query encountered issue: {exc}. Trying VizieR fallback...")

    # 2. Fallback to Vizier
    try:
        v = Vizier(columns=["AllWISE", "RAJ2000", "DEJ2000", "pmRA", "pmDE", "W1mag", "W2mag", "W3mag", "W4mag"], row_limit=max_rows)
        v_res = v.query_region(target_coord, radius=radius, catalog="II/328/allwise")
        if v_res and len(v_res) > 0:
            vt = v_res[0]
            for row in vt:
                ra = float(row["RAJ2000"])
                dec = float(row["DEJ2000"])
                pmra = float(row["pmRA"]) if ("pmRA" in row.colnames and np.isfinite(row["pmRA"])) else 0.0
                pmdec = float(row["pmDE"]) if ("pmDE" in row.colnames and np.isfinite(row["pmDE"])) else 0.0
                w1 = float(row["W1mag"]) if ("W1mag" in row.colnames and np.isfinite(row["W1mag"])) else None
                w2 = float(row["W2mag"]) if ("W2mag" in row.colnames and np.isfinite(row["W2mag"])) else None
                w3 = float(row["W3mag"]) if ("W3mag" in row.colnames and np.isfinite(row["W3mag"])) else None
                w4 = float(row["W4mag"]) if ("W4mag" in row.colnames and np.isfinite(row["W4mag"])) else None

                results.append({
                    "catalog": "AllWISE",
                    "designation": f"WISE {row['AllWISE']}",
                    "ra": ra,
                    "dec": dec,
                    "pmra_mas_yr": pmra,
                    "pmdec_mas_yr": pmdec,
                    "pm_total_mas_yr": float(np.sqrt(pmra**2 + pmdec**2)),
                    "w1_mag": w1,
                    "w2_mag": w2,
                    "w3_mag": w3,
                    "w4_mag": w4,
                })
            logger.info(f"Retrieved {len(results)} AllWISE sources via VizieR fallback.")
            return results
    except Exception as v_exc:
        logger.warning(f"Vizier AllWISE fallback failed: {v_exc}")

    return results


def project_sources_to_wcs(
    sources: List[Dict[str, Any]],
    ref_wcs: WCS,
    shape_out: Tuple[int, int],
    pixel_scale_arcsec: float = 6.2,
    proper_motion_baseline_years: float = 10.0,
) -> List[Dict[str, Any]]:
    """
    Project catalog sources onto reference WCS grid.
    Computes:
    - Pixel coordinates (pixel_x, pixel_y)
    - Normalized canvas coordinates (norm_x, norm_y) [0.0, 1.0]
    - Canvas-relative proper motion vectors (delta_x_canvas, delta_y_canvas)
    """
    height, width = shape_out
    projected: List[Dict[str, Any]] = []

    for src in sources:
        coord = SkyCoord(ra=src["ra"] * u.deg, dec=src["dec"] * u.deg, frame="icrs")
        try:
            x_pix, y_pix = ref_wcs.world_to_pixel(coord)
            x_f = float(x_pix)
            y_f = float(y_pix)

            # Normalized canvas coordinates (0.0 to 1.0)
            # In HTML5 Canvas, (0, 0) is top-left and y goes downwards.
            # In FITS WCS, y=0 is bottom row and y increases upwards.
            norm_x = x_f / float(width)
            norm_y_fits = y_f / float(height)
            norm_y_canvas = 1.0 - norm_y_fits

            # Include sources within canvas plus a 10% outer margin
            if -0.1 <= norm_x <= 1.1 and -0.1 <= norm_y_fits <= 1.1:
                # Compute proper motion vector in pixels
                # pmra is in mas/yr, pmdec is in mas/yr
                # North is Up (+y in FITS, -y in Canvas)
                # East is Left (-x in FITS, -x in Canvas)
                pmra = src["pmra_mas_yr"]
                pmdec = src["pmdec_mas_yr"]

                # Total arcsec motion over baseline
                motion_ra_arcsec = (pmra * proper_motion_baseline_years) / 1000.0
                motion_dec_arcsec = (pmdec * proper_motion_baseline_years) / 1000.0

                # Convert to pixel displacement
                # East is towards -x, North is towards +y (FITS)
                delta_x_pix = -motion_ra_arcsec / pixel_scale_arcsec
                delta_y_pix = motion_dec_arcsec / pixel_scale_arcsec

                # Normalized canvas motion vector
                delta_norm_x = delta_x_pix / float(width)
                delta_norm_y = -delta_y_pix / float(height)  # Inverted for canvas top-down y

                item = dict(src)
                item.update({
                    "pixel_x": round(x_f, 2),
                    "pixel_y": round(y_f, 2),
                    "norm_x": round(norm_x, 4),
                    "norm_y": round(norm_y_fits, 4),
                    "norm_y_canvas": round(norm_y_canvas, 4),
                    "delta_x_pix": round(delta_x_pix, 3),
                    "delta_y_pix": round(delta_y_pix, 3),
                    "delta_norm_x": round(delta_norm_x, 5),
                    "delta_norm_y": round(delta_norm_y, 5),
                    "pm_baseline_years": proper_motion_baseline_years,
                })
                projected.append(item)

        except Exception as exc:
            logger.debug(f"Coordinate projection error for {src['designation']}: {exc}")

    return projected


def cross_match_catalogs(
    ra_deg: float,
    dec_deg: float,
    fov_deg: float,
    ref_wcs: WCS,
    shape_out: Tuple[int, int],
    pixel_scale_arcsec: float = 6.2,
) -> Dict[str, Any]:
    """
    Execute full multi-mission catalog query and cross-match for Gaia DR3 and AllWISE.
    """
    target_coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
    # Search radius covers full diagonal of the FOV square
    search_radius = (fov_deg * np.sqrt(2.0) / 2.0) * u.deg

    logger.info(f"Querying Gaia DR3 & AllWISE within radius {search_radius.to(u.arcmin):.2f}...")

    raw_gaia = query_gaia_dr3(target_coord, search_radius)
    raw_allwise = query_allwise(target_coord, search_radius)

    projected_gaia = project_sources_to_wcs(raw_gaia, ref_wcs, shape_out, pixel_scale_arcsec)
    projected_allwise = project_sources_to_wcs(raw_allwise, ref_wcs, shape_out, pixel_scale_arcsec)

    return {
        "center": {"ra": ra_deg, "dec": dec_deg},
        "fov_deg": fov_deg,
        "pixel_scale_arcsec": pixel_scale_arcsec,
        "grid_dimensions": {"width": shape_out[1], "height": shape_out[0]},
        "gaia_count": len(projected_gaia),
        "allwise_count": len(projected_allwise),
        "gaia_sources": projected_gaia,
        "allwise_sources": projected_allwise,
    }
