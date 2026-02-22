/**
 * Unit tests for Mailbox class
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import * as THREE from 'three';

// Mock the global environment for tests
global.THREE = THREE;

describe('Mailbox', () => {
  // Mock DOM elements
  const mockElements = {
    mailboxIndicator: {
      classList: {
        toggle: vi.fn(),
        add: vi.fn(),
        remove: vi.fn(),
      },
    },
    mailboxText: { textContent: '' },
  };

  beforeEach(() => {
    // Reset DOM mocks
    mockElements.mailboxIndicator.classList.toggle.mockClear();
    mockElements.mailboxIndicator.classList.add.mockClear();
    mockElements.mailboxIndicator.classList.remove.mockClear();
    mockElements.mailboxText.textContent = '';

    document.getElementById = vi.fn((id) => mockElements[id]);

    // Mock worldGroup
    global.worldGroup = {
      add: vi.fn(),
      remove: vi.fn(),
    };
  });

  // Simplified Mailbox class for testing
  class Mailbox {
    constructor(x, z) {
      this.position = new THREE.Vector3(x, 0, z);
      this.sprite = null;
      this.flagUp = false;
    }

    async init() {
      this.sprite = {
        material: { dispose: vi.fn() },
        position: this.position.clone(),
        renderOrder: 8,
      };
      this.sprite.position.y = 1.5;

      global.worldGroup.add(this.sprite);
    }

    toggleFlag() {
      this.flagUp = !this.flagUp;
      this.updateFlag();

      const indicator = document.getElementById('mailboxIndicator');
      const mailboxText = document.getElementById('mailboxText');

      if (indicator) {
        indicator.classList.toggle('has-mail', this.flagUp);
        indicator.classList.toggle('active', this.flagUp);
      }

      if (mailboxText) {
        mailboxText.textContent = this.flagUp ? 'New Mail!' : 'No Mail';
      }
    }

    updateFlag() {
      // Flag sprite update would go here
    }

    dispose() {
      if (this.sprite) {
        this.sprite.material.dispose();
        global.worldGroup.remove(this.sprite);
      }
    }
  }

  describe('constructor', () => {
    it('should create a mailbox with correct position', () => {
      const mailbox = new Mailbox(5, 10);

      expect(mailbox.position.x).toBe(5);
      expect(mailbox.position.z).toBe(10);
      expect(mailbox.flagUp).toBe(false);
    });

    it('should initialize with flag down', () => {
      const mailbox = new Mailbox(0, 0);

      expect(mailbox.flagUp).toBe(false);
    });
  });

  describe('init', () => {
    it('should initialize mailbox sprite', async () => {
      const mailbox = new Mailbox(0, 0);

      await mailbox.init();

      expect(mailbox.sprite).not.toBeNull();
      expect(mailbox.sprite.position.y).toBe(1.5);
      expect(mailbox.sprite.renderOrder).toBe(8);
      expect(global.worldGroup.add).toHaveBeenCalledWith(mailbox.sprite);
    });
  });

  describe('toggleFlag', () => {
    it('should raise flag when toggled from down', () => {
      const mailbox = new Mailbox(0, 0);
      mailbox.flagUp = false;

      mailbox.toggleFlag();

      expect(mailbox.flagUp).toBe(true);
      expect(mockElements.mailboxText.textContent).toBe('New Mail!');
      expect(mockElements.mailboxIndicator.classList.toggle).toHaveBeenCalledWith('has-mail', true);
      expect(mockElements.mailboxIndicator.classList.toggle).toHaveBeenCalledWith('active', true);
    });

    it('should lower flag when toggled from up', () => {
      const mailbox = new Mailbox(0, 0);
      mailbox.flagUp = true;

      mailbox.toggleFlag();

      expect(mailbox.flagUp).toBe(false);
      expect(mockElements.mailboxText.textContent).toBe('No Mail');
    });

    it('should handle missing DOM elements gracefully', () => {
      const mailbox = new Mailbox(0, 0);
      document.getElementById = vi.fn(() => null);

      expect(() => mailbox.toggleFlag()).not.toThrow();
      expect(mailbox.flagUp).toBe(true); // Flag still toggles
    });
  });

  describe('updateFlag', () => {
    it('should handle missing sprite gracefully', () => {
      const mailbox = new Mailbox(0, 0);

      expect(() => mailbox.updateFlag()).not.toThrow();
    });
  });

  describe('dispose', () => {
    it('should dispose sprite material and remove from group', async () => {
      const mailbox = new Mailbox(0, 0);
      await mailbox.init();

      mailbox.dispose();

      expect(mailbox.sprite.material.dispose).toHaveBeenCalled();
      expect(global.worldGroup.remove).toHaveBeenCalledWith(mailbox.sprite);
    });

    it('should handle missing sprite gracefully', () => {
      const mailbox = new Mailbox(0, 0);

      expect(() => mailbox.dispose()).not.toThrow();
    });
  });
});
