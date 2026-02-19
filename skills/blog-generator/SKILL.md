---
name: blog-generator
displayName: Blog Generator | OpenClaw Skill
description: Analyzes journal entries and chat history to identify high-value topics and automatically generate blog posts.
version: 1.0.0
---

# Blog Generator | OpenClaw Skill

## Description

Analyzes journal entries and chat history to identify high-value topics and automatically generate blog posts.

# Blog Generator | OpenClaw Skill

Automatically generates blog posts by analyzing journal entries, chat history, and recent activity to identify high-value, high-search-volume topics related to OpenClaw.


## Usage

- As a scheduled cron job to automatically generate blog content weekly or daily
- Manually to create blog posts from recent journal analysis
- To identify and document high-value solutions and discoveries

```bash
# Generate blog posts from last 7 days of journal entries
python3 /Users/ghost/.openclaw/workspace/skills/blog-generator/scripts/blog_generator.py

# Analyze last 14 days and generate up to 5 posts
python3 /Users/ghost/.openclaw/workspace/skills/blog-generator/scripts/blog_generator.py --days 14 --max-topics 5

# Output JSON format
python3 /Users/ghost/.openclaw/workspace/skills/blog-generator/scripts/blog_generator.py --json
```


## What this skill does

- **Scans** journal entries from the last N days for interesting topics (discoveries, obstacles, solutions)
- **Identifies** high-value topics based on keyword relevance and problem-solving value
- **Researches** search volume and keyword opportunities (heuristic-based, can be enhanced with APIs)
- **Generates** structured blog posts with overview, problem, solution, and takeaways sections
- **Saves** blog posts to `/Users/ghost/.openclaw/blogs/` as markdown files


## Integration as a Cron Job

This skill is designed to run periodically (daily or weekly) via OpenClaw cron to automatically generate blog content.

**Example Cron Job Configuration (Daily):**

```json
{
  "payload": {
    "kind": "agentTurn",
    "message": "Run blog-generator skill to analyze journal entries and generate high-value blog posts.",
    "model": "openrouter/google/gemini-2.5-flash",
    "thinking": "low",
    "timeoutSeconds": 300
  },
  "schedule": {
    "kind": "cron",
    "cron": "0 9 * * *"
  },
  "delivery": {
    "mode": "announce"
  },
  "sessionTarget": "isolated",
  "name": "Blog Post Generator"
}
```

**Example Cron Job Configuration (Weekly):**

```json
{
  "payload": {
    "kind": "agentTurn",
    "message": "Run blog-generator skill with --days 7 --max-topics 3 to generate weekly blog posts from journal analysis.",
    "model": "openrouter/google/gemini-2.5-flash",
    "thinking": "low",
    "timeoutSeconds": 300
  },
  "schedule": {
    "kind": "cron",
    "cron": "0 10 * * 1"
  },
  "delivery": {
    "mode": "announce"
  },
  "sessionTarget": "isolated",
  "name": "Weekly Blog Generator"
}
```


## Output Format

Blog posts are saved to `/Users/ghost/.openclaw/blogs/YYYYMMDD_slugified-title.md` with:

- **Title**: Extracted or generated from topic content
- **Overview**: Context about the topic
- **The Problem**: Description of the issue or challenge
- **The Solution**: Step-by-step solution guide
- **Key Takeaways**: Summary points
- **Related Topics**: Links to related content


## Topic Scoring

Topics are scored based on:

- **High-value keywords**: OpenClaw-specific terms, problem-solving language
- **Content type**: Solutions score highest, then obstacles, then discoveries
- **Content depth**: Longer, more detailed content scores higher
- **Search volume indicators**: Keywords like "how to", "tutorial", "fix" increase value


## Requirements

- Journal entries in `/Users/ghost/.openclaw/journal/`
- Blogs directory writable at `/Users/ghost/.openclaw/blogs/`
- Chat history analyzer skill (for journal entries)


## How it works

1. Scans journal directory for markdown files from the last N days
2. Extracts topics from discoveries, obstacles, and solutions sections
3. Scores topics based on keyword relevance and value
4. Selects top N high-value topics
5. Generates structured blog posts with problem/solution format
6. Saves posts to blogs directory with timestamped filenames


## Enhancement Opportunities

- Integrate with Google Keyword Planner API for real search volume data
- Use AI model to enhance blog post quality and SEO optimization
- Cross-reference with existing blog posts to avoid duplicates
- Generate multiple variations of posts for A/B testing
