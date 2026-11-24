#!/usr/bin/env python3
"""Streaming combine: never holds more than a few slabs (along axis 0) in memory.

Same processing as the original combine_rsm script; the only change is that
the input files and the output file come from the command line, and the
third file is optional:

    python combine_rsm.py A.nxs B.nxs [C.nxs] -o combined.nxs

A is the reference, B is aligned to A and averaged with it, and C (if given)
fills the voxels that are still empty.
"""
import argparse
import contextlib
import os
import warnings

import h5py
import numpy as np
import scipy.signal

parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
parser.add_argument("files", nargs="+", help="two or three .nxs files: A (reference), B, [C]")
parser.add_argument("-o", "--output", required=True, help="combined .nxs file to write")
parser.add_argument("--chunk", type=int, default=10,
                    help="slices along axis 0 per read; lower if short on memory")
args = parser.parse_args()
files = args.files
if len(files) not in (2, 3):
    parser.error("give two or three files")
output = args.output
chunk = args.chunk

warnings.simplefilter("ignore", category=RuntimeWarning)


def _str(x):
    return x.decode() if isinstance(x, bytes) else str(x)


def open_nxdata(h):
    g = h["entry/data"]
    axes = g.attrs["axes"]
    axes = [_str(a) for a in np.atleast_1d(axes)]
    if len(axes) == 1 and ":" in axes[0]:
        axes = axes[0].split(":")
    return g, g[_str(g.attrs["signal"])], axes


def read(ds, i, j):
    blk = ds[i:j].astype(np.float32)
    blk[blk == 0] = np.nan
    return blk


def projection(ds):
    proj = np.zeros((ds.shape[0], ds.shape[2]), dtype=np.float32)
    for i in range(0, ds.shape[0], chunk):
        proj[i:i + chunk] = np.nan_to_num(np.nanmax(read(ds, i, i + chunk), axis=1))
    return proj


def read_shifted(ds, i, j, d0, d2):
    """Rows i:j of ds shifted by integer (d0, 0, d2); out-of-range -> NaN."""
    n0, n1, n2 = ds.shape
    out = np.full((j - i, n1, n2), np.nan, dtype=np.float32)
    s0, e0 = max(i - d0, 0), min(j - d0, n0)
    if s0 >= e0:
        return out
    src = read(ds, s0, e0)
    o0 = s0 + d0 - i
    if d2 >= 0:
        out[o0:o0 + src.shape[0], :, d2:] = src[:, :, :n2 - d2]
    else:
        out[o0:o0 + src.shape[0], :, :n2 + d2] = src[:, :, -d2:]
    return out


with contextlib.ExitStack() as stack:
    fa = stack.enter_context(h5py.File(files[0], "r"))
    fb = stack.enter_context(h5py.File(files[1], "r"))
    fc = stack.enter_context(h5py.File(files[2], "r")) if len(files) == 3 else None
    fo = stack.enter_context(h5py.File(output, "w"))
    ga, A, axes = open_nxdata(fa)
    _, B, _ = open_nxdata(fb)
    C = open_nxdata(fc)[1] if fc is not None else None   # no third file: nothing to fill from
    if A.shape != B.shape or (C is not None and C.shape != A.shape):
        raise SystemExit("Shape mismatch: " + " ".join(
            str(d.shape) for d in (A, B, C) if d is not None))
    n0 = A.shape[0]
    print(f"Volume shape {A.shape}, axes {axes}", flush=True)

    # Alignment of B to A from 2D max-projections (streamed)
    img1 = projection(A); img1 -= img1.mean()
    img2 = projection(B); img2 -= img2.mean()
    corr = scipy.signal.fftconvolve(img1, img2[::-1, ::-1], mode="same")
    d0, d2 = (int(v) for v in np.array(np.unravel_index(np.argmax(corr), corr.shape)) - np.array(corr.shape) // 2)
    del img1, img2, corr
    print(f"Shift applied to {os.path.basename(files[1])}: {axes[0]} {d0:+d} px, {axes[2]} {d2:+d} px", flush=True)

    # Output NeXus structure
    fo.attrs["NX_class"] = "NXroot"
    fo.attrs["default"] = "entry"
    entry = fo.create_group("entry"); entry.attrs["NX_class"] = "NXentry"; entry.attrs["default"] = "data"
    gd = entry.create_group("data"); gd.attrs["NX_class"] = "NXdata"
    gd.attrs["signal"] = "counts"
    gd.attrs["axes"] = np.array(axes, dtype=h5py.string_dtype())
    for name in axes:
        gd.create_dataset(name, data=ga[name][()])
    out = gd.create_dataset("counts", shape=A.shape, dtype=np.float32,
                            chunks=(1,) + A.shape[1:], compression="gzip", compression_opts=1)
    out.attrs["long_name"] = "Intensity"

    # Average A and aligned B, fill empties from C (if given), write slab by slab
    for i in range(0, n0, chunk):
        j = min(i + chunk, n0)
        blk = np.nan_to_num(np.nanmean([read(A, i, j), read_shifted(B, i, j, d0, d2)], axis=0))
        if C is not None:
            empty = blk == 0
            blk[empty] = C[i:j][empty]
        out[i:j] = blk
        print(f"\r  {j}/{n0}", end="", flush=True)

print(f"\nSaved {output}")
