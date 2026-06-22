# Codex Vault Pipeline — DeepWiki sanity checker
"""DeepWiki sanity checker for public GitHub repos."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pathlib import Path


@dataclass
class DeepWikiResult:
    """Result from DeepWiki sanity check."""
    
    repo: str
    deepwiki_url: str
    reachable: Optional[bool] = None
    status_code: Optional[int] = None
    recommended_use: str = "external sanity only"
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "repo": self.repo,
            "deepwiki_url": self.deepwiki_url,
            "reachable": self.reachable,
            "status_code": self.status_code,
            "recommended_use": self.recommended_use,
            "error": self.error,
        }


class DeepWikiSanityChecker:
    """Sanity checker for DeepWiki URLs."""
    
    @staticmethod
    def convert_to_deepwiki_url(github_url: str) -> str:
        """Convert GitHub URL to DeepWiki URL."""
        # Pattern: github.com/<owner>/<repo>
        # Convert to: deepwiki.com/<owner>/<repo>
        pattern = r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/.*)?$"
        match = re.match(pattern, github_url)
        if match:
            owner = match.group(1)
            repo = match.group(2)
            return f"https://deepwiki.com/{owner}/{repo}"
        
        # Try owner/repo format
        pattern2 = r"^([^/]+)/([^/]+?)(?:\.git)?$"
        match2 = re.match(pattern2, github_url)
        if match2:
            owner = match2.group(1)
            repo = match2.group(2)
            return f"https://deepwiki.com/{owner}/{repo}"
        
        return ""
    
    @staticmethod
    def check_url(url: str) -> DeepWikiResult:
        """Check if a DeepWiki URL is reachable."""
        # Extract repo name from URL
        parts = url.rstrip("/").split("/")
        if len(parts) >= 2:
            repo = f"{parts[-2]}/{parts[-1]}"
        else:
            repo = url
        
        result = DeepWikiResult(
            repo=repo,
            deepwiki_url=url,
            reachable=None,
            status_code=None,
            recommended_use="use manually in browser",
        )
        
        # Note: We don't actually make HTTP requests in this phase
        # Just document the URL and recommend manual check
        result.recommended_use = "external sanity check only - verify manually"
        
        return result
    
    @staticmethod
    def check_pilot_repos(pilot_sources: List[Dict[str, Any]]) -> List[DeepWikiResult]:
        """Check DeepWiki URLs for all pilot sources."""
        results = []
        
        for source in pilot_sources:
            if source.get("source_type") == "github" and source.get("repo_url"):
                url = DeepWikiSanityChecker.convert_to_deepwiki_url(source["repo_url"])
                if url:
                    result = DeepWikiSanityChecker.check_url(url)
                    results.append(result)
        
        return results
