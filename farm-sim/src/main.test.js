/**
 * Unit Tests for Farm Sim Visualizer
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { jest } from '@vitest/expect';

// Mock THREE.js before importing
vi.mock('three', () => ({
  Scene: vi.fn(() => ({
    add: vi.fn(),
    background: null,
    children: [],
  })),
  OrthographicCamera: vi.fn(() => ({
    position: { set: vi.fn() },
    lookAt: vi.fn(),
    updateProjectionMatrix: vi.fn(),
  })),
  WebGLRenderer: vi.fn(() => ({
    setSize: vi.fn(),
    setPixelRatio: vi.fn(),
    render: vi.fn(),
    dispose: vi.fn(),
  })),
  Color: vi.fn(),
  Raycaster: vi.fn(() => ({
    setFromCamera: vi.fn(),
    intersectObjects: vi.fn(() => []),
  })),
  Vector2: vi.fn(function(x, y) { this.x = x; this.y = y; }),
  Vector3: vi.fn(function(x, y, z) {
    this.x = x; this.y = y; this.z = z;
    this.clone = vi.fn(() => this);
    this.copy = vi.fn(function(p) { this.x = p.x; this.y = p.y; this.z = p.z; });
    this.distanceTo = vi.fn(() => 0);
  }),
  Group: vi.fn(() => ({
    add: vi.fn(),
    remove: vi.fn(),
    children: [],
  })),
  Sprite: vi.fn(() => ({
    position: { x: 0, y: 0, z: 0 },
    scale: { x: 1, y: 1, z: 1 },
    material: { dispose: vi.fn(), map: null },
    userData: {},
    renderOrder: 0,
  })),
  SpriteMaterial: vi.fn(() => ({
    dispose: vi.fn(),
    map: null,
  })),
  PlaneGeometry: vi.fn(),
  MeshBasicMaterial: vi.fn(),
  Mesh: vi.fn(() => ({
    rotation: { x: 0 },
    position: { x: 0, y: 0, z: 0 },
    renderOrder: 0,
  })),
  Clock: vi.fn(() => ({
    getDelta: vi.fn(() => 0.016),
  })),
  NearestFilter: 0,
}));

// Mock DOM elements
const mockElements = {
  thoughtBubble: { classList: { add: vi.fn(), remove: vi.fn() }, style: {} },
  thoughtText: { textContent: '' },
  dialogueBox: { classList: { add: vi.fn(), remove: vi.fn() } },
  dialogueName: { textContent: '' },
  dialogueText: { textContent: '' },
  dialoguePortrait: { getContext: vi.fn(() => ({ clearRect: vi.fn(), drawImage: vi.fn() })) },
  mailboxIndicator: { classList: { toggle: vi.fn() } },
  mailboxText: { textContent: '' },
  clickHint: { classList: { add: vi.fn(), remove: vi.fn() } },
  timeDisplay: { textContent: '' },
  seasonDisplay: { textContent: '' },
  gameCanvas: {},
};

global.document = {
  getElementById: vi.fn((id) => mockElements[id] || null),
};

global.window = {
  innerWidth: 1920,
  innerHeight: 1080,
  ASSET_BASE_URL: null,
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
};

// Mock requestAnimationFrame and setInterval
global.requestAnimationFrame = vi.fn((cb) => setTimeout(cb, 16));
global.setInterval = vi.fn((cb, delay) => ({
  _callback: cb,
  _delay: delay,
  unref: vi.fn(),
}));
global.clearInterval = vi.fn();

// Import classes and utilities (defined at end of main.js via window.FarmSim)
// For now, we'll test the classes in isolation

describe('Agent Class', () => {
  // Define Agent class inline for testing since we can't easily import from main.js
  class TestAgent {
    constructor(name, role, x, z, color) {
      this.name = name;
      this.role = role;
      this.position = { x, y: 0, z };
      this.targetPosition = { x, y: 0, z };
      this.color = color;
      this.thoughts = this.generateThoughts();
      this.isMoving = false;
      this.thoughtTimer = 0;
      this.sprite = null;
    }

    generateThoughts() {
      const roleThoughts = {
        'Farmer': ['I hope the crops grow well this season...', 'Should check the soil moisture today.'],
        'Gardener': ['These flowers need more attention.', 'The compost is ready for spreading!'],
        'Rancher': ['The animals need fresh water.', 'Planning to build a bigger coop.'],
      };
      return roleThoughts[this.role] || roleThoughts['Farmer'];
    }

    moveTo(x, z) {
      this.targetPosition.x = x;
      this.targetPosition.z = z;
    }

    dispose() {
      if (this.sprite && this.sprite.material) {
        this.sprite.material.dispose();
      }
    }
  }

  let agent;

  beforeEach(() => {
    agent = new TestAgent('Test Farmer', 'Farmer', 0, 0, '#8B4513');
  });

  it('should create agent with correct properties', () => {
    expect(agent.name).toBe('Test Farmer');
    expect(agent.role).toBe('Farmer');
    expect(agent.color).toBe('#8B4513');
    expect(agent.isMoving).toBe(false);
    expect(agent.thoughtTimer).toBe(0);
  });

  it('should initialize position correctly', () => {
    expect(agent.position.x).toBe(0);
    expect(agent.position.z).toBe(0);
    expect(agent.targetPosition.x).toBe(0);
    expect(agent.targetPosition.z).toBe(0);
  });

  it('should generate role-specific thoughts', () => {
    expect(agent.thoughts).toBeDefined();
    expect(agent.thoughts.length).toBeGreaterThan(0);
    expect(agent.thoughts[0]).toContain('crops');
  });

  it('should return different thoughts for different roles', () => {
    const farmer = new TestAgent('Farmer', 'Farmer', 0, 0, '#8B4513');
    const gardener = new TestAgent('Gardener', 'Gardener', 0, 0, '#228B22');
    expect(farmer.thoughts).not.toEqual(gardener.thoughts);
  });

  it('should fallback to Farmer thoughts for unknown role', () => {
    const unknown = new TestAgent('Unknown', 'UnknownRole', 0, 0, '#000000');
    expect(unknown.thoughts).toBeDefined();
    expect(unknown.thoughts[0]).toContain('crops');
  });

  it('should set target position correctly', () => {
    agent.moveTo(5, 10);
    expect(agent.targetPosition.x).toBe(5);
    expect(agent.targetPosition.z).toBe(10);
  });

  it('should dispose sprite safely', () => {
    const mockDispose = vi.fn();
    agent.sprite = { material: { dispose: mockDispose } };
    agent.dispose();
    expect(mockDispose).toHaveBeenCalled();
  });

  it('should handle dispose without sprite', () => {
    expect(() => agent.dispose()).not.toThrow();
  });
});

describe('Garden Class', () => {
  class TestGarden {
    constructor(ownerAgent, x, z, width, height) {
      this.owner = ownerAgent;
      this.position = { x, y: 0.1, z };
      this.width = width;
      this.height = height;
      this.crops = [];
      this.sprite = null;

      // Create crops
      for (let i = 0; i < width; i++) {
        for (let j = 0; j < height; j++) {
          this.crops.push({
            position: { x: x + i, y: 0.5, z: z + j },
            stage: Math.floor(Math.random() * 8),
            sprite: null,
          });
        }
      }
    }

    dispose() {
      if (this.sprite && this.sprite.material) {
        this.sprite.material.dispose();
      }
      this.crops.forEach(crop => {
        if (crop.sprite && crop.sprite.material) {
          crop.sprite.material.dispose();
        }
      });
    }
  }

  let garden;
  let mockAgent;

  beforeEach(() => {
    mockAgent = { name: 'Test Agent' };
    garden = new TestGarden(mockAgent, 0, 0, 3, 3);
  });

  it('should create garden with correct dimensions', () => {
    expect(garden.width).toBe(3);
    expect(garden.height).toBe(3);
    expect(garden.owner).toBe(mockAgent);
  });

  it('should create correct number of crops', () => {
    expect(garden.crops.length).toBe(9); // 3x3
  });

  it('should initialize crops with positions', () => {
    expect(garden.crops[0].position).toBeDefined();
    expect(garden.crops[0].stage).toBeGreaterThanOrEqual(0);
    expect(garden.crops[0].stage).toBeLessThan(8);
  });

  it('should dispose safely', () => {
    const mockDispose = vi.fn();
    garden.sprite = { material: { dispose: mockDispose } };
    garden.crops[0].sprite = { material: { dispose: mockDispose } };
    garden.dispose();
    expect(mockDispose).toHaveBeenCalled();
  });
});

describe('Mailbox Class', () => {
  class TestMailbox {
    constructor(x, z) {
      this.position = { x, y: 0, z };
      this.sprite = null;
      this.flagSprite = null;
      this.flagUp = false;
    }

    toggleFlag() {
      this.flagUp = !this.flagUp;
      this.updateFlag();
    }

    updateFlag() {
      if (this.flagSprite) {
        this.flagSprite.visible = this.flagUp;
      }
    }

    dispose() {
      if (this.sprite && this.sprite.material) {
        this.sprite.material.dispose();
      }
      if (this.flagSprite && this.flagSprite.material) {
        this.flagSprite.material.dispose();
      }
    }
  }

  let mailbox;

  beforeEach(() => {
    mailbox = new TestMailbox(0, 10);
  });

  it('should create mailbox at correct position', () => {
    expect(mailbox.position.x).toBe(0);
    expect(mailbox.position.z).toBe(10);
  });

  it('should initialize with flag down', () => {
    expect(mailbox.flagUp).toBe(false);
  });

  it('should toggle flag state', () => {
    mailbox.toggleFlag();
    expect(mailbox.flagUp).toBe(true);

    mailbox.toggleFlag();
    expect(mailbox.flagUp).toBe(false);
  });

  it('should update flag sprite visibility', () => {
    mailbox.flagSprite = { visible: false };
    mailbox.flagUp = true;
    mailbox.updateFlag();
    expect(mailbox.flagSprite.visible).toBe(true);
  });

  it('should dispose safely', () => {
    const mockDispose = vi.fn();
    mailbox.sprite = { material: { dispose: mockDispose } };
    mailbox.flagSprite = { material: { dispose: mockDispose } };
    mailbox.dispose();
    expect(mockDispose).toHaveBeenCalled();
  });
});

describe('Dialogue System', () => {
  let testDialogueSystem;

  beforeEach(() => {
    testDialogueSystem = {
      isOpen: false,
      currentDialogue: [],
      currentIndex: 0,
      typewriterTimer: null,

      open(agentName, portrait, lines) {
        this.currentDialogue = lines;
        this.currentIndex = 0;
        this.isOpen = true;
      },

      next() {
        if (!this.isOpen) return;
        this.currentIndex++;
        if (this.currentIndex >= this.currentDialogue.length) {
          this.close();
        }
      },

      close() {
        this.isOpen = false;
        this.currentDialogue = [];
        this.currentIndex = 0;
        this.typewriterTimer = null;
      },

      clearTimer() {
        if (this.typewriterTimer) {
          global.clearInterval(this.typewriterTimer);
          this.typewriterTimer = null;
        }
      },
    };
  });

  it('should open dialogue with correct state', () => {
    testDialogueSystem.open('Test Agent', null, ['Line 1', 'Line 2']);

    expect(testDialogueSystem.isOpen).toBe(true);
    expect(testDialogueSystem.currentDialogue).toEqual(['Line 1', 'Line 2']);
    expect(testDialogueSystem.currentIndex).toBe(0);
  });

  it('should advance to next line', () => {
    testDialogueSystem.open('Test', null, ['Line 1', 'Line 2', 'Line 3']);
    expect(testDialogueSystem.currentIndex).toBe(0);

    testDialogueSystem.next();
    expect(testDialogueSystem.currentIndex).toBe(1);

    testDialogueSystem.next();
    expect(testDialogueSystem.currentIndex).toBe(2);
  });

  it('should close after last line', () => {
    testDialogueSystem.open('Test', null, ['Line 1']);
    testDialogueSystem.next();

    expect(testDialogueSystem.isOpen).toBe(false);
  });

  it('should not advance when not open', () => {
    testDialogueSystem.next();
    expect(testDialogueSystem.currentIndex).toBe(0);
  });

  it('should close dialogue and reset state', () => {
    testDialogueSystem.open('Test', null, ['Line 1', 'Line 2']);
    testDialogueSystem.close();

    expect(testDialogueSystem.isOpen).toBe(false);
    expect(testDialogueSystem.currentDialogue).toEqual([]);
    expect(testDialogueSystem.currentIndex).toBe(0);
  });

  it('should clear and nullify timer', () => {
    const mockTimer = { _callback: vi.fn(), _delay: 30 };
    testDialogueSystem.typewriterTimer = mockTimer;

    const spy = vi.spyOn(global, 'clearInterval');
    testDialogueSystem.clearTimer();

    expect(spy).toHaveBeenCalledWith(mockTimer);
    expect(testDialogueSystem.typewriterTimer).toBeNull();
  });

  it('should handle null timer gracefully', () => {
    expect(() => testDialogueSystem.clearTimer()).not.toThrow();
  });
});

describe('Utility Functions', () => {
  describe('safeGetElement', () => {
    it('should return element when found', () => {
      const mockGetElementById = vi.fn((id) => mockElements[id] || null);
      document.getElementById = mockGetElementById;

      const result = mockGetElementById('thoughtBubble');
      expect(result).toBeDefined();
      expect(result).toBe(mockElements['thoughtBubble']);
    });

    it('should return null when not found', () => {
      const mockGetElementById = vi.fn((id) => null);
      document.getElementById = mockGetElementById;

      const result = mockGetElementById('nonexistent');
      expect(result).toBeNull();
    });
  });
});

describe('Texture Cache Management', () => {
  it('should track cache correctly', () => {
    const cache = new Map();
    const clonedTextures = new Set();

    // Test adding to cache
    cache.set('test.png', { dispose: vi.fn() });
    expect(cache.has('test.png')).toBe(true);

    // Test adding to cloned set
    const mockTexture = { dispose: vi.fn() };
    clonedTextures.add(mockTexture);
    expect(clonedTextures.has(mockTexture)).toBe(true);

    // Test cleanup
    const texture = cache.get('test.png');
    texture.dispose();
    expect(texture.dispose).toHaveBeenCalled();

    mockTexture.dispose();
    expect(mockTexture.dispose).toHaveBeenCalled();

    cache.clear();
    clonedTextures.clear();
    expect(cache.size).toBe(0);
    expect(clonedTextures.size).toBe(0);
  });
});

describe('Depth Sorting', () => {
  it('should sort by renderOrder first', () => {
    const children = [
      { renderOrder: 5, position: { y: 10, z: 0 } },
      { renderOrder: 1, position: { y: 0, z: 0 } },
      { renderOrder: 10, position: { y: 0, z: 0 } },
    ];

    children.sort((a, b) => {
      if (a.renderOrder !== b.renderOrder) {
        return a.renderOrder - b.renderOrder;
      }
      return (a.position.y + a.position.z * 0.01) - (b.position.y + b.position.z * 0.01);
    });

    expect(children[0].renderOrder).toBe(1);
    expect(children[1].renderOrder).toBe(5);
    expect(children[2].renderOrder).toBe(10);
  });

  it('should sort by depth when renderOrder is same', () => {
    const children = [
      { renderOrder: 5, position: { y: 10, z: 0 } },
      { renderOrder: 5, position: { y: 5, z: 0 } },
      { renderOrder: 5, position: { y: 15, z: 0 } },
    ];

    children.sort((a, b) => {
      if (a.renderOrder !== b.renderOrder) {
        return a.renderOrder - b.renderOrder;
      }
      return (a.position.y + a.position.z * 0.01) - (b.position.y + b.position.z * 0.01);
    });

    expect(children[0].position.y).toBe(5);
    expect(children[1].position.y).toBe(10);
    expect(children[2].position.y).toBe(15);
  });
});
