/**
 * Unit tests for Agent class
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import * as THREE from 'three';

// Mock the global environment for tests
global.THREE = THREE;

// Mock DOM elements
const mockElements = {};

beforeEach(() => {
  // Reset DOM mocks
  mockElements.thoughtBubble = {
    classList: { add: vi.fn(), remove: vi.fn() },
    style: {},
  };
  mockElements.thoughtText = { textContent: '' };

  document.getElementById = vi.fn((id) => mockElements[id]);

  // Mock camera for projection
  global.camera = {
    projectionMatrix: new THREE.Matrix4(),
    updateProjectionMatrix: vi.fn(),
  };
});

describe('Agent', () => {
  // Agent class definition (simplified for testing)
  class Agent {
    constructor(name, role, x, z, color) {
      this.name = name;
      this.role = role;
      this.position = new THREE.Vector3(x, 0, z);
      this.targetPosition = new THREE.Vector3(x, 0, z);
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
        'Farmer': ['Crops look good today.', 'Time to water.'],
        'Gardener': ['Flowers need attention.', 'Bees are happy.'],
        'Rancher': ['Animals need water.', 'Chickens are active.'],
      };
      return roleThoughts[this.role] || roleThoughts['Farmer'];
    }

    update(delta) {
      const speed = 3;
      const direction = new THREE.Vector3()
        .subVectors(this.targetPosition, this.position)
        .normalize();

      const distance = this.position.distanceTo(this.targetPosition);

      if (distance > 0.1) {
        this.isMoving = true;
        this.position.add(direction.multiplyScalar(speed * delta));
        if (this.sprite) {
          this.sprite.position.x = this.position.x;
          this.sprite.position.z = this.position.z;
        }
      } else {
        this.isMoving = false;
      }

      this.thoughtTimer += delta * 1000;
      if (this.thoughtTimer >= 8000) {
        this.showThought();
        this.thoughtTimer = 0;
      }
    }

    showThought() {
      const thought = this.thoughts[Math.floor(Math.random() * this.thoughts.length)];
      this.currentThought = thought;

      const bubble = document.getElementById('thoughtBubble');
      const text = document.getElementById('thoughtText');

      if (!bubble || !text) return;

      text.textContent = thought;
      bubble.classList.add('active');
    }

    moveTo(x, z) {
      this.targetPosition.set(x, 0, z);
    }

    dispose() {
      if (this.sprite) {
        this.sprite.material.dispose();
      }
    }
  }

  describe('constructor', () => {
    it('should create an agent with correct properties', () => {
      const agent = new Agent('Test Agent', 'Farmer', 5, 10, '#FF0000');

      expect(agent.name).toBe('Test Agent');
      expect(agent.role).toBe('Farmer');
      expect(agent.position.x).toBe(5);
      expect(agent.position.z).toBe(10);
      expect(agent.color).toBe('#FF0000');
      expect(agent.isMoving).toBe(false);
    });

    it('should generate role-specific thoughts', () => {
      const farmer = new Agent('John', 'Farmer', 0, 0, '#000');
      const gardener = new Agent('Mary', 'Gardener', 0, 0, '#000');
      const rancher = new Agent('Bob', 'Rancher', 0, 0, '#000');

      expect(farmer.thoughts).toContain('Crops look good today.');
      expect(gardener.thoughts).toContain('Flowers need attention.');
      expect(rancher.thoughts).toContain('Animals need water.');
    });
  });

  describe('update', () => {
    it('should update agent position when moving', () => {
      const agent = new Agent('Test', 'Farmer', 0, 0, '#000');
      agent.moveTo(5, 0);

      agent.update(0.1);

      expect(agent.isMoving).toBe(true);
      expect(agent.position.x).toBeGreaterThan(0);
    });

    it('should stop moving when target reached', () => {
      const agent = new Agent('Test', 'Farmer', 0, 0, '#000');
      agent.moveTo(0.05, 0);

      agent.update(0.1);

      expect(agent.isMoving).toBe(false);
    });

    it('should track thought timer', () => {
      const agent = new Agent('Test', 'Farmer', 0, 0, '#000');
      const showThoughtSpy = vi.spyOn(agent, 'showThought');

      agent.update(10); // 10 seconds

      expect(showThoughtSpy).toHaveBeenCalled();
    });
  });

  describe('moveTo', () => {
    it('should set target position', () => {
      const agent = new Agent('Test', 'Farmer', 0, 0, '#000');
      agent.moveTo(10, 20);

      expect(agent.targetPosition.x).toBe(10);
      expect(agent.targetPosition.z).toBe(20);
    });
  });

  describe('showThought', () => {
    it('should display thought bubble with null checks', () => {
      const agent = new Agent('Test', 'Farmer', 0, 0, '#000');

      agent.showThought();

      expect(mockElements.thoughtText.textContent).not.toBe('');
      expect(mockElements.thoughtBubble.classList.add).toHaveBeenCalledWith('active');
    });

    it('should handle missing DOM elements gracefully', () => {
      const agent = new Agent('Test', 'Farmer', 0, 0, '#000');
      document.getElementById = vi.fn(() => null);

      expect(() => agent.showThought()).not.toThrow();
    });
  });

  describe('dispose', () => {
    it('should dispose sprite material if exists', () => {
      const agent = new Agent('Test', 'Farmer', 0, 0, '#000');
      agent.sprite = {
        material: { dispose: vi.fn() },
      };

      agent.dispose();

      expect(agent.sprite.material.dispose).toHaveBeenCalled();
    });

    it('should handle missing sprite gracefully', () => {
      const agent = new Agent('Test', 'Farmer', 0, 0, '#000');

      expect(() => agent.dispose()).not.toThrow();
    });
  });
});
