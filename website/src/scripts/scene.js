// A cena da página de entrada: uma pista noturna que leva ao hangar, trilhas de luz douradas pulsando e os agentes
// (os avatares do Agent Hangar) saindo do hangar para o trabalho. Leve de propósito: sem modelos 3D nem texturas
// grandes, pausa com a aba oculta e fica parada com "reduzir movimento". Sem WebGL, sobra o gradiente do fundo.
import * as THREE from 'three';

const GOLD = new THREE.Color('#f4b400');
const WARM = new THREE.Color('#ffd27a');

export function startScene(canvas) {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
  } catch {
    canvas.remove();
    return;
  }
  const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2('#0c111a', 0.016);
  renderer.setClearColor('#070a0f');
  // céu: escuro no alto e um brilho quente no horizonte, na direção do hangar (sem a linha dura onde o chão acaba)
  scene.add(new THREE.Mesh(new THREE.SphereGeometry(300, 32, 16), new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, fog: false,
    vertexShader: 'varying vec3 p; void main(){ p = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.); }',
    fragmentShader: `varying vec3 p;
      void main(){
        float h = clamp(p.y, -0.2, 1.);
        vec3 zenith = vec3(.027,.039,.059), horizon = vec3(.11,.085,.05);
        vec3 col = mix(horizon, zenith, smoothstep(0., .45, h));
        float sun = pow(max(dot(normalize(vec3(p.x, p.y * 2.5, p.z)), vec3(0., .08, -1.)), 0.), 18.);
        col += vec3(.95,.62,.12) * sun * .55;
        gl_FragColor = vec4(col, 1.);
      }`,
  })));
  const camera = new THREE.PerspectiveCamera(58, 1, 0.1, 400);
  camera.position.set(0, 3.2, 14);

  // chão: um plano escuro com uma grade bem discreta (o pátio do hangar)
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(400, 400),
    new THREE.MeshBasicMaterial({ color: '#0a0f16' }));
  ground.rotation.x = -Math.PI / 2;
  scene.add(ground);
  const grid = new THREE.GridHelper(400, 160, '#1b2533', '#121a25');
  grid.position.y = 0.01;
  grid.material.transparent = true;
  grid.material.opacity = 0.45;
  scene.add(grid);

  // pista: luzes de borda amarelas e a faixa central que corre na direção da câmera
  const edge = new THREE.InstancedMesh(new THREE.SphereGeometry(0.07, 8, 8),
    new THREE.MeshBasicMaterial({ color: GOLD }), 2 * 60);
  const m4 = new THREE.Matrix4();
  for (let i = 0; i < 60; i++) {
    m4.makeTranslation(-3.2, 0.06, 12 - i * 2.4);
    edge.setMatrixAt(i * 2, m4);
    m4.makeTranslation(3.2, 0.06, 12 - i * 2.4);
    edge.setMatrixAt(i * 2 + 1, m4);
  }
  scene.add(edge);
  const dashes = new THREE.InstancedMesh(new THREE.PlaneGeometry(0.18, 1.4),
    new THREE.MeshBasicMaterial({ color: WARM, transparent: true, opacity: 0.55 }), 40);
  scene.add(dashes);

  // o hangar no horizonte: casco escuro com o arco do telhado e a porta acesa
  const hangar = new THREE.Group();
  const shell = new THREE.Mesh(new THREE.CylinderGeometry(9, 9, 18, 40, 1, true, 0, Math.PI),
    new THREE.MeshBasicMaterial({ color: '#0d131c', side: THREE.DoubleSide }));
  shell.rotation.z = Math.PI / 2;
  shell.rotation.y = Math.PI / 2;
  hangar.add(shell);
  const door = new THREE.Mesh(new THREE.PlaneGeometry(9, 6.5),
    new THREE.MeshBasicMaterial({ color: '#ffe3a1', transparent: true, opacity: 0.9 }));
  door.position.set(0, 3.25, 9.05);
  hangar.add(door);
  const glow = new THREE.Mesh(new THREE.PlaneGeometry(26, 16), new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { c: { value: GOLD } },
    vertexShader: 'varying vec2 v; void main(){ v = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.); }',
    fragmentShader: 'uniform vec3 c; varying vec2 v; void main(){ float d = distance(v, vec2(.5,.22)); gl_FragColor = vec4(c, .55 * smoothstep(.55, 0., d)); }',
  }));
  glow.position.set(0, 4, 9.2);
  hangar.add(glow);
  hangar.position.set(0, 0, -132);
  scene.add(hangar);

  // trilhas de luz: curvas que saem do hangar e se abrem em direção à câmera, com pulsos correndo nelas
  const trailMat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { t: { value: 0 }, a: { value: GOLD }, b: { value: WARM } },
    vertexShader: 'varying vec2 v; void main(){ v = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.); }',
    fragmentShader: `uniform float t; uniform vec3 a; uniform vec3 b; varying vec2 v;
      void main(){
        float base = .32 + .35 * v.x;
        float p = pow(fract(v.x * 3. - t * .35), 18.) + pow(fract(v.x * 7. - t * .6 + .3), 30.) * .7;
        float edge = smoothstep(0., .5, v.y) * smoothstep(1., .5, v.y);
        gl_FragColor = vec4(mix(a, b, p), (base + p * 2.2) * edge * smoothstep(0., .08, v.x) * smoothstep(1., .82, v.x));
      }`,
  });
  const curves = [];
  const spread = [-16, -9, -4.5, 4.5, 9, 16, -24, 24];
  spread.forEach((x, i) => {
    const y = i > 5 ? 0.6 : 0.15;
    const pts = [new THREE.Vector3(x * 0.15, 1.2, -128), new THREE.Vector3(x * 0.35, 0.8 + y, -80),
      new THREE.Vector3(x * 0.7, 0.4 + y, -36), new THREE.Vector3(x * 1.1, y, 4), new THREE.Vector3(x * 1.5, y, 20)];
    const curve = new THREE.CatmullRomCurve3(pts);
    curves.push(curve);
    scene.add(new THREE.Mesh(new THREE.TubeGeometry(curve, 160, i > 5 ? 0.05 : 0.09, 6, false), trailMat));
  });

  // estrelas
  const starsGeo = new THREE.BufferGeometry();
  const pos = new Float32Array(1400 * 3);
  for (let i = 0; i < 1400; i++) {
    pos[i * 3] = (Math.random() - 0.5) * 360;
    pos[i * 3 + 1] = 12 + Math.random() * 90;
    pos[i * 3 + 2] = -60 - Math.random() * 200;
  }
  starsGeo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  scene.add(new THREE.Points(starsGeo, new THREE.PointsMaterial({ color: '#c8d3e6', size: 0.35, transparent: true, opacity: 0.7 })));

  // os agentes: avatares em círculo, com um halo dourado, percorrendo as trilhas do hangar até a câmera
  const base = canvas.dataset.base || '';
  const crew = (canvas.dataset.crew || '').split(',').filter(Boolean);
  const walkers = [];
  const loader = new THREE.ImageLoader();
  const halo = (() => {
    const c = document.createElement('canvas');
    c.width = c.height = 128;
    const g = c.getContext('2d');
    const grd = g.createRadialGradient(64, 64, 30, 64, 64, 64);
    grd.addColorStop(0, 'rgba(255,214,120,.9)');
    grd.addColorStop(1, 'rgba(244,180,0,0)');
    g.fillStyle = grd;
    g.fillRect(0, 0, 128, 128);
    return new THREE.CanvasTexture(c);
  })();
  crew.forEach((id, i) => {
    loader.load(`${base}/avatars/${id}.webp`, (img) => {
      const c = document.createElement('canvas');
      c.width = c.height = 128;
      const g = c.getContext('2d');
      g.beginPath();
      g.arc(64, 64, 62, 0, Math.PI * 2);
      g.clip();
      g.drawImage(img, 0, 0, 128, 128);
      const tex = new THREE.CanvasTexture(c);
      tex.colorSpace = THREE.SRGBColorSpace;
      const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }));
      const ring = new THREE.Sprite(new THREE.SpriteMaterial({ map: halo, transparent: true, depthWrite: false,
        blending: THREE.AdditiveBlending }));
      scene.add(ring, sprite);
      walkers.push({ sprite, ring, curve: curves[i % 6], u: (i / crew.length + Math.random() * 0.05) % 1,
        speed: 0.018 + Math.random() * 0.012, bob: Math.random() * 6 });
    });
  });

  // câmera: segue o mouse de leve e sobe com a rolagem
  const target = new THREE.Vector2();
  window.addEventListener('pointermove', (e) => {
    target.set((e.clientX / window.innerWidth - 0.5) * 2, (e.clientY / window.innerHeight - 0.5) * 2);
  }, { passive: true });
  const look = new THREE.Vector3(0, 1.4, -40);

  function resize() {
    const w = window.innerWidth;
    const h = window.innerHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.fov = w < 700 ? 70 : 58;
    camera.updateProjectionMatrix();
  }
  window.addEventListener('resize', resize);
  resize();

  const clock = new THREE.Clock();
  let running = true;
  document.addEventListener('visibilitychange', () => {
    running = !document.hidden;
    if (running && !still) { clock.getDelta(); requestAnimationFrame(frame); }
  });

  function frame() {
    if (!running) return;
    const dt = Math.min(clock.getDelta(), 0.05);
    const t = clock.elapsedTime;
    trailMat.uniforms.t.value = t;
    for (let i = 0; i < 40; i++) {  // a faixa central corre para a câmera
      const z = 12 - ((i * 3.2 + t * 6) % 128);
      m4.makeRotationX(-Math.PI / 2).setPosition(0, 0.02, z);
      dashes.setMatrixAt(i, m4);
    }
    dashes.instanceMatrix.needsUpdate = true;
    for (const w of walkers) {
      w.u = (w.u + w.speed * dt) % 1;
      const p = w.curve.getPointAt(w.u);
      const s = 0.55 + w.u * 0.85;  // crescem pouco: não cobrem o texto nem os capítulos
      const fade = Math.min(1, w.u * 6) * Math.max(0, Math.min(1, (0.78 - w.u) * 5));  // somem antes de chegar à câmera
      w.sprite.position.set(p.x, p.y + 1.1 + s * 0.5 + Math.sin(t * 2 + w.bob) * 0.06, p.z);
      w.sprite.scale.set(s, s, 1);
      w.sprite.material.opacity = fade;
      w.ring.position.copy(w.sprite.position);
      w.ring.scale.set(s * 1.7, s * 1.7, 1);
      w.ring.material.opacity = fade * 0.55;
    }
    const scroll = Math.min(1, window.scrollY / (window.innerHeight * 4));
    camera.position.x += (target.x * 1.6 - camera.position.x) * 0.04;
    camera.position.y += (3.2 + scroll * 6 - target.y * 0.6 - camera.position.y) * 0.04;
    camera.position.z = 14 - scroll * 10;
    camera.lookAt(look);
    renderer.render(scene, camera);
    if (!still) requestAnimationFrame(frame);
  }
  if (still) {  // reduzir movimento: um quadro só, com os agentes já no caminho
    clock.elapsedTime = 4;
    setTimeout(frame, 600);
  } else {
    requestAnimationFrame(frame);
  }
}
