# Farm Sim Visualizer

A cozy farm sim visualizer built with Three.js featuring sprite sheets, mailboxes, individual gardens for each agent, and time-based realistic thought bubbles with italic text.

## Features

- **Three.js Rendering**: Beautiful 2D sprite-based farm world
- **Multiple Agents**: Farmer, Gardener, and Rancher with unique personalities
- **Individual Gardens**: Each agent has their own 3x3 garden plot
- **Mailbox System**: Interactive mailbox with mail notifications
- **Thought Bubbles**: Time-based realistic thought bubbles with italic text
- **Traditional UI Dialogue Boxes**: Classic RPG-style dialogue with typewriter effect
- **Day/Night Cycle**: Time system with day counter
- **Sprite Sheets**: Uses PIPOYA and Tiny Wonder Farm assets

## Installation

```bash
cd farm-sim
npm install
```

## Running

```bash
npm run dev
```

Open http://localhost:3000 in your browser.

## Controls

- **Click on agents**: Interact and see dialogue
- **Click on mailbox**: Read mail
- **Space/Enter**: Advance dialogue
- **Escape**: Close dialogue

## Game Loop

- Agents wander randomly around their gardens
- Every 8 seconds, agents show thought bubbles with realistic thoughts
- Time progresses (1 real second = 1 game minute)
- Mail arrives after 5 seconds with notification

## Assets

Uses farm assets from:
- PIPOYA FREE RPG Character Sprites 32x32
- Tiny Wonder Farm Free 3
- Sunnyside World Asset Pack

## Architecture

- `main.js`: Core game logic with Agent, Garden, Mailbox classes
- Three.js for rendering
- Vite for bundling
- Nearest-filtered sprites for crisp pixel art
