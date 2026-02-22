/**
 * Farm Sim Visualizer
 * A cozy farm sim with Three.js, sprite sheets, mailboxes, gardens, and thought bubbles
 */

import * as THREE from 'three';

// ============== CONFIGURATION ==============
const CONFIG = {
  PIXELS_PER_UNIT: 32,
  CHUNK_SIZE: 16,
  TILE_SIZE: 2,
  AGENT_SCALE: 2.5,
  TIME_SCALE: 1, // 1 real second = 1 game minute
  DAY_LENGTH: 1440, // minutes in a day
  THOUGHT_INTERVAL: 8000, // ms between thoughts
  THOUGHT_DURATION: 4000, // ms how long thoughts show
};

// ============== ASSET PATHS ==============
const ASSETS = {
  // Character sprites (PIPOYA)
  characters: {
    male: '/assets/packs/Farm/PIPOYA FREE RPG Character Sprites 32x32/Male/Male 01-1.png',
  },
  // Farm tilemap (Tiny Wonder Farm)
  tiles: '/assets/packs/Farm/Tiny Wonder Farm Free 3/tilemaps/spring farm tilemap.png',
  // Farm objects
  objects: '/assets/packs/Farm/Tiny Wonder Farm Free 3/objects&items/farm objects free.png',
  // UI elements (Sunnyside)
  ui: '/assets/packs/Farm/Sunnyside_World_ASSET_PACK_V2.1 2/Sunnyside_World_Assets/UI/9slice_box_white/',
};

// ============== GAME STATE ==============
const gameState = {
  time: 360, // 6:00 AM in minutes
  day: 1,
  season: 'Spring',
  agents: [],
  gardens: [],
  mailbox: null,
  mail: [],
  selectedAgent: null,
};

// ============== THREE.JS SETUP ==============
let scene, camera, renderer, raycaster, mouse;
let worldGroup, agentsGroup, uiGroup;
let clock = new THREE.Clock();

// ============== TEXTURE CACHE ==============
const textureCache = new Map();

async function loadTexture(url) {
  if (textureCache.has(url)) return textureCache.get(url);

  return new Promise((resolve, reject) => {
    new THREE.TextureLoader().load(
      url,
      (texture) => {
        texture.magFilter = THREE.NearestFilter;
        texture.minFilter = THREE.NearestFilter;
        textureCache.set(url, texture);
        resolve(texture);
      },
      undefined,
      reject
    );
  });
}

// ============== SPRITE UTILITIES ==============
function createGridSprite(texture, col, row, cols, rows, width, height) {
  const material = new THREE.SpriteMaterial({
    map: texture.clone(),
    transparent: true,
    depthTest: false,
  });

  const tex = material.map;
  tex.repeat.set(1 / cols, 1 / rows);
  tex.offset.set(col / cols, 1 - (row + 1) / rows);
  tex.needsUpdate = true;

  const sprite = new THREE.Sprite(material);
  sprite.scale.set(width, height, 1);

  return sprite;
}

function createRegionSprite(texture, px, py, pw, ph, ppu) {
  const material = new THREE.SpriteMaterial({
    map: texture.clone(),
    transparent: true,
    depthTest: false,
  });

  const tex = material.map;
  tex.repeat.set(pw / texture.image.width, ph / texture.image.height);
  tex.offset.set(px / texture.image.width, 1 - (py + ph) / texture.image.height);
  tex.needsUpdate = true;

  const sprite = new THREE.Sprite(material);
  sprite.scale.set(pw / ppu, ph / ppu, 1);

  return sprite;
}

// ============== AGENT CLASS ==============
class Agent {
  constructor(name, role, x, z, color) {
    this.name = name;
    this.role = role;
    this.position = new THREE.Vector3(x, 0, z);
    this.targetPosition = new THREE.Vector3(x, 0, z);
    this.velocity = new THREE.Vector3();
    this.color = color;
    this.thoughts = this.generateThoughts();
    this.currentThought = null;
    this.thoughtTimer = 0;
    this.isMoving = false;
    this.garden = null;

    this.sprite = null;
    this.thoughtBubble = null;
  }

  generateThoughts() {
    const roleThoughts = {
      'Farmer': [
        "I hope the crops grow well this season...",
        "Should check the soil moisture today.",
        "The weather looks perfect for planting!",
        "I wonder if I should expand the garden?",
        "These tomatoes are coming along nicely.",
        "Time to water the vegetables again.",
        "I really enjoy the peaceful farm life.",
        "The sunrise over the fields is beautiful.",
      ],
      'Gardener': [
        "These flowers need more attention.",
        "The compost is ready for spreading!",
        "I should rotate the crops next week.",
        "The bees seem happy today!",
        "Need to prune those tomato plants.",
        "Watching plants grow is so rewarding.",
        "The garden is my happy place.",
        "Every seed tells a story of potential.",
      ],
      'Rancher': [
        "The animals need fresh water.",
        "Planning to build a bigger coop.",
        "The chickens are especially active today!",
        "Should repair the fence this afternoon.",
        "Animals have such gentle souls.",
        "Morning chores are peaceful.",
        "The barn could use a fresh coat of paint.",
        "Collecting eggs is my favorite task!",
      ],
    };

    return roleThoughts[this.role] || roleThoughts['Farmer'];
  }

  async init() {
    const texture = await loadTexture(ASSETS.characters.male);
    this.sprite = createGridSprite(
      texture,
      0, 0, 4, 4, // col, row, cols, rows
      CONFIG.AGENT_SCALE,
      CONFIG.AGENT_SCALE * 1.5
    );

    this.sprite.position.copy(this.position);
    this.sprite.position.y = CONFIG.AGENT_SCALE * 0.75;
    this.sprite.userData.agent = this;
    this.sprite.renderOrder = 10;

    agentsGroup.add(this.sprite);
  }

  update(delta) {
    // Movement
    const speed = 3;
    const direction = new THREE.Vector3()
      .subVectors(this.targetPosition, this.position)
      .normalize();

    const distance = this.position.distanceTo(this.targetPosition);

    if (distance > 0.1) {
      this.isMoving = true;
      this.position.add(direction.multiplyScalar(speed * delta));
      this.sprite.position.x = this.position.x;
      this.sprite.position.z = this.position.z;

      // Face movement direction
      if (direction.x >= 0) {
        this.sprite.scale.x = Math.abs(CONFIG.AGENT_SCALE);
      } else {
        this.sprite.scale.x = -Math.abs(CONFIG.AGENT_SCALE);
      }
    } else {
      this.isMoving = false;
    }

    // Thought timer
    this.thoughtTimer += delta * 1000;
    if (this.thoughtTimer >= CONFIG.THOUGHT_INTERVAL) {
      this.showThought();
      this.thoughtTimer = 0;
    }
  }

  showThought() {
    const thought = this.thoughts[Math.floor(Math.random() * this.thoughts.length)];
    this.currentThought = thought;

    // Update thought bubble UI
    const bubble = document.getElementById('thoughtBubble');
    const text = document.getElementById('thoughtText');
    text.textContent = thought;

    // Position bubble above agent
    const screenPos = this.sprite.position.clone();
    screenPos.project(camera);

    const x = (screenPos.x * 0.5 + 0.5) * window.innerWidth;
    const y = (-screenPos.y * 0.5 + 0.5) * window.innerHeight;

    bubble.style.left = `${x - 100}px`;
    bubble.style.top = `${y - 100}px`;
    bubble.classList.add('active');

    // Hide after duration
    setTimeout(() => {
      bubble.classList.remove('active');
    }, CONFIG.THOUGHT_DURATION);
  }

  moveTo(x, z) {
    this.targetPosition.set(x, 0, z);
  }

  dispose() {
    if (this.sprite) {
      this.sprite.material.dispose();
      agentsGroup.remove(this.sprite);
    }
  }
}

// ============== GARDEN CLASS ==============
class Garden {
  constructor(ownerAgent, x, z, width, height) {
    this.owner = ownerAgent;
    this.position = new THREE.Vector3(x, 0.1, z);
    this.width = width;
    this.height = height;
    this.crops = [];
    this.sprite = null;
  }

  async init() {
    const texture = await loadTexture(ASSETS.tiles);
    this.sprite = createRegionSprite(
      texture,
      32, 32, 16 * this.width, 16 * this.height, // tilled soil from tilemap
      CONFIG.PIXELS_PER_UNIT
    );

    this.sprite.position.copy(this.position);
    this.sprite.scale.set(
      this.width * CONFIG.TILE_SIZE,
      this.height * CONFIG.TILE_SIZE,
      1
    );

    worldGroup.add(this.sprite);

    // Create crops
    const cropSpacing = 1.5;
    for (let i = 0; i < this.width; i++) {
      for (let j = 0; j < this.height; j++) {
        const cropX = this.position.x - (this.width * CONFIG.TILE_SIZE / 2) + (i + 0.5) * CONFIG.TILE_SIZE;
        const cropZ = this.position.z - (this.height * CONFIG.TILE_SIZE / 2) + (j + 0.5) * CONFIG.TILE_SIZE;
        this.crops.push({
          position: new THREE.Vector3(cropX, 0.5, cropZ),
          stage: Math.floor(Math.random() * 8),
          sprite: null,
        });
      }
    }

    await this.initCrops();
  }

  async initCrops() {
    const texture = await loadTexture(ASSETS.tiles);

    for (const crop of this.crops) {
      const stageCol = crop.stage % 8;
      const stageRow = Math.floor(crop.stage / 8) + 4; // crops start at row 4 in tilemap

      crop.sprite = createGridSprite(
        texture,
        stageCol, stageRow, 8, 8,
        1.2, 1.2
      );

      crop.sprite.position.copy(crop.position);
      crop.sprite.renderOrder = 5;
      worldGroup.add(crop.sprite);
    }
  }

  dispose() {
    if (this.sprite) {
      this.sprite.material.dispose();
      worldGroup.remove(this.sprite);
    }
    this.crops.forEach(crop => {
      if (crop.sprite) {
        crop.sprite.material.dispose();
        worldGroup.remove(crop.sprite);
      }
    });
  }
}

// ============== MAILBOX CLASS ==============
class Mailbox {
  constructor(x, z) {
    this.position = new THREE.Vector3(x, 0, z);
    this.sprite = null;
    this.flagUp = false;
  }

  async init() {
    const texture = await loadTexture(ASSETS.objects);
    this.sprite = createRegionSprite(
      texture,
      1, 1, 30, 48, // mailbox from farm objects
      CONFIG.PIXELS_PER_UNIT
    );

    this.sprite.position.copy(this.position);
    this.sprite.position.y = 1.5;
    this.sprite.renderOrder = 8;

    worldGroup.add(this.sprite);
  }

  toggleFlag() {
    this.flagUp = !this.flagUp;
    this.updateFlag();

    const indicator = document.getElementById('mailboxIndicator');
    indicator.classList.toggle('has-mail', this.flagUp);
    indicator.classList.toggle('active', this.flagUp);

    if (this.flagUp) {
      document.getElementById('mailboxText').textContent = 'New Mail!';
    } else {
      document.getElementById('mailboxText').textContent = 'No Mail';
    }
  }

  updateFlag() {
    // Would update flag sprite here
  }

  dispose() {
    if (this.sprite) {
      this.sprite.material.dispose();
      worldGroup.remove(this.sprite);
    }
  }
}

// ============== DIALOGUE SYSTEM ==============
const dialogueSystem = {
  isOpen: false,
  currentDialogue: [],
  currentIndex: 0,
  typewriterTimer: null,

  open(agentName, portrait, lines) {
    const box = document.getElementById('dialogueBox');
    const nameEl = document.getElementById('dialogueName');
    const textEl = document.getElementById('dialogueText');
    const portraitCanvas = document.getElementById('dialoguePortrait');
    const ctx = portraitCanvas.getContext('2d');

    nameEl.textContent = agentName;
    this.currentDialogue = lines;
    this.currentIndex = 0;
    this.isOpen = true;

    // Draw portrait if provided
    if (portrait) {
      ctx.clearRect(0, 0, 80, 80);
      ctx.drawImage(portrait, 0, 0, 80, 80);
    }

    box.classList.add('active');
    this.showCurrentLine();
  },

  showCurrentLine() {
    const textEl = document.getElementById('dialogueText');
    const line = this.currentDialogue[this.currentIndex];

    // Typewriter effect
    textEl.textContent = '';
    let charIndex = 0;

    if (this.typewriterTimer) {
      clearInterval(this.typewriterTimer);
    }

    this.typewriterTimer = setInterval(() => {
      if (charIndex < line.length) {
        textEl.textContent += line[charIndex];
        charIndex++;
      } else {
        clearInterval(this.typewriterTimer);
      }
    }, 30);
  },

  next() {
    if (!this.isOpen) return;

    this.currentIndex++;

    if (this.currentIndex >= this.currentDialogue.length) {
      this.close();
    } else {
      this.showCurrentLine();
    }
  },

  close() {
    const box = document.getElementById('dialogueBox');
    box.classList.remove('active');
    this.isOpen = false;
    this.currentDialogue = [];
    this.currentIndex = 0;

    if (this.typewriterTimer) {
      clearInterval(this.typewriterTimer);
    }
  },
};

// ============== WORLD GENERATION ==============
async function createGround() {
  const texture = await loadTexture(ASSETS.tiles);

  // Create grass ground
  const groundGeo = new THREE.PlaneGeometry(100, 100);
  const groundMat = new THREE.MeshBasicMaterial({
    map: createRegionSprite(texture, 0, 0, 16, 16, CONFIG.PIXELS_PER_UNIT).material.map,
  });

  const ground = new THREE.Mesh(groundGeo, groundMat);
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = 0;
  ground.renderOrder = 0;

  worldGroup.add(ground);

  // Add dirt paths
  for (let i = -5; i <= 5; i++) {
    const pathTile = createRegionSprite(texture, 16, 0, 16, 16, CONFIG.PIXELS_PER_UNIT);
    pathTile.position.set(i * CONFIG.TILE_SIZE, 0.05, 8);
    pathTile.scale.set(CONFIG.TILE_SIZE, CONFIG.TILE_SIZE, 1);
    pathTile.renderOrder = 1;
    worldGroup.add(pathTile);
  }

  // Add fence
  for (let i = -15; i <= 15; i++) {
    if (Math.abs(i) > 3 || Math.abs(i) < 3) { // Leave gap for entrance
      const fence = createRegionSprite(texture, 48, 0, 16, 16, CONFIG.PIXELS_PER_UNIT);
      fence.position.set(i * CONFIG.TILE_SIZE * 0.5, 1, -15);
      fence.scale.set(1, 2, 1);
      fence.renderOrder = 7;
      worldGroup.add(fence);
    }
  }
}

// ============== INITIALIZATION ==============
async function init() {
  // Scene setup
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x87CEEB);

  camera = new THREE.OrthographicCamera(
    -window.innerWidth / 50, window.innerWidth / 50,
    window.innerHeight / 50, -window.innerHeight / 50,
    0.1, 1000
  );
  camera.position.set(0, 30, 20);
  camera.lookAt(0, 0, 0);

  renderer = new THREE.WebGLRenderer({
    canvas: document.getElementById('gameCanvas'),
    antialias: true,
  });
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

  raycaster = new THREE.Raycaster();
  mouse = new THREE.Vector2();

  // Groups
  worldGroup = new THREE.Group();
  agentsGroup = new THREE.Group();
  uiGroup = new THREE.Group();

  scene.add(worldGroup);
  scene.add(agentsGroup);
  scene.add(uiGroup);

  // Create world
  await createGround();

  // Create agents
  const farmer = new Agent('Farmer John', 'Farmer', -5, 0, '#8B4513');
  await farmer.init();
  gameState.agents.push(farmer);

  const gardener = new Agent('Gardener Mary', 'Gardener', 0, -3, '#228B22');
  await gardener.init();
  gameState.agents.push(gardener);

  const rancher = new Agent('Rancher Bob', 'Rancher', 5, 0, '#4169E1');
  await rancher.init();
  gameState.agents.push(rancher);

  // Create gardens for each agent
  for (const agent of gameState.agents) {
    const garden = new Garden(
      agent,
      agent.position.x,
      agent.position.z + 4,
      3, 3 // 3x3 garden
    );
    await garden.init();
    gameState.gardens.push(garden);
    agent.garden = garden;
  }

  // Create mailbox
  const mailbox = new Mailbox(0, 12);
  await mailbox.init();
  gameState.mailbox = mailbox;

  // Show click hint
  setTimeout(() => {
    document.getElementById('clickHint').classList.add('visible');
    setTimeout(() => {
      document.getElementById('clickHint').classList.remove('visible');
    }, 5000);
  }, 2000);

  // Event listeners
  window.addEventListener('resize', onWindowResize);
  window.addEventListener('click', onClick);
  window.addEventListener('keydown', onKeyDown);

  // Add some mail
  setTimeout(() => {
    mailbox.toggleFlag();
    gameState.mail.push({
      from: 'Farm Association',
      subject: 'Spring Festival',
      body: ['Dear Farmer,', 'The annual Spring Festival is coming!', 'Bring your best crops to win prizes!'],
    });
  }, 5000);

  animate();
}

// ============== EVENT HANDLERS ==============
function onWindowResize() {
  const aspect = window.innerWidth / window.innerHeight;
  const viewSize = 30;

  camera.left = -viewSize * aspect;
  camera.right = viewSize * aspect;
  camera.top = viewSize;
  camera.bottom = -viewSize;

  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}

function onClick(event) {
  // Update mouse coordinates
  mouse.x = (event.clientX / window.innerWidth) * 2 - 1;
  mouse.y = -(event.clientY / window.innerHeight) * 2 + 1;

  // Check for dialogue close
  if (dialogueSystem.isOpen) {
    dialogueSystem.next();
    return;
  }

  // Raycast for agent clicks
  raycaster.setFromCamera(mouse, camera);
  const intersects = raycaster.intersectObjects(agentsGroup.children);

  if (intersects.length > 0) {
    const clickedSprite = intersects[0].object;
    const agent = clickedSprite.userData.agent;

    if (agent) {
      showAgentDialogue(agent);
    }
  }

  // Check for mailbox click
  const mailboxIntersects = raycaster.intersectObjects(worldGroup.children);
  for (const intersect of mailboxIntersects) {
    if (intersect.object.position.distanceTo(gameState.mailbox.position) < 2) {
      showMailbox();
      return;
    }
  }
}

function onKeyDown(event) {
  if (event.code === 'Space' || event.code === 'Enter') {
    if (dialogueSystem.isOpen) {
      dialogueSystem.next();
    }
  }
  if (event.code === 'Escape') {
    dialogueSystem.close();
  }
}

function showAgentDialogue(agent) {
  const dialogues = {
    'Farmer': [
      `Hello! I'm ${agent.name}, the farmer around here.`,
      'I take care of the crops and make sure everything grows well.',
      'Feel free to walk around my garden!',
    ],
    'Gardener': [
      `Hi there! I'm ${agent.name}, passionate about flowers.`,
      'I believe every plant has its own personality.',
      'Would you like to see my prize-winning roses?',
    ],
    'Rancher': [
      `Howdy! ${agent.name} here, caretaker of all the animals.`,
      'The chickens are especially friendly today!',
      'Always happy to have visitors at the ranch!',
    ],
  };

  const role = agent.role;
  const lines = dialogues[role] || dialogues['Farmer'];

  dialogueSystem.open(agent.name, null, lines);
}

function showMailbox() {
  if (gameState.mail.length === 0) {
    dialogueSystem.open('Mailbox', null, ['No mail right now.', 'Check back later!']);
    return;
  }

  const mail = gameState.mail[0];
  const lines = [`From: ${mail.from}`, `Subject: ${mail.subject}`, '', ...mail.body];

  dialogueSystem.open('📬 Mailbox', null, lines);
  gameState.mailbox.toggleFlag();
}

// ============== GAME LOOP ==============
function updateTime(delta) {
  gameState.time += delta * CONFIG.TIME_SCALE;

  if (gameState.time >= CONFIG.DAY_LENGTH) {
    gameState.time = 0;
    gameState.day++;
  }

  // Update time display
  const hours = Math.floor(gameState.time / 60);
  const minutes = Math.floor(gameState.time % 60);
  const ampm = hours >= 12 ? 'PM' : 'AM';
  const displayHours = hours % 12 || 12;
  const displayMinutes = minutes.toString().padStart(2, '0');

  document.getElementById('timeDisplay').textContent = `Time: ${displayHours}:${displayMinutes} ${ampm}`;
  document.getElementById('seasonDisplay').textContent = `Day ${gameState.day} - ${gameState.season}`;
}

function animate() {
  requestAnimationFrame(animate);

  const delta = clock.getDelta();

  // Update agents
  for (const agent of gameState.agents) {
    // Random movement
    if (!agent.isMoving && Math.random() < 0.005) {
      const newX = agent.position.x + (Math.random() - 0.5) * 4;
      const newZ = agent.position.z + (Math.random() - 0.5) * 4;
      agent.moveTo(newX, newZ);
    }

    agent.update(delta);
  }

  // Update time
  updateTime(delta);

  // Depth sort for proper rendering
  worldGroup.children.sort((a, b) => {
    return (a.position.y + a.position.z * 0.01) - (b.position.y + b.position.z * 0.01);
  });
  agentsGroup.children.sort((a, b) => {
    return (a.position.y + a.position.z * 0.01) - (b.position.y + b.position.z * 0.01);
  });

  renderer.render(scene, camera);
}

// Start the game
init().catch(console.error);
