// Transparent globe overlay that paints WebGPU-computed covered cells.
//
// Three.js (WebGL2) and the WebGPU compute pipeline use separate GPU
// contexts, so the GPU-side mask buffer can't be shared as a texture
// directly. Instead, the compute pipeline reads back the mask once per
// dispatch (~1 MB at 0.25 deg) and uploads it here as a DataTexture.
//
// Renders as a slightly larger semi-transparent sphere parented to the
// globe. The fragment shader samples the mask texture and outputs a
// uniform tint (V2 blue gradient) only where covered, using the existing
// equirect UV mapping that matches the population grid layout.

import {
  DataTexture,
  Mesh,
  RedFormat,
  ShaderMaterial,
  SphereGeometry,
  UnsignedByteType,
  Vector3,
  type Color,
} from "three";

const GLOBE_RADIUS = 100;
const OVERLAY_ALTITUDE = 0.005; // sit just above the globe surface
const SEGMENTS_LON = 96;
const SEGMENTS_LAT = 64;

const VERT = /* glsl */ `
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const FRAG = /* glsl */ `
precision highp float;
uniform sampler2D uMask;
uniform vec3 uTint;
uniform float uAlpha;
varying vec2 vUv;

void main() {
  // Longitude: pop grid column 0 == lon -180. ThreeGlobe rotates its
  // root object by -PI/2 around Y so that lon 0 sits at +Z. Once that
  // rotation is inherited by this child mesh, the default UV.x already
  // matches the pop grid layout (lon -180 -> u 0, lon +180 -> u 1).
  //
  // Latitude: pop grid row 0 == lat +90 (north). Three's SphereGeometry
  // also has v == 0 at the north pole, but WebGL's UnpackFlipY default
  // for DataTextures effectively places data row 0 at the bottom of the
  // texture in UV space, so we flip V here.
  vec2 uv = vec2(vUv.x, 1.0 - vUv.y);
  float covered = texture2D(uMask, uv).r;
  if (covered <= 0.0) {
    discard;
  }
  gl_FragColor = vec4(uTint, uAlpha * covered);
}
`;

export interface CoverageLayer {
  mesh: Mesh;
  setMask(mask: Uint8Array): void;
  setVisible(v: boolean): void;
  destroy(): void;
}

export function createCoverageLayer(
  width: number,
  height: number,
  tint: Color | { r: number; g: number; b: number } = { r: 0.122, g: 0.498, b: 0.941 },
  alpha = 0.45,
): CoverageLayer {
  const initialData = new Uint8Array(width * height);
  const texture = new DataTexture(
    initialData,
    width,
    height,
    RedFormat,
    UnsignedByteType,
  );
  texture.needsUpdate = true;
  texture.flipY = false;

  const material = new ShaderMaterial({
    vertexShader: VERT,
    fragmentShader: FRAG,
    transparent: true,
    depthWrite: false,
    uniforms: {
      uMask: { value: texture },
      uTint: { value: new Vector3(tint.r, tint.g, tint.b) },
      uAlpha: { value: alpha },
    },
  });

  const radius = GLOBE_RADIUS * (1 + OVERLAY_ALTITUDE);
  const geometry = new SphereGeometry(radius, SEGMENTS_LON, SEGMENTS_LAT);
  const mesh = new Mesh(geometry, material);

  function setMask(mask: Uint8Array): void {
    if (mask.length !== width * height) {
      console.warn(
        `[coverageLayer] mask size mismatch: got ${mask.length}, expected ${width * height}`,
      );
      return;
    }
    texture.image.data.set(mask);
    texture.needsUpdate = true;
  }

  function setVisible(v: boolean): void {
    mesh.visible = v;
  }

  function destroy(): void {
    geometry.dispose();
    material.dispose();
    texture.dispose();
  }

  return { mesh, setMask, setVisible, destroy };
}
