import io
import json
import os
import sys
import tempfile
import threading
import time
import traceback
import uuid
from pathlib import Path
from queue import Empty, Queue

import numpy as np
from flask import Flask, Response, jsonify, render_template, request

sys.path.insert(0, str(Path(__file__).parent.parent))
from preprocessing import (
    make_pc, preprocess, normalize_geometry,
    voxel_downsample, radius_downsample, mls_smooth,
)
from evaluation import d1_psnr, d2_psnr, temporal_stats, inter_frame_chamfer

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024  # 512 MB upload limit

jobs: dict[str, dict] = {}



def _parse_ply(data: bytes) -> dict:
    """
    Parse a PLY file from raw bytes.
    """
    import struct as _s
    buf = io.BytesIO(data)

    assert buf.readline().strip() == b"ply", "Not a PLY file"
    fmt, props, n = None, [], 0
    while True:
        line = buf.readline().decode("ascii", errors="replace").strip()
        if line == "end_header":
            break
        tok = line.split()
        if tok[0] == "format":
            fmt = tok[1]
        elif tok[0] == "element" and tok[1] == "vertex":
            n = int(tok[2])
        elif tok[0] == "property" and len(tok) == 3:
            props.append((tok[1], tok[2]))

    _dtypes = {
        "float": ("<f4", 4), "float32": ("<f4", 4),
        "double": ("<f8", 8), "float64": ("<f8", 8),
        "uchar": ("u1", 1), "uint8": ("u1", 1),
        "int": ("<i4", 4), "int32": ("<i4", 4),
        "uint": ("<u4", 4), "uint32": ("<u4", 4),
    }
    names = [p[1] for p in props]
    endian = "<" if fmt != "binary_big_endian" else ">"
    row_size = sum(_dtypes[p[0]][1] for p in props)

    if fmt == "ascii":
        rows = [list(map(float, buf.readline().split())) for _ in range(n)]
        data_cols = {nm: np.array([r[i] for r in rows]) for i, nm in enumerate(names)}
    else:
        raw = buf.read(n * row_size)
        dtype = np.dtype([(nm, _dtypes[tp][0]) for tp, nm in props])
        arr = np.frombuffer(raw, dtype=dtype, count=n)
        data_cols = {nm: arr[nm] for nm in names}

    xyz = np.stack([
        data_cols["x"].astype(np.float64),
        data_cols["y"].astype(np.float64),
        data_cols["z"].astype(np.float64),
    ], axis=1)

    rgb = None
    r = data_cols.get("red",   data_cols.get("r"))
    g = data_cols.get("green", data_cols.get("g"))
    b = data_cols.get("blue",  data_cols.get("b"))
    if r is not None and g is not None and b is not None:
        rgb = np.stack([r, g, b], axis=1).astype(np.uint8)

    return make_pc(xyz, rgb=rgb)


def _pc_to_json(pc: dict, max_points: int = 20_000) -> dict:
    """
    Serialise a point cloud to a plain JSON dict for the 3-D viewer.
    """
    xyz = pc["xyz"]
    rgb = pc.get("rgb")
    n   = xyz.shape[0]

    if n > max_points:
        idx = np.random.default_rng(0).choice(n, max_points, replace=False)
        xyz = xyz[idx]
        rgb = rgb[idx] if rgb is not None else None

    return {
        "xyz": xyz.tolist(),
        "rgb": rgb.tolist() if rgb is not None else None,
        "n":   int(xyz.shape[0]),
    }

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files uploaded"}), 400

    files = sorted(files, key=lambda f: f.filename)

    frames = []
    names = []
    for f in files:
        if not f.filename.lower().endswith(".ply"):
            return jsonify({"error": f"Only PLY files accepted, got: {f.filename}"}), 400
        try:
            pc = _parse_ply(f.read())
            frames.append(pc)
            names.append(f.filename)
        except Exception as e:
            return jsonify({"error": f"Failed to parse {f.filename}: {e}"}), 400

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        "status": "idle",
        "queue": Queue(),
        "results": [],
        "frames": frames,
        "names": names,
    }

    preview = _pc_to_json(frames[0])

    xyz = frames[0]["xyz"]
    ext = xyz.max(axis=0) - xyz.min(axis=0)
    coord_range = float(np.linalg.norm(ext))

    return jsonify({
        "job_id": job_id,
        "n_frames": len(frames),
        "filenames": names,
        "pts_frame": frames[0]["xyz"].shape[0],
        "has_color": frames[0].get("rgb") is not None,
        "preview": preview,
        "coord_range": coord_range,
    })


@app.route("/preprocess", methods=["POST"])
def preprocess_route():
    body   = request.get_json()
    job_id = body.get("job_id")
    if job_id not in jobs:
        return jsonify({"error": "Unknown job_id"}), 404

    frames = jobs[job_id]["frames"]
    frame  = frames[0]   

    steps_applied = []

    pc = {k: (v.copy() if isinstance(v, np.ndarray) else v) for k, v in frame.items()}

    if body.get("normalize", False):
        pc = normalize_geometry(
            pc,
            target_range=float(body.get("target_range", 1.0)),
            align_pca=bool(body.get("pca_align", True)),
        )
        steps_applied.append(
            f"Normalised: centred + scaled to [{body.get('target_range', 1.0)} unit box]"
            + (" + PCA aligned" if body.get("pca_align") else "")
        )

    density = body.get("density")
    pts_before = pc["xyz"].shape[0]
    if density == "voxel":
        pc = voxel_downsample(pc, voxel_size=float(body.get("voxel_size", 0.025)))
        steps_applied.append(
            f"Voxel downsample: {pts_before:,} → {pc['xyz'].shape[0]:,} pts "
            f"(voxel size = {body.get('voxel_size', 0.025)})"
        )
    elif density == "radius":
        pc = radius_downsample(pc, radius=float(body.get("radius", 0.025)))
        steps_applied.append(
            f"Radius downsample: {pts_before:,} → {pc['xyz'].shape[0]:,} pts "
            f"(radius = {body.get('radius', 0.025)})"
        )

    if body.get("smooth", False):
        pc = mls_smooth(
            pc,
            radius=float(body.get("mls_radius", 0.05)),
            poly_degree=int(body.get("mls_degree", 2)),
            n_iter=int(body.get("mls_iter", 1)),
        )
        steps_applied.append(
            f"MLS smooth: radius={body.get('mls_radius', 0.05)}, "
            f"degree={body.get('mls_degree', 2)}, "
            f"iterations={body.get('mls_iter', 1)}"
        )

    jobs[job_id]["preprocess_kwargs"] = {
        k: v for k, v in body.items() if k != "job_id"
    }

    pts_before_total = int(frame["xyz"].shape[0])
    pts_after_total = int(pc["xyz"].shape[0])

    return jsonify({
        "before": _pc_to_json(frame),
        "after": _pc_to_json(pc),
        "steps_applied": steps_applied,
        "pts_before": pts_before_total,
        "pts_after": pts_after_total,
        "pts_removed": max(0, pts_before_total - pts_after_total),
    })


@app.route("/run", methods=["POST"])
def run_job():
    body   = request.get_json()
    job_id = body.get("job_id")
    if job_id not in jobs:
        return jsonify({"error": "Unknown job_id"}), 404

    codec_configs = body.get("codecs", [])
    if not codec_configs:
        return jsonify({"error": "No codecs specified"}), 400

    jobs[job_id]["status"] = "running"

    thread = threading.Thread(
        target=_run_experiment,
        args=(job_id, codec_configs),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/stream/<job_id>")
def stream(job_id: str):
    if job_id not in jobs:
        return Response("job not found", status=404)

    def event_generator():
        q = jobs[job_id]["queue"]
        while True:
            try:
                event = q.get(timeout=30)
                yield f"data: {json.dumps(event)}\n\n"


                if event.get("type") in ("done", "error"):
                    break
            except Empty:
                # Heartbeat so the connection doesn't time out
                yield ": heartbeat"



    return Response(event_generator(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


@app.route("/result/<job_id>")
def result(job_id: str):
    if job_id not in jobs:
        return jsonify({"error": "Unknown job_id"}), 404
    return jsonify({
        "status":  jobs[job_id]["status"],
        "results": jobs[job_id]["results"],
    })


def _run_experiment(job_id: str, codec_configs: list[dict]) -> None:
    q = jobs[job_id]["queue"]
    frames = jobs[job_id]["frames"]
    pp_kw = jobs[job_id].get("preprocess_kwargs", {})
    results = []

    def emit(event: dict):
        q.put(event)

    try:
        from pc_codecs import GPCCCodec, VPCCCodec, DracoCodec

        pipeline_kw = {
            k: v for k, v in pp_kw.items()
            if k in (
                "normalize", "target_range", "pca_align",
                "density", "voxel_size", "radius",
                "smooth", "mls_radius", "mls_degree", "mls_iter"
            )
        }

        for cfg in codec_configs:
            codec_name = cfg.get("name", "").lower()
            codec_kw = {k: v for k, v in cfg.items() if k != "name"}

            emit({
                "type": "progress",
                "message": f"Starting {codec_name.upper()} ({', '.join(f'{k}={v}' for k, v in codec_kw.items())})"
            })

            try:
                if codec_name == "gpcc":
                    codec = GPCCCodec(**codec_kw)
                elif codec_name == "vpcc":
                    codec = VPCCCodec(**codec_kw)
                elif codec_name == "draco":
                    codec = DracoCodec(**codec_kw)
                else:
                    emit({"type": "error", "message": f"Unknown codec: {codec_name}"})
                    continue
            except Exception as e:
                emit({"type": "error", "message": f"{codec_name.upper()} init failed: {e}"})
                continue

            frame_d1 = []
            frame_d2 = []
            frame_bpp = []
            frame_enc_s = []
            frame_dec_s = []

            for fi, frame in enumerate(frames):
                emit({
                    "type": "progress",
                    "message": f"{codec_name.upper()} — frame {fi+1}/{len(frames)}"
                })

                pp = {k: (v.copy() if isinstance(v, np.ndarray) else v) for k, v in frame.items()}
                pp = preprocess(pp, **pipeline_kw)

                try:
                    res = codec.compress_eval(frame, pp)
                except Exception as e:
                    emit({
                        "type": "error",
                        "message": f"{codec_name.upper()} failed on frame {fi+1}: {e}"
                    })
                    raise

                frame_d1.append(float(res.get("d1_psnr", res["d1"]["sym_psnr"])))
                frame_d2.append(float(res.get("d2_psnr", res["d2"]["sym_psnr"])))
                frame_bpp.append(float(res["bpp"]))
                frame_enc_s.append(float(res.get("encode_s", 0.0)))
                frame_dec_s.append(float(res.get("decode_s", 0.0)))

            d1_mean = float(np.mean(frame_d1)) if frame_d1 else float("nan")
            d1_std = float(np.std(frame_d1)) if frame_d1 else float("nan")
            d2_mean = float(np.mean(frame_d2)) if frame_d2 else float("nan")

            row = {
                "codec": codec_name.upper(),
                "params": codec_kw,
                "bpp": round(float(np.mean(frame_bpp)), 3) if frame_bpp else None,
                "d1_psnr": round(d1_mean, 2) if np.isfinite(d1_mean) else None,
                "d1_std": round(d1_std, 2) if np.isfinite(d1_std) else None,
                "d2_psnr": round(d2_mean, 2) if np.isfinite(d2_mean) else None,
                "tfi": None,
                "consistency": None,
                "ifc_mean": None,
                "n_frames": len(frames),
                "encode_s": round(float(np.mean(frame_enc_s)), 4) if frame_enc_s else 0.0,
                "decode_s": round(float(np.mean(frame_dec_s)), 4) if frame_dec_s else 0.0,
            }

            results.append(row)
            emit({"type": "result", **row})

        jobs[job_id]["results"] = results
        jobs[job_id]["status"] = "done"
        emit({"type": "done", "n_results": len(results)})

    except Exception:
        jobs[job_id]["status"] = "error"
        emit({"type": "error", "message": traceback.format_exc()})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)