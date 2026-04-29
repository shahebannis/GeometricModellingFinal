# import os
# import shutil
# import subprocess
# import tempfile
# import time
# from pathlib import Path
# from typing import Optional

# import numpy as np

# from evaluation import d1_psnr, d2_psnr

# def _write_ply_float(pc: dict, path: Path) -> None:
#     xyz = np.asarray(pc["xyz"], dtype=np.float32)
#     rgb = pc.get("rgb")
#     n = xyz.shape[0]

#     props = ["property float x", "property float y", "property float z"]
#     if rgb is not None:
#         rgb = np.asarray(rgb, dtype=np.uint8)
#         props += ["property uchar red", "property uchar green", "property uchar blue"]

#     header = (
#         "ply\nformat binary_little_endian 1.0\n"
#         f"element vertex {n}\n"
#         + "\n".join(props)
#         + "\nend_header\n"
#     )
#     with open(path, "wb") as f:
#         f.write(header.encode("ascii"))
#         if rgb is not None:
#             dtype = np.dtype(
#                 [("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("r", "u1"), ("g", "u1"), ("b", "u1")]
#             )
#             buf = np.empty(n, dtype=dtype)
#             buf["x"], buf["y"], buf["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
#             buf["r"], buf["g"], buf["b"] = rgb[:, 0], rgb[:, 1], rgb[:, 2]
#             f.write(buf.tobytes())
#         else:
#             f.write(xyz.astype("<f4", copy=False).tobytes())



# def _write_ply_int(pc: dict, path: Path, bits: int = 10) -> dict:
#     xyz = np.asarray(pc["xyz"], dtype=np.float64)
#     rgb = pc.get("rgb")
#     n = xyz.shape[0]

#     max_val = (1 << bits) - 1
#     xyz_min = xyz.min(axis=0)
#     xyz_max = xyz.max(axis=0)
#     extent = float((xyz_max - xyz_min).max())
#     if extent < 1e-12:
#         extent = 1.0

#     scale = max_val / extent
#     xyz_int = np.round((xyz - xyz_min) * scale).astype(np.int32)
#     xyz_int = np.clip(xyz_int, 0, max_val).astype(np.int32)

#     props = ["property int x", "property int y", "property int z"]
#     if rgb is not None:
#         rgb = np.asarray(rgb, dtype=np.uint8)
#         props += ["property uchar red", "property uchar green", "property uchar blue"]

#     header = (
#         "ply\nformat binary_little_endian 1.0\n"
#         f"element vertex {n}\n"
#         + "\n".join(props)
#         + "\nend_header\n"
#     )

#     with open(path, "wb") as f:
#         f.write(header.encode("ascii"))
#         if rgb is not None:
#             dtype = np.dtype(
#                 [("x", "<i4"), ("y", "<i4"), ("z", "<i4"),
#                  ("r", "u1"), ("g", "u1"), ("b", "u1")]
#             )
#             buf = np.empty(n, dtype=dtype)
#             buf["x"], buf["y"], buf["z"] = xyz_int[:, 0], xyz_int[:, 1], xyz_int[:, 2]
#             buf["r"], buf["g"], buf["b"] = rgb[:, 0], rgb[:, 1], rgb[:, 2]
#             f.write(buf.tobytes())
#         else:
#             f.write(xyz_int.astype("<i4", copy=False).tobytes())

#     return {"xyz_min": xyz_min, "scale": scale, "bits": bits}

# def _require_executable(path_or_name: str, label: str) -> str:
#     p = shutil.which(path_or_name)
#     if p:
#         return p

#     candidate = Path(path_or_name)
#     if candidate.is_file() and os.access(candidate, os.X_OK):
#         return str(candidate)

#     raise RuntimeError(f"{label} executable '{path_or_name}' not found or not executable.")

# def _maybe_make_pc(xyz: np.ndarray, rgb: Optional[np.ndarray] = None) -> dict:
#     try:
#         from preprocessing import make_pc

#         return make_pc(xyz, rgb=rgb)
#     except Exception:
#         out = {"xyz": np.asarray(xyz, dtype=np.float64)}
#         if rgb is not None:
#             out["rgb"] = np.asarray(rgb, dtype=np.uint8)
#         return out



# def _restore_from_quantized(pc: dict, meta: dict) -> dict:
#     xyz = np.asarray(pc["xyz"], dtype=np.float64)
#     restored_xyz = xyz / float(meta["scale"]) + np.asarray(meta["xyz_min"], dtype=np.float64)
#     return _maybe_make_pc(restored_xyz, rgb=pc.get("rgb"))



# def _read_ply(path: Path) -> dict:
#     with open(path, "rb") as f:
#         first = f.readline().strip()
#         if first != b"ply":
#             raise ValueError(f"Not a PLY file: {path}")

#         fmt, props, n = None, [], 0
#         while True:
#             line = f.readline().decode("ascii", errors="replace").strip()
#             if line == "end_header":
#                 break
#             if not line:
#                 continue
#             tok = line.split()
#             if tok[0] == "format":
#                 fmt = tok[1]
#             elif tok[0] == "element" and tok[1] == "vertex":
#                 n = int(tok[2])
#             elif tok[0] == "property" and len(tok) == 3:
#                 props.append((tok[1], tok[2]))

#         type_map = {
#             "float": ("<f4", ">f4", 4),
#             "float32": ("<f4", ">f4", 4),
#             "double": ("<f8", ">f8", 8),
#             "float64": ("<f8", ">f8", 8),
#             "uchar": ("u1", "u1", 1),
#             "uint8": ("u1", "u1", 1),
#             "char": ("i1", "i1", 1),
#             "int8": ("i1", "i1", 1),
#             "short": ("<i2", ">i2", 2),
#             "int16": ("<i2", ">i2", 2),
#             "ushort": ("<u2", ">u2", 2),
#             "uint16": ("<u2", ">u2", 2),
#             "int": ("<i4", ">i4", 4),
#             "int32": ("<i4", ">i4", 4),
#             "uint": ("<u4", ">u4", 4),
#             "uint32": ("<u4", ">u4", 4),
#         }

#         names = [p[1] for p in props]
#         row_size = sum(type_map[p[0]][2] for p in props)

#         if fmt == "ascii":
#             rows = [f.readline().decode("ascii", errors="replace").split() for _ in range(n)]
#             data = {nm: np.array([float(r[i]) for r in rows]) for i, nm in enumerate(names)}
#         else:
#             if fmt not in ("binary_little_endian", "binary_big_endian"):
#                 raise ValueError(f"Unsupported PLY format '{fmt}' in {path}")
#             little = fmt == "binary_little_endian"
#             raw = f.read(n * row_size)
#             dtype = np.dtype(
#                 [
#                     (nm, type_map[tp][0] if little else type_map[tp][1])
#                     for tp, nm in props
#                 ]
#             )
#             arr = np.frombuffer(raw, dtype=dtype, count=n)
#             data = {nm: arr[nm] for nm in names}

#     xyz = np.stack(
#         [
#             np.asarray(data["x"], dtype=np.float64),
#             np.asarray(data["y"], dtype=np.float64),
#             np.asarray(data["z"], dtype=np.float64),
#         ],
#         axis=1,
#     )
#     rgb = None
#     r = data.get("red", data.get("r"))
#     g = data.get("green", data.get("g"))
#     b = data.get("blue", data.get("b"))
#     if r is not None and g is not None and b is not None:
#         rgb = np.stack([r, g, b], axis=1).astype(np.uint8)
#     return _maybe_make_pc(xyz, rgb=rgb)



# def _run(cmd: list[str], label: str) -> str:
#     result = subprocess.run(cmd, capture_output=True, text=True)
#     if result.returncode != 0:
#         raise RuntimeError(
#             f"{label} failed (rc={result.returncode}).\n"
#             f"Command: {' '.join(str(c) for c in cmd)}\n"
#             f"stdout:\n{result.stdout[-2000:]}\n"
#             f"stderr:\n{result.stderr[-4000:]}"
#         )
#     return result.stdout


# class GPCCCodec:
#     name = "gpcc"
#     BINARY = os.environ.get("TMC3_BINARY", "tmc3")

#     def __init__(self, position_qscale: float = 1.0, input_bits: int = 10):
#         if not shutil.which(self.BINARY):
#             raise RuntimeError(
#                 f"'{self.BINARY}' not found on PATH. Set TMC3_BINARY or run inside the Docker container."
#             )
#         self.position_qscale = position_qscale
#         self.input_bits = input_bits

#     def compress_eval(self, original: dict, preprocessed: dict, peak: Optional[float] = None) -> dict:
#         with tempfile.TemporaryDirectory() as td:
#             r = self._round_trip(preprocessed, Path(td))

#         n = int(original["xyz"].shape[0])
#         d1 = d1_psnr(original, r["rec"], peak=peak)
#         d2 = d2_psnr(original, r["rec"], peak=peak)

#         return {
#             "bpp": (r["size_bytes"] * 8) / max(n, 1),
#             "d1": d1,
#             "d2": d2,
#             "d1_psnr": d1["sym_psnr"],
#             "d2_psnr": d2["sym_psnr"],
#             "encode_s": r["encode_s"],
#             "decode_s": r["decode_s"],
#             "size_bytes": r["size_bytes"],
#             "reconstructed": r["rec"],
#         }

#     def _round_trip(self, pc: dict, td: Path) -> dict:
#         ply_in = td / "in.ply"
#         bin_out = td / "stream.bin"
#         ply_out = td / "decoded.ply"

#         meta = _write_ply_int(pc, ply_in, bits=self.input_bits)

#         cmd = [
#             self.BINARY,
#             "--mode=0",
#             f"--uncompressedDataPath={ply_in}",
#             f"--compressedStreamPath={bin_out}",
#             f"--positionQuantizationScale={self.position_qscale}",
#         ]
#         if pc.get("rgb") is None:
#             cmd.append("--disableAttributeCoding=1")

#         t0 = time.perf_counter()
#         _run(cmd, "tmc3 encode")
#         encode_s = time.perf_counter() - t0

#         t0 = time.perf_counter()
#         _run(
#             [
#                 self.BINARY,
#                 "--mode=1",
#                 f"--compressedStreamPath={bin_out}",
#                 f"--reconstructedDataPath={ply_out}",
#             ],
#             "tmc3 decode",
#         )
#         decode_s = time.perf_counter() - t0

#         if not ply_out.exists():
#             raise RuntimeError("tmc3 decode finished but did not produce decoded.ply")

#         rec = _restore_from_quantized(_read_ply(ply_out), meta)
#         return {
#             "rec": rec,
#             "size_bytes": os.path.getsize(bin_out),
#             "encode_s": encode_s,
#             "decode_s": decode_s,
#         }


# class VPCCCodec:
#     name = "vpcc"
#     CFG_DIR = os.environ.get("TMC2_CFG_DIR", "/opt/tmc2/cfg")
#     ENC_BINARY = os.environ.get("TMC2_ENCODER", "PccAppEncoder")
#     DEC_BINARY = os.environ.get("TMC2_DECODER", "PccAppDecoder")

#     def __init__(self, geom_qp: int = 36, sequence_cfg: Optional[str] = None, input_bits: int = 10):
#         self.ENC_BINARY = _require_executable(
#             os.environ.get("TMC2_ENCODER", "PccAppEncoder"),
#             "TMC2 encoder"
#         )
#         self.DEC_BINARY = _require_executable(
#             os.environ.get("TMC2_DECODER", "PccAppDecoder"),
#             "TMC2 decoder"
#         )
#         self.geom_qp = geom_qp
#         self.sequence_cfg = sequence_cfg
#         self.input_bits = input_bits

#     def compress_eval(self, original: dict, preprocessed: dict, peak: Optional[float] = None) -> dict:
#         with tempfile.TemporaryDirectory() as td:
#             r = self._round_trip(preprocessed, Path(td))

#         n = int(original["xyz"].shape[0])
#         d1 = d1_psnr(original, r["rec"], peak=peak)
#         d2 = d2_psnr(original, r["rec"], peak=peak)

#         return {
#             "bpp": (r["size_bytes"] * 8) / max(n, 1),
#             "d1": d1,
#             "d2": d2,
#             "d1_psnr": d1["sym_psnr"],
#             "d2_psnr": d2["sym_psnr"],
#             "encode_s": r["encode_s"],
#             "decode_s": r["decode_s"],
#             "size_bytes": r["size_bytes"],
#             "reconstructed": r["rec"],
#         }

#     def _round_trip(self, pc: dict, td: Path) -> dict:
#         enc_dir = td / "enc"
#         dec_dir = td / "dec"
#         enc_dir.mkdir()
#         dec_dir.mkdir()

#         meta = _write_ply_int(pc, enc_dir / "frame_0000.ply", bits=self.input_bits)
#         bin_out = td / "stream.bin"

#         hm_enc = self._find_hm_encoder()
#         hm_dec = self._find_hm_decoder()
#         seq_cfg = self._find_sequence_cfg()

#         t0 = time.perf_counter()
#         _run(
#             [
#                 self.ENC_BINARY,
#                 f"--configurationFolder={self.CFG_DIR}/",
#                 f"--config={self.CFG_DIR}/common/ctc-common.cfg",
#                 f"--config={self.CFG_DIR}/condition/ctc-all-intra.cfg",
#                 f"--config={seq_cfg}",
#                 f"--config={self.CFG_DIR}/rate/ctc-r3.cfg",
#                 f"--uncompressedDataPath={enc_dir}/frame_%04d.ply",
#                 f"--compressedStreamPath={bin_out}",
#                 f"--reconstructedDataPath={dec_dir}/rec_%04d.ply",
#                 "--frameCount=1",
#                 "--startFrameNumber=0",
#                 f"--geometryQP={self.geom_qp}",
#                 f"--attributeQP={self.geom_qp + 2}",
#                 f"--videoEncoderGeometryPath={hm_enc}",
#                 f"--videoEncoderAttributePath={hm_enc}",
#                 f"--videoEncoderOccupancyPath={hm_enc}",
#             ],
#             "PccAppEncoder",
#         )
#         encode_s = time.perf_counter() - t0

#         t0 = time.perf_counter()
#         _run(
#             [
#                 self.DEC_BINARY,
#                 f"--compressedStreamPath={bin_out}",
#                 f"--reconstructedDataPath={dec_dir}/dec_%04d.ply",
#                 "--startFrameNumber=0",
#                 f"--videoDecoderGeometryPath={hm_dec}",
#                 f"--videoDecoderAttributePath={hm_dec}",
#                 f"--videoDecoderOccupancyPath={hm_dec}",
#             ],
#             "PccAppDecoder",
#         )
#         decode_s = time.perf_counter() - t0

#         rec_path = dec_dir / "dec_0000.ply"
#         if not rec_path.exists():
#             candidates = sorted(dec_dir.glob("*.ply"))
#             if not candidates:
#                 raise RuntimeError("PccAppDecoder produced no PLY output.")
#             rec_path = candidates[0]

#         rec = _restore_from_quantized(_read_ply(rec_path), meta)
#         return {
#             "rec": rec,
#             "size_bytes": os.path.getsize(bin_out),
#             "encode_s": encode_s,
#             "decode_s": decode_s,
#         }

#     def _find_sequence_cfg(self) -> str:
#         if self.sequence_cfg:
#             path = Path(self.sequence_cfg)
#             if not path.exists():
#                 raise RuntimeError(f"Requested VPCC sequence config does not exist: {path}")
#             return str(path)

#         candidates = [
#             Path(self.CFG_DIR) / "sequence" / "longdress_vox10.cfg",
#             Path(self.CFG_DIR) / "sequence" / "loot_vox10.cfg",
#             Path(self.CFG_DIR) / "sequence" / "redandblack_vox10.cfg",
#             Path(self.CFG_DIR) / "sequence" / "soldier_vox10.cfg",
#         ]
#         for candidate in candidates:
#             if candidate.exists():
#                 return str(candidate)

#         found = sorted((Path(self.CFG_DIR) / "sequence").glob("*.cfg")) if (Path(self.CFG_DIR) / "sequence").exists() else []
#         if found:
#             return str(found[0])
#         raise RuntimeError(
#             "Could not find a VPCC sequence config under CFG_DIR/sequence. "
#             "Pass sequence_cfg explicitly for your dataset."
#         )

#     def _find_hm_encoder(self) -> str:
#         return self._find_hm_binary("TAppEncoder")

#     def _find_hm_decoder(self) -> str:
#         return self._find_hm_binary("TAppDecoder")

#     def _find_hm_binary(self, name: str) -> str:
#         on_path = shutil.which(name)
#         if on_path:
#             return on_path

#         search_dirs = ["/opt/hm", "/opt/tmc2"]
#         candidates = []
#         for search_root in search_dirs:
#             result = subprocess.run(
#                 [
#                     "find",
#                     search_root,
#                     "-type",
#                     "f",
#                     "-name",
#                     f"{name}*",
#                     "-not",
#                     "-name",
#                     "*.o",
#                     "-not",
#                     "-name",
#                     "*.a",
#                     "-not",
#                     "-name",
#                     "*.cpp",
#                     "-not",
#                     "-name",
#                     "*.h",
#                     "-not",
#                     "-name",
#                     "*.vcxproj*",
#                     "-not",
#                     "-name",
#                     "*.filters",
#                     "-not",
#                     "-name",
#                     "*.cmake",
#                     "-not",
#                     "-path",
#                     "*/.git/*",
#                 ],
#                 capture_output=True,
#                 text=True,
#             )
#             for p in result.stdout.splitlines():
#                 p = p.strip()
#                 if not p or p in candidates:
#                     continue
#                 try:
#                     with open(p, "rb") as f:
#                         if f.read(4) == b"\x7fELF":
#                             candidates.append(p)
#                 except (OSError, PermissionError):
#                     pass
#             if candidates:
#                 break

#         if not candidates:
#             raise RuntimeError(
#                 f"HM binary '{name}' not found. Build HM in the container or add it to PATH."
#             )

#         candidates = [p for p in candidates if "Analyser" not in Path(p).name]
#         static = [p for p in candidates if "Static" in Path(p).name]
#         if static:
#             return static[0]
#         hbd = [p for p in candidates if "HighBitDepth" in Path(p).name]
#         return (hbd or candidates)[0]


# class DracoCodec:
#     name = "draco"
#     ENC_BINARY = os.environ.get("DRACO_ENCODER", "draco_encoder")
#     DEC_BINARY = os.environ.get("DRACO_DECODER", "draco_decoder")

#     def __init__(self, qp: int = 11, cl: int = 1):
#         self.ENC_BINARY = _require_executable(
#             os.environ.get("DRACO_ENCODER", "draco_encoder"),
#             "Draco encoder"
#         )
#         self.DEC_BINARY = _require_executable(
#             os.environ.get("DRACO_DECODER", "draco_decoder"),
#             "Draco decoder"
#         )
#         if not (1 <= qp <= 31):
#             raise ValueError("qp must be in [1, 31]")
#         if not (0 <= cl <= 10):
#             raise ValueError("cl must be in [0, 10]")
#         self.qp = qp
#         self.cl = cl

#     def compress_eval(self, original: dict, preprocessed: dict, peak: Optional[float] = None) -> dict:
#         with tempfile.TemporaryDirectory() as td:
#             r = self._round_trip(preprocessed, Path(td))

#         n = int(original["xyz"].shape[0])
#         d1 = d1_psnr(original, r["rec"], peak=peak)
#         d2 = d2_psnr(original, r["rec"], peak=peak)

#         return {
#             "bpp": (r["size_bytes"] * 8) / max(n, 1),
#             "d1": d1,
#             "d2": d2,
#             "d1_psnr": d1["sym_psnr"],
#             "d2_psnr": d2["sym_psnr"],
#             "encode_s": r["encode_s"],
#             "decode_s": r["decode_s"],
#             "size_bytes": r["size_bytes"],
#             "reconstructed": r["rec"],
#         }

#     def _round_trip(self, pc: dict, td: Path) -> dict:
#         ply_in = td / "in.ply"
#         drc_out = td / "stream.drc"
#         ply_out = td / "decoded.ply"

#         _write_ply_float(pc, ply_in)

#         t0 = time.perf_counter()
#         _run(
#             [
#                 self.ENC_BINARY,
#                 "-point_cloud",
#                 "-i",
#                 str(ply_in),
#                 "-o",
#                 str(drc_out),
#                 "-qp",
#                 str(self.qp),
#                 "-cl",
#                 str(self.cl),
#             ],
#             "draco_encoder",
#         )
#         encode_s = time.perf_counter() - t0

#         t0 = time.perf_counter()
#         _run([self.DEC_BINARY, "-i", str(drc_out), "-o", str(ply_out)], "draco_decoder")
#         decode_s = time.perf_counter() - t0

#         if not ply_out.exists():
#             raise RuntimeError("draco_decoder finished but did not produce decoded.ply")

#         rec = _read_ply(ply_out)
#         return {
#             "rec": rec,
#             "size_bytes": os.path.getsize(drc_out),
#             "encode_s": encode_s,
#             "decode_s": decode_s,
#         }


# CODECS = {"gpcc": GPCCCodec, "vpcc": VPCCCodec, "draco": DracoCodec}


# def get_codec(name: str, **kwargs):
#     cls = CODECS.get(name.lower())
#     if cls is None:
#         raise ValueError(f"Unknown codec '{name}'. Available: {list(CODECS)}")
#     return cls(**kwargs)

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

import numpy as np

from evaluation import d1_psnr, d2_psnr


def _run(cmd: list[str], label: str) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"{label} failed (rc={result.returncode}).\n"
            f"Command: {' '.join(str(c) for c in cmd)}\n"
            f"stdout:\n{result.stdout[-4000:]}\n"
            f"stderr:\n{result.stderr[-4000:]}"
        )
    return result.stdout


def _find_executable(path_or_name: str, extra_search_roots: Optional[list[str]] = None) -> str:
    p = shutil.which(path_or_name)
    if p:
        return p

    candidate = Path(path_or_name)
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)

    checked = [path_or_name]
    roots = extra_search_roots or []
    for root in roots:
        root_path = Path(root)
        if not root_path.exists():
            continue
        try:
            for found in root_path.rglob(Path(path_or_name).name):
                if found.is_file() and os.access(found, os.X_OK):
                    return str(found)
                checked.append(str(found))
        except Exception:
            pass

    raise RuntimeError(
        f"Executable not found or not executable: {path_or_name}. "
        f"Searched PATH and roots={roots}"
    )


def _maybe_make_pc(xyz: np.ndarray, rgb: Optional[np.ndarray] = None) -> dict:
    try:
        from preprocessing import make_pc
        return make_pc(xyz, rgb=rgb)
    except Exception:
        out = {"xyz": np.asarray(xyz, dtype=np.float64)}
        if rgb is not None:
            out["rgb"] = np.asarray(rgb, dtype=np.uint8)
        else:
            out["rgb"] = None
        out["normal"] = None
        out["_meta"] = {}
        return out


def _write_ply_float(pc: dict, path: Path) -> None:
    xyz = np.asarray(pc["xyz"], dtype=np.float32)
    rgb = pc.get("rgb")
    n = xyz.shape[0]

    props = ["property float x", "property float y", "property float z"]
    if rgb is not None:
        rgb = np.asarray(rgb, dtype=np.uint8)
        props += ["property uchar red", "property uchar green", "property uchar blue"]

    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"element vertex {n}\n"
        + "\n".join(props)
        + "\nend_header\n"
    )

    with open(path, "wb") as f:
        f.write(header.encode("ascii"))
        if rgb is not None:
            dtype = np.dtype(
                [("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
                 ("r", "u1"), ("g", "u1"), ("b", "u1")]
            )
            buf = np.empty(n, dtype=dtype)
            buf["x"], buf["y"], buf["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
            buf["r"], buf["g"], buf["b"] = rgb[:, 0], rgb[:, 1], rgb[:, 2]
            f.write(buf.tobytes())
        else:
            f.write(xyz.astype("<f4", copy=False).tobytes())


def _write_ply_int_ascii(pc: dict, path: Path, bits: int = 10) -> dict:
    xyz = np.asarray(pc["xyz"], dtype=np.float64)
    rgb = pc.get("rgb")
    n = xyz.shape[0]

    max_val = (1 << bits) - 1
    xyz_min = xyz.min(axis=0)
    xyz_max = xyz.max(axis=0)
    extent = float((xyz_max - xyz_min).max())
    if extent < 1e-12:
        extent = 1.0

    scale = max_val / extent
    xyz_int = np.round((xyz - xyz_min) * scale).astype(np.int32)
    xyz_int = np.clip(xyz_int, 0, max_val).astype(np.int32)

    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write("ply\n")
        f.write("format ascii 1.0\n")
        f.write(f"element vertex {n}\n")
        f.write("property int x\n")
        f.write("property int y\n")
        f.write("property int z\n")
        if rgb is not None:
            rgb = np.asarray(rgb, dtype=np.uint8)
            f.write("property uchar red\n")
            f.write("property uchar green\n")
            f.write("property uchar blue\n")
        f.write("end_header\n")

        if rgb is not None:
            for p, c in zip(xyz_int, rgb):
                f.write(f"{int(p[0])} {int(p[1])} {int(p[2])} {int(c[0])} {int(c[1])} {int(c[2])}\n")
        else:
            for p in xyz_int:
                f.write(f"{int(p[0])} {int(p[1])} {int(p[2])}\n")

    return {"xyz_min": xyz_min, "scale": scale, "bits": bits}


def _restore_from_quantized(pc: dict, meta: dict) -> dict:
    xyz = np.asarray(pc["xyz"], dtype=np.float64)
    restored_xyz = xyz / float(meta["scale"]) + np.asarray(meta["xyz_min"], dtype=np.float64)
    return _maybe_make_pc(restored_xyz, rgb=pc.get("rgb"))


def _read_ply(path: Path) -> dict:
    with open(path, "rb") as f:
        first = f.readline().strip()
        if first != b"ply":
            raise ValueError(f"Not a PLY file: {path}")

        fmt, props, n = None, [], 0
        while True:
            line = f.readline().decode("ascii", errors="replace").strip()
            if line == "end_header":
                break
            if not line:
                continue
            tok = line.split()
            if tok[0] == "format":
                fmt = tok[1]
            elif tok[0] == "element" and tok[1] == "vertex":
                n = int(tok[2])
            elif tok[0] == "property" and len(tok) == 3:
                props.append((tok[1], tok[2]))

        type_map = {
            "float": ("<f4", ">f4", 4),
            "float32": ("<f4", ">f4", 4),
            "double": ("<f8", ">f8", 8),
            "float64": ("<f8", ">f8", 8),
            "uchar": ("u1", "u1", 1),
            "uint8": ("u1", "u1", 1),
            "char": ("i1", "i1", 1),
            "int8": ("i1", "i1", 1),
            "short": ("<i2", ">i2", 2),
            "int16": ("<i2", ">i2", 2),
            "ushort": ("<u2", ">u2", 2),
            "uint16": ("<u2", ">u2", 2),
            "int": ("<i4", ">i4", 4),
            "int32": ("<i4", ">i4", 4),
            "uint": ("<u4", ">u4", 4),
            "uint32": ("<u4", ">u4", 4),
        }

        names = [p[1] for p in props]
        row_size = sum(type_map[p[0]][2] for p in props)

        if fmt == "ascii":
            rows = [f.readline().decode("ascii", errors="replace").split() for _ in range(n)]
            data = {nm: np.array([float(r[i]) for r in rows]) for i, nm in enumerate(names)}
        else:
            if fmt not in ("binary_little_endian", "binary_big_endian"):
                raise ValueError(f"Unsupported PLY format '{fmt}' in {path}")
            little = fmt == "binary_little_endian"
            raw = f.read(n * row_size)
            dtype = np.dtype(
                [(nm, type_map[tp][0] if little else type_map[tp][1]) for tp, nm in props]
            )
            arr = np.frombuffer(raw, dtype=dtype, count=n)
            data = {nm: arr[nm] for nm in names}

    xyz = np.stack(
        [
            np.asarray(data["x"], dtype=np.float64),
            np.asarray(data["y"], dtype=np.float64),
            np.asarray(data["z"], dtype=np.float64),
        ],
        axis=1,
    )

    rgb = None
    r = data.get("red", data.get("r"))
    g = data.get("green", data.get("g"))
    b = data.get("blue", data.get("b"))
    if r is not None and g is not None and b is not None:
        rgb = np.stack([r, g, b], axis=1).astype(np.uint8)

    return _maybe_make_pc(xyz, rgb=rgb)


class GPCCCodec:
    name = "gpcc"

    def __init__(self, position_qscale: float = 1.0, input_bits: int = 10):
        self.BINARY = _find_executable(
            os.environ.get("TMC3_BINARY", "tmc3"),
            extra_search_roots=["/opt/tmc3", "/usr/local/bin"],
        )
        # self.position_qscale = float(position_qscale)
        self.position_qscale = float(1.0)
        self.input_bits = int(input_bits)

    def compress_eval(self, original: dict, preprocessed: dict, peak: Optional[float] = None) -> dict:
        with tempfile.TemporaryDirectory() as td:
            r = self._round_trip(preprocessed, Path(td))

        n = int(original["xyz"].shape[0])
        d1 = d1_psnr(original, r["rec"], peak=peak)
        d2 = d2_psnr(original, r["rec"], peak=peak)

        return {
            "bpp": (r["size_bytes"] * 8) / max(n, 1),
            "d1": d1,
            "d2": d2,
            "d1_psnr": d1["sym_psnr"],
            "d2_psnr": d2["sym_psnr"],
            "encode_s": r["encode_s"],
            "decode_s": r["decode_s"],
            "size_bytes": r["size_bytes"],
            "reconstructed": r["rec"],
        }

    def _round_trip(self, pc: dict, td: Path) -> dict:
        ply_in = td / "in.ply"
        bin_out = td / "stream.bin"
        ply_out = td / "decoded.ply"

        # meta = _write_ply_int_ascii(pc, ply_in, bits=self.input_bits)
        _write_ply_float(pc, ply_in)

        cmd = [
            self.BINARY,
            "--mode=0",
            f"--uncompressedDataPath={ply_in}",
            f"--compressedStreamPath={bin_out}",
            "--inputScale=1000",
            "--codingScale=1",
            "--sequenceScale=1",
            "--autoSeqBbox=1",
        ]
        if pc.get("rgb") is None:
            cmd.append("--disableAttributeCoding=1")

        t0 = time.perf_counter()
        _run(cmd, "tmc3 encode")
        encode_s = time.perf_counter() - t0

        t0 = time.perf_counter()
        _run(
            [
                self.BINARY,
                "--mode=1",
                f"--compressedStreamPath={bin_out}",
                f"--reconstructedDataPath={ply_out}",
            ],
            "tmc3 decode",
        )
        decode_s = time.perf_counter() - t0

        if not ply_out.exists():
            raise RuntimeError("tmc3 decode finished but did not produce decoded.ply")

        # rec = _restore_from_quantized(_read_ply(ply_out), meta)
        rec = _read_ply(ply_out)
        return {
            "rec": rec,
            "size_bytes": os.path.getsize(bin_out),
            "encode_s": encode_s,
            "decode_s": decode_s,
        }


class DracoCodec:
    name = "draco"

    def __init__(self, qp: int = 11, cl: int = 1):
        self.ENC_BINARY = _find_executable(
            os.environ.get("DRACO_ENCODER", "draco_encoder"),
            extra_search_roots=["/opt/draco", "/usr/local/bin", "/usr/bin"],
        )
        self.DEC_BINARY = _find_executable(
            os.environ.get("DRACO_DECODER", "draco_decoder"),
            extra_search_roots=["/opt/draco", "/usr/local/bin", "/usr/bin"],
        )

        if not (1 <= qp <= 31):
            raise ValueError("qp must be in [1, 31]")
        if not (0 <= cl <= 10):
            raise ValueError("cl must be in [0, 10]")

        self.qp = int(qp)
        self.cl = int(cl)

    def compress_eval(self, original: dict, preprocessed: dict, peak: Optional[float] = None) -> dict:
        with tempfile.TemporaryDirectory() as td:
            r = self._round_trip(preprocessed, Path(td))

        n = int(original["xyz"].shape[0])
        d1 = d1_psnr(original, r["rec"], peak=peak)
        d2 = d2_psnr(original, r["rec"], peak=peak)

        return {
            "bpp": (r["size_bytes"] * 8) / max(n, 1),
            "d1": d1,
            "d2": d2,
            "d1_psnr": d1["sym_psnr"],
            "d2_psnr": d2["sym_psnr"],
            "encode_s": r["encode_s"],
            "decode_s": r["decode_s"],
            "size_bytes": r["size_bytes"],
            "reconstructed": r["rec"],
        }

    def _round_trip(self, pc: dict, td: Path) -> dict:
        ply_in = td / "in.ply"
        drc_out = td / "stream.drc"
        ply_out = td / "decoded.ply"

        _write_ply_float(pc, ply_in)

        t0 = time.perf_counter()
        _run(
            [
                self.ENC_BINARY,
                "-point_cloud",
                "-i", str(ply_in),
                "-o", str(drc_out),
                "-qp", str(self.qp),
                "-cl", str(self.cl),
            ],
            "draco_encoder",
        )
        encode_s = time.perf_counter() - t0

        t0 = time.perf_counter()
        _run(
            [
                self.DEC_BINARY,
                "-i", str(drc_out),
                "-o", str(ply_out),
            ],
            "draco_decoder",
        )
        decode_s = time.perf_counter() - t0

        if not ply_out.exists():
            raise RuntimeError("draco_decoder finished but did not produce decoded.ply")

        rec = _read_ply(ply_out)
        return {
            "rec": rec,
            "size_bytes": os.path.getsize(drc_out),
            "encode_s": encode_s,
            "decode_s": decode_s,
        }


class VPCCCodec:
    name = "vpcc"

    def __init__(self, *args, **kwargs):
        raise RuntimeError("VPCC is intentionally disabled for now. Use gpcc or draco.")


CODECS = {
    "gpcc": GPCCCodec,
    "draco": DracoCodec,
    "vpcc": VPCCCodec,
}


def get_codec(name: str, **kwargs):
    cls = CODECS.get(name.lower())
    if cls is None:
        raise ValueError(f"Unknown codec '{name}'. Available: {list(CODECS)}")
    return cls(**kwargs)