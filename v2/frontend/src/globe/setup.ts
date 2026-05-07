import {
  AmbientLight,
  DirectionalLight,
  PerspectiveCamera,
  Scene,
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
  renderer.setClearColor(0x07080c, 1);
  container.appendChild(renderer.domElement);

  scene.add(new AmbientLight(0x6b7288, 0.8));
  const dirLight = new DirectionalLight(0xffffff, 1.0);
  dirLight.position.set(1, 1, 1);
  scene.add(dirLight);

  const globe = new ThreeGlobe()
    .showAtmosphere(true)
    .atmosphereColor("#4aa3ff")
    .atmosphereAltitude(0.18);
  scene.add(globe);

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
