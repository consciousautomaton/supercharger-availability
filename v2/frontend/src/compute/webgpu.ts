export interface WebGPUStatus {
  supported: boolean;
  adapterInfo?: string;
  reason?: string;
}

interface GPUAdapterInfoLike {
  vendor?: string;
  architecture?: string;
  device?: string;
}

interface GPUAdapterLike {
  info?: GPUAdapterInfoLike;
  requestDevice(): Promise<unknown>;
}

interface GPULike {
  requestAdapter(): Promise<GPUAdapterLike | null>;
}

function navigatorGPU(): GPULike | null {
  const nav = navigator as Navigator & { gpu?: GPULike };
  return nav.gpu ?? null;
}

export async function getWebGPUStatus(): Promise<WebGPUStatus> {
  const gpu = navigatorGPU();
  if (!gpu) {
    return {
      supported: false,
      reason: "WebGPU is not available in this browser.",
    };
  }

  const adapter = await gpu.requestAdapter();
  if (!adapter) {
    return {
      supported: false,
      reason: "WebGPU adapter request failed.",
    };
  }

  return {
    supported: true,
    adapterInfo: adapter.info
      ? [adapter.info.vendor, adapter.info.architecture, adapter.info.device]
          .filter(Boolean)
          .join(" / ")
      : undefined,
  };
}

export async function requestWebGPUDevice(): Promise<unknown> {
  const gpu = navigatorGPU();
  if (!gpu) {
    throw new Error("WebGPU is not available in this browser.");
  }
  const adapter = await gpu.requestAdapter();
  if (!adapter) throw new Error("WebGPU adapter request failed.");
  return adapter.requestDevice();
}
