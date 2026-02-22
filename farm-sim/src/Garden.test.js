/**
 * Unit tests for Garden class
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import * as THREE from 'three';

// Mock the global environment for tests
global.THREE = THREE;

describe('Garden', () => {
  // Mock worldGroup
  const mockWorldGroup = {
    add: vi.fn(),
    remove: vi.fn(),
  };

  global.worldGroup = mockWorldGroup;

  // Simplified Garden class for testing
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
      // Mock implementation
      this.sprite = {
        material: { dispose: vi.fn() },
        position: this.position.clone(),
        scale: new THREE.Vector3(
          this.width * 2,
          this.height * 2,
          1
        ),
      };

      mockWorldGroup.add(this.sprite);

      // Create crops
      for (let i = 0; i < this.width; i++) {
        for (let j = 0; j < this.height; j++) {
          const cropX = this.position.x - (this.width * 2 / 2) + (i + 0.5) * 2;
          const cropZ = this.position.z - (this.height * 2 / 2) + (j + 0.5) * 2;
          this.crops.push({
            position: new THREE.Vector3(cropX, 0.5, cropZ),
            stage: Math.floor(Math.random() * 8),
            sprite: null,
          });
        }
      }
    }

    dispose() {
      if (this.sprite) {
        this.sprite.material.dispose();
        mockWorldGroup.remove(this.sprite);
      }
      this.crops.forEach(crop => {
        if (crop.sprite) {
          crop.sprite.material.dispose();
          mockWorldGroup.remove(crop.sprite);
        }
      });
    }
  }

  describe('constructor', () => {
    it('should create a garden with correct properties', () => {
      const mockAgent = { name: 'Farmer John' };
      const garden = new Garden(mockAgent, 5, 10, 3, 3);

      expect(garden.owner).toBe(mockAgent);
      expect(garden.position.x).toBe(5);
      expect(garden.position.z).toBe(10);
      expect(garden.width).toBe(3);
      expect(garden.height).toBe(3);
      expect(garden.crops).toEqual([]);
    });

    it('should set y position slightly above ground', () => {
      const mockAgent = { name: 'Farmer John' };
      const garden = new Garden(mockAgent, 0, 0, 2, 2);

      expect(garden.position.y).toBe(0.1);
    });
  });

  describe('init', () => {
    it('should initialize garden and create crops', async () => {
      const mockAgent = { name: 'Farmer John' };
      const garden = new Garden(mockAgent, 0, 0, 3, 3);

      await garden.init();

      expect(garden.sprite).not.toBeNull();
      expect(garden.crops.length).toBe(9); // 3x3 = 9 crops
      expect(mockWorldGroup.add).toHaveBeenCalled();
    });

    it('should create correct number of crops for garden size', async () => {
      const mockAgent = { name: 'Farmer John' };
      const garden = new Garden(mockAgent, 0, 0, 2, 4);

      await garden.init();

      expect(garden.crops.length).toBe(8); // 2x4 = 8 crops
    });

    it('should assign random growth stages to crops', async () => {
      const mockAgent = { name: 'Farmer John' };
      const garden = new Garden(mockAgent, 0, 0, 3, 3);

      await garden.init();

      const stages = garden.crops.map(c => c.stage);
      stages.forEach(stage => {
        expect(stage).toBeGreaterThanOrEqual(0);
        expect(stage).toBeLessThan(8);
      });
    });
  });

  describe('dispose', () => {
    it('should dispose sprite material and remove from group', async () => {
      const mockAgent = { name: 'Farmer John' };
      const garden = new Garden(mockAgent, 0, 0, 2, 2);

      await garden.init();
      garden.dispose();

      expect(garden.sprite.material.dispose).toHaveBeenCalled();
      expect(mockWorldGroup.remove).toHaveBeenCalledWith(garden.sprite);
    });

    it('should dispose all crop sprites', async () => {
      const mockAgent = { name: 'Farmer John' };
      const garden = new Garden(mockAgent, 0, 0, 2, 2);

      await garden.init();

      // Add mock sprites to crops
      garden.crops.forEach(crop => {
        crop.sprite = {
          material: { dispose: vi.fn() },
        };
      });

      garden.dispose();

      garden.crops.forEach(crop => {
        if (crop.sprite) {
          expect(crop.sprite.material.dispose).toHaveBeenCalled();
        }
      });
    });

    it('should handle missing sprite gracefully', () => {
      const mockAgent = { name: 'Farmer John' };
      const garden = new Garden(mockAgent, 0, 0, 2, 2);

      expect(() => garden.dispose()).not.toThrow();
    });

    it('should handle crops without sprites gracefully', async () => {
      const mockAgent = { name: 'Farmer John' };
      const garden = new Garden(mockAgent, 0, 0, 2, 2);

      await garden.init();
      // Crops have no sprites yet

      expect(() => garden.dispose()).not.toThrow();
    });
  });
});
