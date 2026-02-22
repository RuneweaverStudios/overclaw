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

// ============== ASSET PATHS (Configurable) ==============
// Can be overridden via window.ASSET_BASE_URL before init()
const getAssetPath = (relativePath) => {
  const baseUrl = window.ASSET_BASE_URL || '/assets/packs/Farm';
  return `${baseUrl}${relativePath}`;
};

const ASSETS = {
  // Character sprites (PIPOYA)
  characters: {
    male: () => getAssetPath('/PIPOYA FREE RPG Character Sprites 32x32/Male/Male 01-1.png'),
  },
  // Farm tilemap (Tiny Wonder Farm)
  tiles: () => getAssetPath('/Tiny Wonder Farm Free 3/tilemaps/spring farm tilemap.png'),
  // Farm objects
  objects: () => getAssetPath('/Tiny Wonder Farm Free 3/objects&items/farm objects free.png'),
  // Mailbox flag (separate sprite for flag animation)
  mailboxFlagDown: () => getAssetPath('/Tiny Wonder Farm Free 3/objects&items/farm objects free.png'),
  mailboxFlagUp: () => getAssetPath('/Tiny Wonder Farm Free 3/objects&items/farm objects free.png'),
  // UI elements (Sunnyside)
  ui: () => getAssetPath('/Sunnyside_World_ASSET_PACK_V2.1 2/Sunnyside_World_Assets/UI/9slice_box_white/'),
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
  isInitialized: false,
};

// ============== THREE.JS SETUP ==============
let scene, camera, renderer, raycaster, mouse;
let worldGroup, agentsGroup, uiGroup;
let clock = new THREE.Clock();

// ============== TEXTURE CACHE WITH CLEANUP ==============
const textureCache = new Map();
const clonedTextures = new Set(); // Track cloned textures for cleanup

function loadTexture(url) {
  return new Promise((resolve, reject) => {
    if (textureCache.has(url)) {
      resolve(textureCache.get(url));
      return;
    }

    new THREE.TextureLoader().load(
      url,
      (texture) => {
        texture.magFilter = THREE.NearestFilter;
        texture.minFilter = THREE.NearestFilter;
        textureCache.set(url, texture);
        resolve(texture);
      },
      undefined,
      (error) => {
        console.error(`Failed to load texture: ${url}`, error);
        reject(error);
      }
    );
  });
}

function cleanupTextureCache() {
  // Dispose all cached textures
  for (const [url, texture] of textureCache) {
    texture.dispose();
  }
  textureCache.clear();

  // Dispose all cloned textures
  for (const texture of clonedTextures) {
    if (texture) texture.dispose();
  }
  clonedTextures.clear();
}

// ============== DOM NULL CHECKS ==============
function safeGetElement(id) {
  const el = document.getElementById(id);
  if (!el) {
    console.warn(`DOM element not found: ${id}`);
  }
  return el;
}

// ============== SPRITE UTILITIES ==============
function createGridSprite(texture, col, row, cols, rows, width, height) {
  const clonedTex = texture.clone();
  clonedTextures.add(clonedTex);

  clonedTex.repeat.set(1 / cols, 1 / rows);
  clonedTex.offset.set(col / cols, 1 - (row + 1) / rows);
  clonedTex.needsUpdate = true;

  const material = new THREE.SpriteMaterial({
    map: clonedTex,
    transparent: true,
    depthTest: false,
  });

  const sprite = new THREE.Sprite(material);
  sprite.scale.set(width, height, 1);

  return sprite;
}

function createRegionSprite(texture, px, py, pw, ph, ppu) {
  if (!texture || !texture.image) {
    console.warn('Invalid texture for region sprite');
    return null;
  }

  const clonedTex = texture.clone();
  clonedTextures.add(clonedTex);

  clonedTex.repeat.set(pw / texture.image.width, ph / texture.image.height);
  clonedTex.offset.set(px / texture.image.width, 1 - (py + ph) / texture.image.height);
  clonedTex.needsUpdate = true;

  const material = new THREE.SpriteMaterial({
    map: clonedTex,
    transparent: true,
    depthTest: false,
  });

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
    try {
      const texture = await loadTexture(ASSETS.characters.male());
      this.sprite = createGridSprite(
        texture,
        0, 0, 4, 4, // col, row, cols, rows
        CONFIG.AGENT_SCALE,
        CONFIG.AGENT_SCALE * 1.5
      );

      if (!this.sprite) {
        throw new Error('Failed to create agent sprite');
      }

      this.sprite.position.copy(this.position);
      this.sprite.position.y = CONFIG.AGENT_SCALE * 0.75;
      this.sprite.userData.agent = this;
      this.sprite.renderOrder = 10;

      agentsGroup.add(this.sprite);
    } catch (error) {
      console.error(`Failed to initialize agent ${this.name}:`, error);
    }
  }

  update(delta) {
    if (!this.sprite) return;

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
    const bubble = safeGetElement('thoughtBubble');
    const text = safeGetElement('thoughtText');

    if (!bubble || !text || !this.sprite || !camera) return;

    const thought = this.thoughts[Math.floor(Math.random() * this.thoughts.length)];
    this.currentThought = thought;
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
      if (this.sprite.material.map) {
        clonedTextures.delete(this.sprite.material.map);
        this.sprite.material.map.dispose();
      }
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
    try {
      const texture = await loadTexture(ASSETS.tiles());
      this.sprite = createRegionSprite(
        texture,
        32, 32, 16 * this.width, 16 * this.height, // tilled soil from tilemap
        CONFIG.PIXELS_PER_UNIT
      );

      if (!this.sprite) {
        throw new Error('Failed to create garden sprite');
      }

      this.sprite.position.copy(this.position);
      this.sprite.scale.set(
        this.width * CONFIG.TILE_SIZE,
        this.height * CONFIG.TILE_SIZE,
        1
      );
      this.sprite.renderOrder = 2;

      worldGroup.add(this.sprite);

      // Create crops
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
    } catch (error) {
      console.error(`Failed to initialize garden for ${this.owner?.name}:`, error);
    }
  }

  async initCrops() {
    try {
      const texture = await loadTexture(ASSETS.tiles());

      for (const crop of this.crops) {
        const stageCol = crop.stage % 8;
        const stageRow = Math.floor(crop.stage / 8) + 4; // crops start at row 4 in tilemap

        crop.sprite = createGridSprite(
          texture,
          stageCol, stageRow, 8, 8,
          1.2, 1.2
        );

        if (crop.sprite) {
          crop.sprite.position.copy(crop.position);
          crop.sprite.renderOrder = 5;
          worldGroup.add(crop.sprite);
        }
      }
    } catch (error) {
      console.error('Failed to initialize crops:', error);
    }
  }

  dispose() {
    if (this.sprite) {
      if (this.sprite.material.map) {
        clonedTextures.delete(this.sprite.material.map);
        this.sprite.material.map.dispose();
      }
      this.sprite.material.dispose();
      worldGroup.remove(this.sprite);
    }
    this.crops.forEach(crop => {
      if (crop.sprite) {
        if (crop.sprite.material.map) {
          clonedTextures.delete(crop.sprite.material.map);
          crop.sprite.material.map.dispose();
        }
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
    this.flagSprite = null;
    this.flagUp = false;
  }

  async init() {
    try {
      const texture = await loadTexture(ASSETS.objects());
      this.sprite = createRegionSprite(
        texture,
        1, 1, 30, 48, // mailbox from farm objects
        CONFIG.PIXELS_PER_UNIT
      );

      if (!this.sprite) {
        throw new Error('Failed to create mailbox sprite');
      }

      this.sprite.position.copy(this.position);
      this.sprite.position.y = 1.5;
      this.sprite.renderOrder = 8;

      worldGroup.add(this.sprite);

      // Create flag sprite
      this.flagSprite = createRegionSprite(
        texture,
        35, 5, 8, 12, // flag from farm objects
        CONFIG.PIXELS_PER_UNIT
      );

      if (this.flagSprite) {
        this.flagSprite.position.set(
          this.position.x + 0.3,
          this.position.y + 1.2,
          this.position.z
        );
        this.flagSprite.visible = false;
        this.flagSprite.renderOrder = 9;
        worldGroup.add(this.flagSprite);
      }
    } catch (error) {
      console.error('Failed to initialize mailbox:', error);
    }
  }

  toggleFlag() {
    this.flagUp = !this.flagUp;
    this.updateFlag();

    const indicator = safeGetElement('mailboxIndicator');
    const mailboxText = safeGetElement('mailboxText');

    if (indicator) {
      indicator.classList.toggle('has-mail', this.flagUp);
      indicator.classList.toggle('active', this.flagUp);
    }

    if (mailboxText) {
      mailboxText.textContent = this.flagUp ? 'New Mail!' : 'No Mail';
    }
  }

  updateFlag() {
    if (this.flagSprite) {
      this.flagSprite.visible = this.flagUp;
    }
  }

  dispose() {
    if (this.sprite) {
      if (this.sprite.material.map) {
        clonedTextures.delete(this.sprite.material.map);
        this.sprite.material.map.dispose();
      }
      this.sprite.material.dispose();
      worldGroup.remove(this.sprite);
    }
    if (this.flagSprite) {
      if (this.flagSprite.material.map) {
        clonedTextures.delete(this.flagSprite.material.map);
        this.flagSprite.material.map.dispose();
      }
      this.flagSprite.material.dispose();
      worldGroup.remove(this.flagSprite);
    }
  }
}

// ============== DIALOGUE SYSTEM WITH MEMORY LEAK FIX ==============
const dialogueSystem = {
  isOpen: false,
  currentDialogue: [],
  currentIndex: 0,
  typewriterTimer: null,

  open(agentName, portrait, lines) {
    const box = safeGetElement('dialogueBox');
    const nameEl = safeGetElement('dialogueName');
    const textEl = safeGetElement('dialogueText');
    const portraitCanvas = safeGetElement('dialoguePortrait');

    if (!box || !nameEl || !textEl) {
      console.error('Required dialogue elements not found');
      return;
    }

    // Clear any existing timer first
    this.clearTimer();

    nameEl.textContent = agentName;
    this.currentDialogue = lines;
    this.currentIndex = 0;
    this.isOpen = true;

    // Draw portrait if provided
    if (portrait && portraitCanvas) {
      const ctx = portraitCanvas.getContext('2d');
      if (ctx) {
        ctx.clearRect(0, 0, 80, 80);
        ctx.drawImage(portrait, 0, 0, 80, 80);
      }
    }

    box.classList.add('active');
    this.showCurrentLine();
  },

  showCurrentLine() {
    const textEl = safeGetElement('dialogueText');
    if (!textEl) return;

    const line = this.currentDialogue[this.currentIndex];

    // Clear any existing timer first
    this.clearTimer();

    // Typewriter effect
    textEl.textContent = '';
    let charIndex = 0;

    this.typewriterTimer = setInterval(() => {
      if (charIndex < line.length) {
        textEl.textContent += line[charIndex];
        charIndex++;
      } else {
        this.clearTimer();
      }
    }, 30);
  },

  clearTimer() {
    if (this.typewriterTimer) {
      clearInterval(this.typewriterTimer);
      this.typewriterTimer = null;
    }
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
    this.clearTimer();

    const box = safeGetElement('dialogueBox');
    if (box) {
      box.classList.remove('active');
    }

    this.isOpen = false;
    this.currentDialogue = [];
    this.currentIndex = 0;
  },
};

// ============== WORLD GENERATION ==============
async function createGround() {
  try {
    const texture = await loadTexture(ASSETS.tiles());

    // Create grass ground
    const groundGeo = new THREE.PlaneGeometry(100, 100);
    const groundMat = new THREE.MeshBasicMaterial({
      map: createRegionSprite(texture, 0, 0, 16, 16, CONFIG.PIXELS_PER_UNIT)?.material.map,
    });

    const ground = new THREE.Mesh(groundGeo, groundMat);
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = 0;
    ground.renderOrder = 0;

    worldGroup.add(ground);

    // Add dirt paths
    for (let i = -5; i <= 5; i++) {
      const pathTile = createRegionSprite(texture, 16, 0, 16, 16, CONFIG.PIXELS_PER_UNIT);
      if (pathTile) {
        pathTile.position.set(i * CONFIG.TILE_SIZE, 0.05, 8);
        pathTile.scale.set(CONFIG.TILE_SIZE, CONFIG.TILE_SIZE, 1);
        pathTile.renderOrder = 1;
        worldGroup.add(pathTile);
      }
    }

    // Add fence
    for (let i = -15; i <= 15; i++) {
      if (Math.abs(i) > 3 || Math.abs(i) < 3) { // Leave gap for entrance
        const fence = createRegionSprite(texture, 48, 0, 16, 16, CONFIG.PIXELS_PER_UNIT);
        if (fence) {
          fence.position.set(i * CONFIG.TILE_SIZE * 0.5, 1, -15);
          fence.scale.set(1, 2, 1);
          fence.renderOrder = 7;
          worldGroup.add(fence);
        }
      }
    }
  } catch (error) {
    console.error('Failed to create ground:', error);
  }
}

// ============== DEPTH SORTING (Respects renderOrder) ==============
function depthSortChildren(group) {
  if (!group || !group.children) return;

  group.children.sort((a, b) => {
    // First sort by renderOrder
    if (a.renderOrder !== undefined && b.renderOrder !== undefined) {
      if (a.renderOrder !== b.renderOrder) {
        return a.renderOrder - b.renderOrder;
      }
    }

    // Then by depth (y + slight z bias for 2.5D effect)
    const depthA = (a.position?.y ?? 0) + ((a.position?.z ?? 0) * 0.01);
    const depthB = (b.position?.y ?? 0) + ((b.position?.z ?? 0) * 0.01);

    return depthA - depthB;
  });
}

// ============== CLEANUP ==============
function cleanup() {
  // Dispose agents
  for (const agent of gameState.agents) {
    agent.dispose();
  }

  // Dispose gardens
  for (const garden of gameState.gardens) {
    garden.dispose();
  }

  // Dispose mailbox
  if (gameState.mailbox) {
    gameState.mailbox.dispose();
  }

  // Clear dialogue timer
  dialogueSystem.clearTimer();

  // Cleanup textures
  cleanupTextureCache();

  // Dispose renderer
  if (renderer) {
    renderer.dispose();
  }
}

// ============== INITIALIZATION ==============
async function init() {
  try {
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

    const canvas = document.getElementById('gameCanvas');
    if (!canvas) {
      throw new Error('Game canvas not found');
    }

    renderer = new THREE.WebGLRenderer({
      canvas,
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
      try {
        const garden = new Garden(
          agent,
          agent.position.x,
          agent.position.z + 4,
          3, 3 // 3x3 garden
        );
        await garden.init();
        gameState.gardens.push(garden);
        agent.garden = garden;
      } catch (error) {
        console.error(`Failed to create garden for ${agent.name}:`, error);
        // Continue with other gardens even if one fails
      }
    }

    // Create mailbox
    const mailbox = new Mailbox(0, 12);
    await mailbox.init();
    gameState.mailbox = mailbox;

    // Show click hint
    const clickHint = safeGetElement('clickHint');
    if (clickHint) {
      setTimeout(() => {
        clickHint.classList.add('visible');
        setTimeout(() => {
          clickHint.classList.remove('visible');
        }, 5000);
      }, 2000);
    }

    // Event listeners
    window.addEventListener('resize', onWindowResize);
    window.addEventListener('click', onClick);
    window.addEventListener('keydown', onKeyDown);

    // Cleanup on page unload
    window.addEventListener('beforeunload', cleanup);

    // Add some mail
    setTimeout(() => {
      if (gameState.mailbox) {
        gameState.mailbox.toggleFlag();
      }
      gameState.mail.push({
        from: 'Farm Association',
        subject: 'Spring Festival',
        body: ['Dear Farmer,', 'The annual Spring Festival is coming!', 'Bring your best crops to win prizes!'],
      });
    }, 5000);

    gameState.isInitialized = true;
    animate();
  } catch (error) {
    console.error('Failed to initialize game:', error);
    cleanup();
  }
}

// ============== EVENT HANDLERS ==============
function onWindowResize() {
  if (!camera || !renderer) return;

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
  if (!raycaster || !camera || !mouse) return;

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
  if (gameState.mailbox) {
    const mailboxIntersects = raycaster.intersectObjects(worldGroup.children);
    for (const intersect of mailboxIntersects) {
      if (intersect.object.position.distanceTo(gameState.mailbox.position) < 2) {
        showMailbox();
        return;
      }
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

  if (gameState.mailbox) {
    gameState.mailbox.toggleFlag();
  }
}

// ============== GAME LOOP ==============
function updateTime(delta) {
  gameState.time += delta * CONFIG.TIME_SCALE;

  if (gameState.time >= CONFIG.DAY_LENGTH) {
    gameState.time = 0;
    gameState.day++;
  }

  // Update time display with null checks
  const timeDisplay = safeGetElement('timeDisplay');
  const seasonDisplay = safeGetElement('seasonDisplay');

  if (timeDisplay || seasonDisplay) {
    const hours = Math.floor(gameState.time / 60);
    const minutes = Math.floor(gameState.time % 60);
    const ampm = hours >= 12 ? 'PM' : 'AM';
    const displayHours = hours % 12 || 12;
    const displayMinutes = minutes.toString().padStart(2, '0');

    if (timeDisplay) {
      timeDisplay.textContent = `Time: ${displayHours}:${displayMinutes} ${ampm}`;
    }
    if (seasonDisplay) {
      seasonDisplay.textContent = `Day ${gameState.day} - ${gameState.season}`;
    }
  }
}

function animate() {
  requestAnimationFrame(animate);

  const delta = clock.getDelta();

  // Update agents
  for (const agent of gameState.agents) {
    if (!agent.isMoving && Math.random() < 0.005) {
      const newX = agent.position.x + (Math.random() - 0.5) * 4;
      const newZ = agent.position.z + (Math.random() - 0.5) * 4;
      agent.moveTo(newX, newZ);
    }

    agent.update(delta);
  }

  // Update time
  updateTime(delta);

  // Depth sort for proper rendering (respects renderOrder)
  depthSortChildren(worldGroup);
  depthSortChildren(agentsGroup);

  if (renderer && scene && camera) {
    renderer.render(scene, camera);
  }
}

// Start the game
init().catch(console.error);

// Export for testing
if (typeof window !== 'undefined') {
  window.FarmSim = {
    Agent,
    Garden,
    Mailbox,
    dialogueSystem,
    gameState,
    cleanup,
  };
}
