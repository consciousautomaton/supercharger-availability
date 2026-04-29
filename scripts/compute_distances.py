import time
import traceback
import numpy as np
import cupy as cp

PIXELS_NPZ   = "data/populated_pixels.npz"
CHARGERS_NPZ = "data/chargers.npz"
OUT_PATH     = "data/pixel_distances.npz"
LOG_PATH     = "data/compute_distances.log"

BATCH = 100_000_000
BLOCK = 512

def log(msg, f):
    print(msg)
    f.write(msg + "\n")
    f.flush()

def vram_str():
    free, total = cp.cuda.runtime.memGetInfo()
    used = total - free
    return f"VRAM {used/1e9:.2f}/{total/1e9:.2f} GB"

# --- CUDA kernel ---
KERNEL = r"""
__device__ float vincenty(float phi1, float lam1, float phi2, float lam2) {
    const float a = 6378137.0f;
    const float f = 1.0f / 298.257223563f;
    const float b = 6356752.3142f;

    float L = lam2 - lam1;
    float U1 = atanf((1.0f - f) * tanf(phi1));
    float U2 = atanf((1.0f - f) * tanf(phi2));
    float sinU1 = sinf(U1), cosU1 = cosf(U1);
    float sinU2 = sinf(U2), cosU2 = cosf(U2);

    float lam = L;
    float sinLam, cosLam, sinSig, cosSig, sig, sinAlpha, cosSqAlpha, cos2SigM;

    for (int iter = 0; iter < 12; iter++) {
        sinLam = sinf(lam);
        cosLam = cosf(lam);
        float t1 = cosU2 * sinLam;
        float t2 = cosU1 * sinU2 - sinU1 * cosU2 * cosLam;
        sinSig = sqrtf(t1*t1 + t2*t2);
        if (sinSig == 0.0f) return 0.0f;
        cosSig     = sinU1*sinU2 + cosU1*cosU2*cosLam;
        sig        = atan2f(sinSig, cosSig);
        sinAlpha   = cosU1 * cosU2 * sinLam / sinSig;
        cosSqAlpha = 1.0f - sinAlpha*sinAlpha;
        cos2SigM   = (cosSqAlpha != 0.0f) ? (cosSig - 2.0f*sinU1*sinU2/cosSqAlpha) : 0.0f;
        float C    = f/16.0f * cosSqAlpha * (4.0f + f*(4.0f - 3.0f*cosSqAlpha));
        lam = L + (1.0f - C)*f*sinAlpha*(sig + C*sinSig*(cos2SigM + C*cosSig*(-1.0f + 2.0f*cos2SigM*cos2SigM)));
    }

    float uSq = cosSqAlpha * (a*a - b*b) / (b*b);
    float A   = 1.0f + uSq/16384.0f * (4096.0f + uSq*(-768.0f + uSq*(320.0f - 175.0f*uSq)));
    float B   = uSq/1024.0f * (256.0f + uSq*(-128.0f + uSq*(74.0f - 47.0f*uSq)));
    float dS  = B*sinSig*(cos2SigM + B/4.0f*(cosSig*(-1.0f + 2.0f*cos2SigM*cos2SigM)
                - B/6.0f*cos2SigM*(-3.0f + 4.0f*sinSig*sinSig)*(-3.0f + 4.0f*cos2SigM*cos2SigM)));
    return b * A * (sig - dS);
}

extern "C" __global__ void min_vincenty(
        const float* pix_lats, const float* pix_lons,
        const float* ch_lats,  const float* ch_lons,
        float* out_km, int N, int C) {

    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= N) return;

    const float DEG2RAD = 0.017453292519943295f;
    float phi1 = pix_lats[i] * DEG2RAD;
    float lam1 = pix_lons[i] * DEG2RAD;

    float best = 1e9f;
    for (int j = 0; j < C; j++) {
        float d = vincenty(phi1, lam1, ch_lats[j] * DEG2RAD, ch_lons[j] * DEG2RAD);
        if (d < best) best = d;
    }
    out_km[i] = best * 1e-3f;
}
"""

with open(LOG_PATH, "w") as logf:
    try:
        log(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}", logf)

        pix = np.load(PIXELS_NPZ)
        pix_lons = pix["lons"]
        pix_lats = pix["lats"]
        pix_pop  = pix["pop"]
        N = len(pix_lons)
        log(f"Pixels:   {N:,}", logf)

        ch = np.load(CHARGERS_NPZ)
        ch_lats = ch["lats"]
        ch_lons = ch["lons"]
        C = len(ch_lats)
        log(f"Chargers: {C:,}", logf)

        kernel = cp.RawKernel(KERNEL, "min_vincenty")
        log("Kernel compiled.", logf)

        d_ch_lats = cp.asarray(ch_lats.astype(np.float32))
        d_ch_lons = cp.asarray(ch_lons.astype(np.float32))
        log(f"Chargers on GPU. {vram_str()}", logf)

        out_km   = np.empty(N, dtype=np.float32)
        t_total  = time.time()

        for start in range(0, N, BATCH):
            end        = min(start + BATCH, N)
            batch_size = end - start
            batch_num  = start // BATCH + 1
            log(f"\nBatch {batch_num}: pixels {start:,}–{end:,} ({batch_size/1e6:.0f}M)  |  {vram_str()}", logf)

            t0     = time.time()
            d_lats = cp.asarray(pix_lats[start:end])
            d_lons = cp.asarray(pix_lons[start:end])
            d_out  = cp.empty(batch_size, dtype=cp.float32)
            log(f"  Uploaded to GPU. {vram_str()}", logf)

            grid = (batch_size + BLOCK - 1) // BLOCK
            kernel((grid,), (BLOCK,), (d_lats, d_lons, d_ch_lats, d_ch_lons, d_out, batch_size, C))
            cp.cuda.runtime.deviceSynchronize()

            elapsed   = time.time() - t0
            throughput = batch_size / elapsed / 1e6
            out_km[start:end] = d_out.get()

            log(f"  Done in {elapsed:.1f}s  ({throughput:.1f}M px/s)  "
                f"min={out_km[start:end].min():.2f} km  max={out_km[start:end].max():.2f} km", logf)

        total_elapsed = time.time() - t_total
        log(f"\nAll batches done in {total_elapsed:.1f}s. Saving...", logf)

        np.savez_compressed(OUT_PATH, lons=pix_lons, lats=pix_lats, pop=pix_pop, min_dist_km=out_km)
        log(f"Saved to {OUT_PATH}", logf)
        log(f"Finished: {time.strftime('%Y-%m-%d %H:%M:%S')}", logf)

    except Exception:
        msg = traceback.format_exc()
        log(f"\nCRASH:\n{msg}", logf)
        raise
