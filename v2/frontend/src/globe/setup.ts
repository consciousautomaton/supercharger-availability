import {
  AmbientLight,
  CanvasTexture,
  DirectionalLight,
  MeshPhongMaterial,
  PerspectiveCamera,
  Scene,
  SRGBColorSpace,
  WebGLRenderer,
} from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import ThreeGlobe from "three-globe";

export interface GlobeContext {
  scene: Scene;
  camera: PerspectiveCamera;
  renderer: WebGLRenderer;
  controls: OrbitControls;
  globe: ThreeGlobe;
}

const LAND_COLOR: [number, number, number] = [207, 210, 216];
const OCEAN_COLOR: [number, number, number] = [244, 245, 248];

function loadColorizedLandMask(url: string): Promise<CanvasTexture> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = img.width;
      canvas.height = img.height;
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        reject(new Error("2d context unavailable"));
        return;
      }
      ctx.drawImage(img, 0, 0);
      const data = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const px = data.data;
      for (let i = 0; i < px.length; i += 4) {
        const t = px[i] / 255;
        px[i] = LAND_COLOR[0] + (OCEAN_COLOR[0] - LAND_COLOR[0]) * t;
        px[i + 1] = LAND_COLOR[1] + (OCEAN_COLOR[1] - LAND_COLOR[1]) * t;
        px[i + 2] = LAND_COLOR[2] + (OCEAN_COLOR[2] - LAND_COLOR[2]) * t;
      }
      ctx.putImageData(data, 0, 0);
      const tex = new CanvasTexture(canvas);
      tex.colorSpace = SRGBColorSpace;
      resolve(tex);
    };
    img.onerror = () => reject(new Error(`failed to load ${url}`));
    img.src = url;
  });
}

export function createGlobe(container: HTMLElement): GlobeContext {
  const scene = new Scene();

  const camera = new PerspectiveCamera(
    50,
    container.clientWidth / container.clientHeight,
    0.1,
    2000,
  );
  camera.position.set(0, 0, 320);

  const renderer = new WebGLRenderer({ antialias: true, alpha: false });
  renderer.setPixelRatio(window.devicePixelRatio);
  renderer.setSize(container.clientWidth, container.clientHeight);
  renderer.setClearColor(0xf4f5f8, 1);
  container.appendChild(renderer.domElement);

  scene.add(new AmbientLight(0xffffff, 0.85));
  const dirLight = new DirectionalLight(0xffffff, 0.9);
  dirLight.position.set(1, 1, 1);
  scene.add(dirLight);

  const globe = new ThreeGlobe()
    .showAtmosphere(true)
    .atmosphereColor("#7fb3ff")
    .atmosphereAltitude(0.16);
  const globeMat = globe.globeMaterial() as MeshPhongMaterial;
  globeMat.color.set("#ffffff");
  globeMat.specular?.set("#ffffff");
  globeMat.shininess = 4;
  scene.add(globe);

  loadColorizedLandMask("/textures/earth-water.png")
    .then((tex) => {
      globeMat.map = tex;
      globeMat.needsUpdate = true;
    })
    .catch((err) => console.warn("[globe] map texture failed:", err));

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.minDistance = 110;
  controls.maxDistance = 800;
  controls.rotateSpeed = 0.4;
  controls.enablePan = false;

  function onResize(): void {
    const w = container.clientWidth;
    const h = container.clientHeight;
    renderer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", onResize);

  function animate(): void {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  }
  animate();

  return { scene, camera, renderer, controls, globe };
}
