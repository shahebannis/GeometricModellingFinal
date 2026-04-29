import numpy as np
from typing import Optional
from scipy.spatial import cKDTree


def normalize_geometry(pc: dict, target_range: float = 1.0, align_pca: bool = True) -> dict:
    xyz = pc["xyz"].astype(np.float64)

    centroid = xyz.mean(axis=0)                      # shape (3,)
    xyz = xyz - centroid

    aabb_min, aabb_max = xyz.min(axis=0), xyz.max(axis=0)
    max_extent = (aabb_max - aabb_min).max()
    scale = target_range / max_extent if max_extent > 1e-12 else 1.0
    xyz = xyz * scale

    meta = {"centroid": centroid, "scale": scale, "pca_R": None}

    if align_pca:
        n = xyz.shape[0]
        C = (xyz.T @ xyz) / n
        eigenvalues, eigenvectors = np.linalg.eigh(C)
        order = np.argsort(eigenvalues)[::-1]
        eigenvectors = eigenvectors[:, order]

        R = eigenvectors.T                   
        if np.linalg.det(R) < 0:
            R[2] *= -1

        xyz = xyz @ R.T
        meta["pca_R"] = R

    out = _copy(pc)
    out["xyz"] = xyz
    out["_meta"] = meta

    if pc.get("normal") is not None and meta["pca_R"] is not None:
        out["normal"] = pc["normal"] @ meta["pca_R"].T

    return out


def voxel_downsample(pc: dict, voxel_size: float, method: str = "centroid") -> dict:
    if voxel_size <= 0:
        raise ValueError("voxel_size must be positive")
    xyz = pc["xyz"]
    n = xyz.shape[0]

    xyz_min = xyz.min(axis=0)
    g = np.floor((xyz - xyz_min) / voxel_size).astype(np.int64)   
    W = int(g[:, 0].max()) + 1
    H = int(g[:, 1].max()) + 1
    linear = g[:, 0] + W * g[:, 1] + W * H * g[:, 2]

    order = np.argsort(linear, kind="stable")
    lin_s  = linear[order]
    xyz_s  = xyz[order]
    rgb_s  = pc["rgb"][order]  if pc.get("rgb")    is not None else None
    norm_s = pc["normal"][order] if pc.get("normal") is not None else None

    _, first, counts = np.unique(lin_s, return_index=True, return_counts=True)
    K = len(first)

    out_xyz    = np.empty((K, 3), dtype=np.float64)
    out_rgb    = np.empty((K, 3), dtype=np.uint8)    if rgb_s  is not None else None
    out_normal = np.empty((K, 3), dtype=np.float64)  if norm_s is not None else None

    rng = np.random.default_rng(0)

    for k in range(K):
        s, c = first[k], counts[k]
        e = s + c
        if method == "centroid":
            out_xyz[k] = xyz_s[s:e].mean(axis=0)
            if rgb_s  is not None:
                out_rgb[k] = rgb_s[s:e].astype(np.float64).mean(axis=0).astype(np.uint8)
            if norm_s is not None:
                avg = norm_s[s:e].mean(axis=0)
                nrm = np.linalg.norm(avg)
                out_normal[k] = avg / nrm if nrm > 1e-12 else avg
        else:
            pick = s + int(rng.integers(0, c))
            out_xyz[k]    = xyz_s[pick]
            if rgb_s  is not None: out_rgb[k]    = rgb_s[pick]
            if norm_s is not None: out_normal[k] = norm_s[pick]

    return {
        "xyz": out_xyz,
        "rgb": out_rgb,
        "normal": out_normal,
        "_meta": (pc.get("_meta") or {}) | {
            "voxel_size": voxel_size,
            "pts_before": n,
            "pts_after":  K,
        },
    }



def radius_downsample(pc: dict, radius: float, seed: int = 0) -> dict:
    if radius <= 0:
        raise ValueError("radius must be positive")
    xyz = pc["xyz"]
    n   = xyz.shape[0]

    xyz_min = xyz.min(axis=0)
    def _key(p):
        return tuple(np.floor((p - xyz_min) / radius).astype(np.int64).tolist())

    grid: dict[tuple, list] = {}
    for i in range(n):
        k = _key(xyz[i])
        grid.setdefault(k, []).append(i)

    rng        = np.random.default_rng(seed)
    order      = rng.permutation(n)
    suppressed = np.zeros(n, dtype=bool)
    selected   = np.zeros(n, dtype=bool)
    r2         = radius * radius

    for i in order:
        if suppressed[i]:
            continue
        selected[i] = True
        ki, kj, kk = _key(xyz[i])
        for di in range(-1, 2):
            for dj in range(-1, 2):
                for dk in range(-1, 2):
                    cell = (ki+di, kj+dj, kk+dk)
                    if cell not in grid:
                        continue
                    for j in grid[cell]:
                        if not suppressed[j] and j != i:
                            diff = xyz[j] - xyz[i]
                            if diff @ diff <= r2:
                                suppressed[j] = True

    idx = np.where(selected)[0]
    has_rgb    = pc.get("rgb")    is not None
    has_normal = pc.get("normal") is not None
    return {
        "xyz":    xyz[idx],
        "rgb":    pc["rgb"][idx]    if has_rgb    else None,
        "normal": pc["normal"][idx] if has_normal else None,
        "_meta":  (pc.get("_meta") or {}) | {
            "radius_sample": radius,
            "pts_before": n,
            "pts_after":  int(selected.sum()),
        },
    }


def mls_smooth(pc: dict,
               radius: float,
               poly_degree: int = 2,
               weight_fn: str = "gaussian",
               n_iter: int = 1) -> dict:
    if radius <= 0:
        raise ValueError("radius must be positive")
    if poly_degree not in (0, 1, 2):
        raise NotImplementedError("Only poly_degree 0, 1, and 2 are implemented")

    out = _copy(pc)

    for iteration in range(n_iter):
        out = _mls_pass(out, radius, poly_degree, weight_fn)

    out["_meta"] = (pc.get("_meta") or {}) | {
        "mls_radius":  radius,
        "mls_degree":  poly_degree,
        "mls_weight":  weight_fn,
        "mls_iter":    n_iter,
    }
    return out




def _build_local_frame(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = normal / (np.linalg.norm(normal) + 1e-30)

    if abs(n[0]) < 0.9:
        a = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    else:
        a = np.array([0.0, 1.0, 0.0], dtype=np.float64)

    u = np.cross(n, a)
    u /= (np.linalg.norm(u) + 1e-30)

    v = np.cross(n, u)
    v /= (np.linalg.norm(v) + 1e-30)

    return u, v, n


def _poly_terms(x: np.ndarray, y: np.ndarray, degree: int) -> np.ndarray:
    if degree == 0:
        return np.column_stack([
            np.ones_like(x)
        ])
    elif degree == 1:
        return np.column_stack([
            np.ones_like(x),
            x,
            y
        ])
    elif degree == 2:
        return np.column_stack([
            np.ones_like(x),
            x,
            y,
            x * x,
            x * y,
            y * y
        ])
    else:
        raise NotImplementedError("Only degree 0, 1, 2 supported")


def _weighted_least_squares(A: np.ndarray, z: np.ndarray, w: np.ndarray) -> np.ndarray:
    sqrt_w = np.sqrt(w + 1e-30)
    Aw = A * sqrt_w[:, None]
    zw = z * sqrt_w
    coeff, *_ = np.linalg.lstsq(Aw, zw, rcond=None)
    return coeff


def _eval_poly(coeff: np.ndarray, x: float, y: float, degree: int) -> float:
    if degree == 0:
        return coeff[0]
    elif degree == 1:
        return coeff[0] + coeff[1] * x + coeff[2] * y
    elif degree == 2:
        return (
            coeff[0]
            + coeff[1] * x
            + coeff[2] * y
            + coeff[3] * x * x
            + coeff[4] * x * y
            + coeff[5] * y * y
        )
    else:
        raise NotImplementedError("Only degree 0, 1, 2 supported")


def _mls_pass(pc: dict, radius: float, poly_degree: int, weight_fn: str) -> dict:
    xyz = pc["xyz"]
    n = xyz.shape[0]
    has_rgb = pc.get("rgb") is not None
    xyz_new = xyz.copy()
    rgb_new = pc["rgb"].astype(np.float64).copy() if has_rgb else None
    r2 = radius * radius

    tree = cKDTree(xyz)
    neighbor_lists = tree.query_ball_point(xyz, r=radius)

    for i in range(n):
        nbr_valid = np.asarray(neighbor_lists[i], dtype=np.int64)
        if nbr_valid.size == 0:
            continue

        pts = xyz[nbr_valid]
        diffs = pts - xyz[i]
        dist2 = (diffs ** 2).sum(axis=1)

        if weight_fn == "gaussian":
            w = np.exp(-dist2 / (r2 + 1e-30))
        else:
            d = np.sqrt(dist2)
            t = np.clip(1.0 - d / (radius + 1e-30), 0.0, 1.0)
            w = (t ** 4) * (1.0 + 4.0 * d / (radius + 1e-30))

        w_sum = w.sum()
        if w_sum < 1e-12:
            continue

        min_pts = 3 if poly_degree == 0 else 6 if poly_degree == 1 else 10
        if pts.shape[0] < min_pts:
            continue

        mu = (w[:, None] * pts).sum(axis=0) / w_sum

        d2 = pts - mu
        C = (w[:, None] * d2).T @ d2 / w_sum
        eigenvalues, eigenvectors = np.linalg.eigh(C)
        normal = eigenvectors[:, 0]

        u, v, nrm = _build_local_frame(normal)

        rel = pts - mu
        x = rel @ u
        y = rel @ v
        z = rel @ nrm

        A = _poly_terms(x, y, poly_degree)
        coeff = _weighted_least_squares(A, z, w)

        q_rel = xyz[i] - mu
        qx = q_rel @ u
        qy = q_rel @ v
        qz_new = _eval_poly(coeff, qx, qy, poly_degree)

        xyz_new[i] = mu + qx * u + qy * v + qz_new * nrm

        if has_rgb:
            rgb_nb = pc["rgb"][nbr_valid].astype(np.float64)
            rgb_new[i] = (w[:, None] * rgb_nb).sum(axis=0) / w_sum

    return {
        "xyz":    xyz_new,
        "rgb":    np.clip(rgb_new, 0, 255).astype(np.uint8) if has_rgb else None,
        "normal": pc.get("normal"),
        "_meta":  dict(pc.get("_meta") or {}),
    }



def preprocess(pc: dict,
               normalize:        bool  = True,
               target_range:     float = 1.0,
               pca_align:        bool  = True,
               density:          str   = "voxel", 
               voxel_size:       float = 0.02,
               radius:           float = 0.02,
               smooth:           bool  = True,
               mls_radius:       float = 0.04,
               mls_degree:       int   = 1,
               mls_iter:         int   = 1) -> dict:
    if normalize:
        pc = normalize_geometry(pc, target_range=target_range, align_pca=pca_align)
    if density == "voxel":
        pc = voxel_downsample(pc, voxel_size=voxel_size)
    elif density == "radius":
        pc = radius_downsample(pc, radius=radius)
    if smooth:
        pc = mls_smooth(pc, radius=mls_radius, poly_degree=mls_degree, n_iter=mls_iter)
    return pc


def preprocess_sequence(frames: list[dict], **kwargs) -> list[dict]:
    if not frames:
        return []

    normalize  = kwargs.get("normalize", True)
    target_range = kwargs.get("target_range", 1.0)
    pca_align  = kwargs.get("pca_align", True)

    ref_centroid, ref_scale = None, None
    if normalize:
        ref_xyz = frames[0]["xyz"].astype(np.float64)
        ref_centroid = ref_xyz.mean(axis=0)
        centered = ref_xyz - ref_centroid
        max_extent = (centered.max(axis=0) - centered.min(axis=0)).max()
        ref_scale = target_range / max_extent if max_extent > 1e-12 else 1.0

    out_frames = []
    for frame in frames:
        pc = {k: (v.copy() if isinstance(v, np.ndarray) else v)
              for k, v in frame.items()}

        if normalize and ref_centroid is not None:
            pc["xyz"] = (frame["xyz"].astype(np.float64) - ref_centroid) * ref_scale
            if pca_align:
                pc = _pca_align_only(pc)
            kwargs2 = dict(kwargs)
            kwargs2["normalize"] = False
        else:
            kwargs2 = dict(kwargs)

        pc = preprocess(pc, **kwargs2)
        out_frames.append(pc)

    return out_frames



def _copy(pc: dict) -> dict:
    return {
        "xyz":    pc["xyz"].copy(),
        "rgb":    pc["rgb"].copy()    if pc.get("rgb")    is not None else None,
        "normal": pc["normal"].copy() if pc.get("normal") is not None else None,
        "_meta":  dict(pc.get("_meta") or {}),
    }


def _pca_align_only(pc: dict) -> dict:
    xyz = pc["xyz"]
    n = xyz.shape[0]
    if n < 3:
        return pc
    centroid = xyz.mean(axis=0)
    C = ((xyz - centroid).T @ (xyz - centroid)) / n
    eigenvalues, eigenvectors = np.linalg.eigh(C)
    order = np.argsort(eigenvalues)[::-1]
    R = eigenvectors[:, order].T
    if np.linalg.det(R) < 0:
        R[2] *= -1
    out = _copy(pc)
    out["xyz"] = (xyz - centroid) @ R.T + centroid
    if pc.get("normal") is not None:
        out["normal"] = pc["normal"] @ R.T
    return out


def make_pc(xyz: np.ndarray,
            rgb: Optional[np.ndarray] = None,
            normal: Optional[np.ndarray] = None) -> dict:
    return {
        "xyz":    np.asarray(xyz,    dtype=np.float64),
        "rgb":    np.asarray(rgb,    dtype=np.uint8)   if rgb    is not None else None,
        "normal": np.asarray(normal, dtype=np.float64) if normal is not None else None,
        "_meta":  {},
    }


def synthetic_sequence(n_frames: int = 6,noise:float = 0.03,n_points: int = 3000,seed:int= 0) -> list[dict]:
    rng = np.random.default_rng(seed)

    def _blob(center, scale, n):
        pts = rng.standard_normal((n, 3)) * scale + center
        return pts

    frames = []
    for t in range(n_frames):
        parts = [
            _blob([0,  0,    0], [0.20, 0.40, 0.10], int(n_points * 0.45)),  # torso
            _blob([0,  0.55, 0], [0.12, 0.12, 0.12], int(n_points * 0.15)),  # head
            _blob([-0.12, -0.45, 0], [0.07, 0.28, 0.07], int(n_points * 0.14)),  # leg L
            _blob([ 0.12, -0.45, 0], [0.07, 0.28, 0.07], int(n_points * 0.14)),  # leg R
            _blob([-0.30,  0.10, 0], [0.22, 0.06, 0.06], int(n_points * 0.06)),  # arm L
            _blob([ 0.30,  0.10, 0], [0.22, 0.06, 0.06], int(n_points * 0.06)),  # arm R
        ]
        xyz = np.vstack(parts)
        xyz += rng.standard_normal(xyz.shape) * noise
        xyz += np.array([0.0, 0.0, t * 0.004])
        colors = [
            np.tile([180, 120,  80], (len(parts[0]), 1)),
            np.tile([220, 170, 130], (len(parts[1]), 1)),
            np.tile([100,  80, 200], (len(parts[2]), 1)),
            np.tile([100,  80, 200], (len(parts[3]), 1)),
            np.tile([ 80, 140,  80], (len(parts[4]), 1)),
            np.tile([ 80, 140,  80], (len(parts[5]), 1)),
        ]
        rgb = np.vstack(colors).clip(0, 255).astype(np.uint8)
        frames.append(make_pc(xyz, rgb=rgb))

    return frames
