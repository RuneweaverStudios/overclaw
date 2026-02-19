#!/usr/bin/env python3
"""
Blog Generator - Analyzes journal entries and chat history to identify high-value topics
and generate blog posts.

Scans journal entries, chat history, and recent activity to find interesting topics,
researches search volume/keywords, and generates blog posts.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
import subprocess


class BlogGenerator:
    def __init__(self, openclaw_home: Path):
        self.openclaw_home = openclaw_home
        self.journal_dir = openclaw_home / "journal"
        self.blogs_dir = openclaw_home / "blogs"
        self.blogs_dir.mkdir(parents=True, exist_ok=True)
        
        # High-value keywords related to OpenClaw
        self.high_value_keywords = [
            "openclaw gateway",
            "openclaw setup",
            "openclaw configuration",
            "openclaw skills",
            "openclaw troubleshooting",
            "gateway auth",
            "gateway restart",
            "gateway disconnected",
            "agent swarm",
            "subagent",
            "cursor chat history",
            "openclaw cron",
            "openclaw automation",
        ]
        
    def scan_journal_entries(self, days_back: int = 7) -> List[Dict[str, Any]]:
        """Scan journal entries from the last N days for interesting topics."""
        topics = []
        cutoff_date = datetime.now() - timedelta(days=days_back)
        
        if not self.journal_dir.exists():
            return topics
        
        # Scan chat analysis files
        for journal_file in self.journal_dir.glob("chat_analysis_*.md"):
            try:
                file_time = self._extract_timestamp_from_filename(journal_file.name)
                if file_time and file_time >= cutoff_date:
                    content = journal_file.read_text()
                    extracted = self._extract_topics_from_content(content, journal_file.name)
                    topics.extend(extracted)
            except Exception as e:
                print(f"Error reading {journal_file}: {e}", file=sys.stderr)
        
        # Scan other markdown files in journal
        for journal_file in self.journal_dir.rglob("*.md"):
            if "chat_analysis" in journal_file.name:
                continue  # Already processed
            try:
                stat = journal_file.stat()
                file_time = datetime.fromtimestamp(stat.st_mtime)
                if file_time >= cutoff_date:
                    content = journal_file.read_text()
                    extracted = self._extract_topics_from_content(content, journal_file.name)
                    topics.extend(extracted)
            except Exception as e:
                continue
        
        return topics
    
    def _extract_timestamp_from_filename(self, filename: str) -> Optional[datetime]:
        """Extract timestamp from filename like chat_analysis_2026-02-17_123045.md"""
        try:
            match = re.search(r'(\d{4}-\d{2}-\d{2})_(\d{6})', filename)
            if match:
                date_str = match.group(1)
                time_str = match.group(2)
                dt_str = f"{date_str} {time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}"
                return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        except:
            pass
        return None
    
    def _extract_topics_from_content(self, content: str, source: str) -> List[Dict[str, Any]]:
        """Extract interesting topics from journal content."""
        topics = []
        
        # Look for discoveries, obstacles, and solutions
        discovery_pattern = r'##\s*🔍\s*Key\s*Discoveries\s*\n(.*?)(?=##|$)'
        obstacle_pattern = r'##\s*⚠️\s*Obstacles\s*Encountered\s*\n(.*?)(?=##|$)'
        solution_pattern = r'##\s*✅\s*Solutions\s*Found\s*\n(.*?)(?=##|$)'
        
        discoveries = re.findall(discovery_pattern, content, re.DOTALL | re.IGNORECASE)
        obstacles = re.findall(obstacle_pattern, content, re.DOTALL | re.IGNORECASE)
        solutions = re.findall(solution_pattern, content, re.DOTALL | re.IGNORECASE)
        
        # Extract topics from discoveries
        for disc_text in discoveries:
            lines = disc_text.split('\n')
            for line in lines:
                if line.strip() and not line.strip().startswith('#'):
                    topics.append({
                        'type': 'discovery',
                        'content': line.strip()[:500],
                        'source': source,
                        'value_score': self._score_topic_value(line)
                    })
        
        # Extract topics from obstacles (high value - problems people face)
        for obs_text in obstacles:
            lines = obs_text.split('\n')
            for line in lines:
                if line.strip() and not line.strip().startswith('#'):
                    topics.append({
                        'type': 'obstacle',
                        'content': line.strip()[:500],
                        'source': source,
                        'value_score': self._score_topic_value(line) + 2  # Obstacles are higher value
                    })
        
        # Extract topics from solutions (very high value - how to solve problems)
        for sol_text in solutions:
            lines = sol_text.split('\n')
            for line in lines:
                if line.strip() and not line.strip().startswith('#'):
                    topics.append({
                        'type': 'solution',
                        'content': line.strip()[:500],
                        'source': source,
                        'value_score': self._score_topic_value(line) + 3  # Solutions are highest value
                    })
        
        return topics
    
    def _score_topic_value(self, content: str) -> int:
        """Score a topic based on keywords and content quality."""
        score = 0
        content_lower = content.lower()
        
        # Check for high-value keywords
        for keyword in self.high_value_keywords:
            if keyword.lower() in content_lower:
                score += 2
        
        # Check for problem-solving language
        problem_words = ['error', 'failed', 'issue', 'problem', 'fix', 'solution', 'how to', 'troubleshoot']
        for word in problem_words:
            if word in content_lower:
                score += 1
        
        # Check for technical depth
        if len(content) > 100:
            score += 1
        
        return score
    
    def identify_high_value_topics(self, topics: List[Dict[str, Any]], max_topics: int = 5) -> List[Dict[str, Any]]:
        """Identify the highest value topics for blog posts."""
        # Sort by value score
        sorted_topics = sorted(topics, key=lambda x: x.get('value_score', 0), reverse=True)
        
        # Group similar topics
        unique_topics = []
        seen_content = set()
        
        for topic in sorted_topics:
            # Create a signature from the content (first 100 chars)
            signature = topic['content'][:100].lower().strip()
            if signature not in seen_content:
                seen_content.add(signature)
                unique_topics.append(topic)
                if len(unique_topics) >= max_topics:
                    break
        
        return unique_topics
    
    def research_keyword(self, keyword: str) -> Dict[str, Any]:
        """Research a keyword for search volume and competition (placeholder - can be enhanced)."""
        # This is a placeholder - in a real implementation, you'd use an API like:
        # - Google Keyword Planner API
        # - Ahrefs API
        # - SEMrush API
        # For now, we'll use heuristics
        
        keyword_lower = keyword.lower()
        search_volume_score = 0
        
        # High-volume indicators
        if any(kw in keyword_lower for kw in ['how to', 'tutorial', 'guide', 'setup', 'install']):
            search_volume_score += 3
        
        if 'error' in keyword_lower or 'fix' in keyword_lower or 'troubleshoot' in keyword_lower:
            search_volume_score += 2
        
        if 'openclaw' in keyword_lower:
            search_volume_score += 1  # Niche but valuable
        
        return {
            'keyword': keyword,
            'estimated_volume': 'medium' if search_volume_score >= 3 else 'low',
            'competition': 'low',  # OpenClaw is niche
            'value_score': search_volume_score
        }
    
    def generate_blog_post(self, topic: Dict[str, Any]) -> str:
        """Generate a blog post from a topic."""
        content_type = topic.get('type', 'general')
        content = topic.get('content', '')
        
        # Extract a title from the content
        title = self._extract_title_from_content(content, content_type)
        
        # Generate blog post structure
        blog_post = f"""# {title}

*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*

## Overview

{self._generate_overview(content, content_type)}

## The Problem

{self._generate_problem_section(content, content_type)}

## The Solution

{self._generate_solution_section(content, content_type)}

## Key Takeaways

{self._generate_takeaways(content, content_type)}

## Related Topics

{self._generate_related_topics(content)}

---

*This post was automatically generated from journal analysis. Source: {topic.get('source', 'unknown')}*
"""
        return blog_post
    
    def _extract_title_from_content(self, content: str, content_type: str) -> str:
        """Extract or generate a title from content."""
        # Try to find a question or statement
        lines = content.split('\n')
        for line in lines[:3]:
            line = line.strip()
            if not line:
                continue
            
            # Remove markdown formatting
            line = re.sub(r'^\d+\.\s*', '', line)
            line = re.sub(r'\*\*', '', line)
            line = re.sub(r'`', '', line)
            
            if len(line) > 20 and len(line) < 100:
                # Capitalize first letter
                title = line[0].upper() + line[1:] if line else "OpenClaw Topic"
                # Ensure it ends properly
                if not title.endswith(('.', '!', '?')):
                    title += ": A Practical Guide"
                return title
        
        # Fallback title based on type
        if content_type == 'obstacle':
            return "Common OpenClaw Issues and How to Resolve Them"
        elif content_type == 'solution':
            return "Solving OpenClaw Configuration Challenges"
        elif content_type == 'discovery':
            return "OpenClaw Tips and Best Practices"
        else:
            return "OpenClaw Insights and Solutions"
    
    def _generate_overview(self, content: str, content_type: str) -> str:
        """Generate overview section."""
        if content_type == 'obstacle':
            return f"In this post, we'll explore a common issue encountered with OpenClaw and provide practical solutions. Based on recent analysis, this problem appears frequently and has a clear resolution path."
        elif content_type == 'solution':
            return f"This guide walks through a proven solution for an OpenClaw configuration or usage challenge. The approach has been tested and documented from real-world usage."
        else:
            return f"This post covers an interesting discovery or insight about using OpenClaw effectively. The information comes from analyzing recent usage patterns and journal entries."
    
    def _generate_problem_section(self, content: str, content_type: str) -> str:
        """Generate problem section."""
        if content_type == 'obstacle':
            # Extract the problem from content
            problem_lines = [line.strip() for line in content.split('\n')[:3] if line.strip()]
            problem_text = ' '.join(problem_lines[:2])
            return f"{problem_text}\n\nThis issue can be frustrating and may prevent you from using OpenClaw effectively. Understanding the root cause is the first step toward resolution."
        else:
            return "While working with OpenClaw, users may encounter various challenges related to configuration, gateway connectivity, or skill management. This post addresses one such challenge."
    
    def _generate_solution_section(self, content: str, content_type: str) -> str:
        """Generate solution section."""
        if content_type == 'solution':
            # Extract solution from content
            solution_lines = [line.strip() for line in content.split('\n')[:5] if line.strip()]
            solution_text = '\n\n'.join(solution_lines[:3])
            return f"{solution_text}\n\n### Step-by-Step Guide\n\n1. Identify the specific issue you're experiencing\n2. Follow the solution approach outlined above\n3. Verify the fix works as expected\n4. Document any additional steps needed for your setup"
        else:
            return "To resolve this issue, follow these steps:\n\n1. Check your OpenClaw configuration\n2. Review recent logs for error messages\n3. Consult the relevant skill documentation\n4. If needed, restart the gateway or relevant services\n\nFor specific guidance, refer to the OpenClaw documentation or community resources."
    
    def _generate_takeaways(self, content: str, content_type: str) -> str:
        """Generate key takeaways."""
        takeaways = [
            "Always check logs when encountering issues",
            "Keep your OpenClaw installation updated",
            "Review skill documentation for best practices",
            "Consider using gateway-guard for automatic recovery"
        ]
        
        return '\n'.join([f"- {takeaway}" for takeaway in takeaways[:4]])
    
    def _generate_related_topics(self, content: str) -> str:
        """Generate related topics section."""
        related = []
        content_lower = content.lower()
        
        if 'gateway' in content_lower:
            related.append("- [Gateway Configuration Guide](#)")
            related.append("- [Troubleshooting Gateway Issues](#)")
        
        if 'skill' in content_lower or 'agent' in content_lower:
            related.append("- [OpenClaw Skills Overview](#)")
            related.append("- [Creating Custom Skills](#)")
        
        if 'cron' in content_lower or 'schedule' in content_lower:
            related.append("- [Setting Up Cron Jobs](#)")
            related.append("- [Automation Best Practices](#)")
        
        if not related:
            related = [
                "- [OpenClaw Documentation](#)",
                "- [Community Resources](#)"
            ]
        
        return '\n'.join(related[:4])
    
    def save_blog_post(self, blog_post: str, topic: Dict[str, Any]) -> Path:
        """Save blog post to blogs directory."""
        # Generate filename from title
        title = blog_post.split('\n')[0].replace('# ', '').strip()
        filename = self._slugify(title)
        timestamp = datetime.now().strftime("%Y%m%d")
        blog_file = self.blogs_dir / f"{timestamp}_{filename}.md"
        
        # Avoid overwriting
        counter = 1
        while blog_file.exists():
            blog_file = self.blogs_dir / f"{timestamp}_{filename}_{counter}.md"
            counter += 1
        
        blog_file.write_text(blog_post)
        return blog_file
    
    def _slugify(self, text: str) -> str:
        """Convert text to URL-friendly slug."""
        text = text.lower()
        text = re.sub(r'[^\w\s-]', '', text)
        text = re.sub(r'[-\s]+', '-', text)
        return text[:50]  # Limit length


def main():
    parser = argparse.ArgumentParser(description="Generate blog posts from journal entries and chat history.")
    parser.add_argument("--days", type=int, default=7, help="Days of journal history to analyze (default: 7)")
    parser.add_argument("--max-topics", type=int, default=3, help="Maximum topics to generate (default: 3)")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    parser.add_argument("--openclaw-home", type=str, help="OpenClaw home directory (default: ~/.openclaw)")
    args = parser.parse_args()
    
    openclaw_home = Path(args.openclaw_home) if args.openclaw_home else Path.home() / ".openclaw"
    
    generator = BlogGenerator(openclaw_home)
    
    try:
        # Scan journal entries
        topics = generator.scan_journal_entries(days_back=args.days)
        
        if not topics:
            if args.json:
                print(json.dumps({"status": "no_topics", "message": "No topics found in journal entries"}))
            else:
                print("No topics found in journal entries from the specified time period.")
            return
        
        # Identify high-value topics
        high_value_topics = generator.identify_high_value_topics(topics, max_topics=args.max_topics)
        
        if not high_value_topics:
            if args.json:
                print(json.dumps({"status": "no_high_value_topics", "message": "No high-value topics identified"}))
            else:
                print("No high-value topics identified.")
            return
        
        # Generate blog posts
        generated_posts = []
        for topic in high_value_topics:
            blog_post = generator.generate_blog_post(topic)
            blog_file = generator.save_blog_post(blog_post, topic)
            generated_posts.append({
                'topic': topic,
                'blog_file': str(blog_file),
                'title': blog_post.split('\n')[0].replace('# ', '')
            })
        
        if args.json:
            output = {
                'status': 'success',
                'topics_found': len(topics),
                'high_value_topics': len(high_value_topics),
                'blog_posts_generated': len(generated_posts),
                'posts': generated_posts
            }
            print(json.dumps(output, indent=2))
        else:
            print("=" * 70)
            print("BLOG POST GENERATION REPORT")
            print("=" * 70)
            print(f"\nTopics analyzed: {len(topics)}")
            print(f"High-value topics identified: {len(high_value_topics)}")
            print(f"Blog posts generated: {len(generated_posts)}\n")
            
            for i, post in enumerate(generated_posts, 1):
                print(f"{i}. {post['title']}")
                print(f"   Saved to: {post['blog_file']}\n")
            
            print("=" * 70)
    
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
