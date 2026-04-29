import math
import os
import struct
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np


def nn_distances(query: np.ndarray, ref: np.ndarray, chunk: int = 512) -> np.ndarray:
    Q = query.shape[0]
    dist2 = np.empty(Q, dtype=np.float64)
    for s in range(0, Q, chunk):
        e   = min(s + chunk, Q)
        d   = query[s:e, None, :] - ref[None, :, :]    
        dist2[s:e] = (d ** 2).sum(axis=2).min(axis=1)  
    return dist2


def nn_indices(query: np.ndarray, ref: np.ndarray, chunk: int = 256) -> np.ndarray:
    Q = query.shape[0]
    idx = np.empty(Q, dtype=np.int64)
    for s in range(0, Q, chunk):
        e  = min(s + chunk, Q)
        d  = query[s:e, None, :] - ref[None, :, :]     # (C, R, 3)
        idx[s:e] = (d ** 2).sum(axis=2).argmin(axis=1) # (C,)
    return idx


def d1_psnr(original: dict, reconstructed: dict, peak: Optional[float] = None) -> dict:
    xyz_o = original["xyz"]
    xyz_r = reconstructed["xyz"]

    if xyz_r.shape[0] == 0:
        nan = float("nan")
        return dict(fwd_psnr=nan, bwd_psnr=nan, sym_psnr=nan,
                    fwd_mse=nan, bwd_mse=nan, sym_mse=nan)

    if peak is None:
        ext = xyz_o.max(axis=0) - xyz_o.min(axis=0)
        peak = float(np.linalg.norm(ext))
    peak = max(peak, 1e-12)

    mse_fwd = float(nn_distances(xyz_o, xyz_r).mean())
    mse_bwd = float(nn_distances(xyz_r, xyz_o).mean())
    mse_sym = max(mse_fwd, mse_bwd)

    def to_db(mse):
        return 10.0 * math.log10(peak**2 / mse) if mse > 0 else math.inf

    return dict(
        fwd_psnr=to_db(mse_fwd), bwd_psnr=to_db(mse_bwd), sym_psnr=to_db(mse_sym),
        fwd_mse=mse_fwd,         bwd_mse=mse_bwd,          sym_mse=mse_sym,
    )


def d2_psnr(original: dict, reconstructed: dict, peak: Optional[float] = None) -> dict:
    xyz_o = original["xyz"]
    xyz_r = reconstructed["xyz"]
    n_o   = original.get("normal")
    n_r   = reconstructed.get("normal")

    if n_o is None and n_r is None:
        return d1_psnr(original, reconstructed, peak=peak)

    if peak is None:
        ext = xyz_o.max(axis=0) - xyz_o.min(axis=0)
        peak = max(float(np.linalg.norm(ext)), 1e-12)

    def _proj_mse(query_xyz, ref_xyz, ref_normals):
        idx   = nn_indices(query_xyz, ref_xyz)    # (Q,)
        nn_pts = ref_xyz[idx]                     # (Q, 3)
        nn_n   = ref_normals[idx]                 # (Q, 3)
        # normalise normals
        nrm = np.linalg.norm(nn_n, axis=1, keepdims=True)
        nrm = np.where(nrm > 1e-12, nrm, 1.0)
        nn_n /= nrm
        diff = query_xyz - nn_pts                 # (Q, 3)
        proj = (diff * nn_n).sum(axis=1)          # (Q,)
        return float((proj**2).mean())

    normals_for_fwd = n_r if n_r is not None else n_o
    normals_for_bwd = n_o if n_o is not None else n_r

    mse_fwd = _proj_mse(xyz_o, xyz_r, normals_for_fwd)
    mse_bwd = _proj_mse(xyz_r, xyz_o, normals_for_bwd)
    mse_sym = max(mse_fwd, mse_bwd)

    def to_db(mse):
        return 10.0 * math.log10(peak**2 / mse) if mse > 0 else math.inf

    return dict(
        fwd_psnr=to_db(mse_fwd), bwd_psnr=to_db(mse_bwd), sym_psnr=to_db(mse_sym),
        fwd_mse=mse_fwd,         bwd_mse=mse_bwd,          sym_mse=mse_sym,
    )


def temporal_stats(psnr_sequence: list[float]) -> dict:
    arr = np.array([v for v in psnr_sequence if math.isfinite(v)])
    if arr.size == 0:
        return {k: float("nan") for k in
                ["mean","std","min","max","range","tfi","consistency_ratio"]}
    tfi = float(np.abs(np.diff(arr)).mean()) if arr.size > 1 else 0.0
    mu, sigma = arr.mean(), arr.std()
    consistency = float((np.abs(arr - mu) <= sigma).mean())
    return dict(mean=float(mu),   std=float(sigma),
                min=float(arr.min()), max=float(arr.max()),
                range=float(arr.max()-arr.min()),
                tfi=tfi, consistency_ratio=consistency)


def inter_frame_chamfer(frames: list[dict]) -> list[float]:
    result = []
    for i in range(len(frames)-1):
        a, b = frames[i]["xyz"], frames[i+1]["xyz"]
        if a.shape[0] == 0 or b.shape[0] == 0:
            result.append(float("nan"))
            continue
        d_ab = np.sqrt(nn_distances(a, b)).mean()
        d_ba = np.sqrt(nn_distances(b, a)).mean()
        result.append(float((d_ab + d_ba) / 2.0))
    return result


class SimQuantCodec:
    MAGIC = b"SQC1"

    def __init__(self, geom_bits: int = 10, color_bits: int = 8):
        assert 1 <= geom_bits  <= 16, "geom_bits must be in [1, 16]"
        assert 1 <= color_bits <=  8, "color_bits must be in [1, 8]"
        self.geom_bits  = geom_bits
        self.color_bits = color_bits
        self.name       = f"simquant_g{geom_bits}"

    def compress_eval(self, original: dict, preprocessed: dict, peak: Optional[float] = None) -> dict:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "stream.sqc"
            t0 = time.perf_counter()
            self._encode(preprocessed, path)
            enc_t = time.perf_counter() - t0

            t0 = time.perf_counter()
            rec = self._decode(path)
            dec_t = time.perf_counter() - t0

            size_bytes = os.path.getsize(path)

        n_orig = original["xyz"].shape[0]
        bpp    = (size_bytes * 8) / max(n_orig, 1)

        return dict(
            bpp       = bpp,
            d1        = d1_psnr(original, rec, peak=peak),
            d2        = d2_psnr(original, rec, peak=peak),
            encode_s  = enc_t,
            decode_s  = dec_t,
            size_bytes= size_bytes,
            reconstructed = rec,
        )


    def _encode(self, pc: dict, path: Path) -> None:
        xyz = pc["xyz"]
        rgb = pc.get("rgb")
        n   = xyz.shape[0]
        g_levels = (1 << self.geom_bits)  - 1
        c_levels = (1 << self.color_bits) - 1

        xyz_min  = xyz.min(axis=0)
        extents  = xyz.max(axis=0) - xyz_min
        extents  = np.where(extents < 1e-12, 1.0, extents)

        xyz_q = np.round((xyz - xyz_min) / extents * g_levels).astype(np.uint16).clip(0, g_levels)
        has_rgb = 1 if rgb is not None else 0

        with open(path, "wb") as f:
            f.write(self.MAGIC)
            f.write(struct.pack("<IbbB", n, self.geom_bits, self.color_bits, has_rgb))
            f.write(struct.pack("<ddd", *xyz_min))
            f.write(struct.pack("<ddd", *extents))
            f.write(xyz_q.astype("<u2").tobytes())
            if has_rgb:
                c_q = np.round(rgb / 255.0 * c_levels).astype(np.uint8).clip(0, c_levels)
                f.write(c_q.tobytes())


    def _decode(self, path: Path) -> dict:
        from preprocessing import make_pc
        with open(path, "rb") as f:
            assert f.read(4) == self.MAGIC, "Bad magic"
            n, geom_bits, color_bits, has_rgb = struct.unpack("<IbbB", f.read(7))
            xyz_min  = np.array(struct.unpack("<ddd", f.read(24)))
            extents  = np.array(struct.unpack("<ddd", f.read(24)))
            g_levels = (1 << geom_bits)  - 1
            c_levels = (1 << color_bits) - 1
            xyz_q    = np.frombuffer(f.read(n*3*2), dtype="<u2").reshape(n,3).astype(np.float64)
            xyz      = xyz_q / g_levels * extents + xyz_min
            rgb      = None
            if has_rgb:
                c_q = np.frombuffer(f.read(n*3), dtype=np.uint8).reshape(n,3)
                rgb = np.round(c_q / c_levels * 255).astype(np.uint8)
        return make_pc(xyz, rgb=rgb)
