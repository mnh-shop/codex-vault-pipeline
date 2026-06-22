# Codex Vault Pipeline — Repomix adapter
"""Repomix adapter for GitHub/local repo packing."""
from __future__ import annotations

import json
import subprocess
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .manifest import RepoPackManifest


@dataclass
class RepomixResult:
    """Result from a Repomix run."""
    
    source_id: str
    success: bool
    command: str
    exit_code: int
    stdout: str
    stderr: str
    output_file: Optional[str] = None
    file_size: Optional[int] = None
    token_count: Optional[int] = None
    security_findings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "source_id": self.source_id,
            "success": self.success,
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout[:1000] if self.stdout else "",
            "stderr": self.stderr[:1000] if self.stderr else "",
            "output_file": self.output_file,
            "file_size": self.file_size,
            "token_count": self.token_count,
            "security_findings": self.security_findings,
            "error": self.error,
        }


class RepomixAdapter:
    """Adapter for running Repomix via npx."""
    
    def __init__(self):
        """Initialize Repomix adapter."""
        self.npx_path = shutil.which("npx")
        self.node_available = shutil.which("node") is not None
        self.npx_available = self.npx_path is not None
    
    def check_availability(self) -> Dict[str, Any]:
        """Check if Repomix is available."""
        result = {
            "node_available": self.node_available,
            "npx_available": self.npx_available,
            "repomix_available": False,
            "repomix_version": None,
        }
        
        if not self.npx_available:
            return result
        
        try:
            cmd = [self.npx_path, "repomix", "--version"]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode == 0:
                result["repomix_available"] = True
                result["repomix_version"] = proc.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        
        return result
    
    def run_repomix(
        self,
        manifest: RepoPackManifest,
        output_dir: Path,
    ) -> RepomixResult:
        """Run Repomix for a single source."""
        if not self.npx_available:
            return RepomixResult(
                source_id=manifest.source_id,
                success=False,
                command="",
                exit_code=1,
                stdout="",
                stderr="npx not available",
                error="npx not available",
            )
        
        # Build command
        cmd = [self.npx_path, "repomix"]
        
        # Add target
        if manifest.source_type == "github" and manifest.repo_url:
            cmd.extend(["--remote", manifest.repo_url])
        elif manifest.source_type == "local" and manifest.local_path:
            # For local repos, pass directory as argument
            cmd.append(manifest.local_path)
        
        # Add output format
        if manifest.output_format == "xml":
            cmd.extend(["-o", str(output_dir / "output.xml"), "--style", "xml"])
        else:
            cmd.extend(["-o", str(output_dir / "output.md"), "--style", "markdown"])
        
        # Add security check (enabled by default, use --no-security-check to disable)
        # Security check is enabled by default in Repomix, no flag needed
        
        # Add compression
        if manifest.compression:
            cmd.append("--compress")
        
        # Run command
        command_str = " ".join(cmd)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(output_dir),
            )
            
            # Parse output
            output_file = None
            file_size = None
            token_count = None
            security_findings = []
            
            # Check for output file
            if manifest.output_format == "xml":
                candidate = output_dir / "output.xml"
            else:
                candidate = output_dir / "output.md"
            
            if candidate.exists():
                output_file = str(candidate)
                file_size = candidate.stat().st_size
            
            # Parse Repomix output for token count
            if proc.stdout:
                for line in proc.stdout.split("\n"):
                    if "token" in line.lower() and "count" in line.lower():
                        # Try to extract token count
                        parts = line.split(":")
                        if len(parts) > 1:
                            try:
                                token_count = int(parts[1].strip())
                            except ValueError:
                                pass
            
            # Check for security findings
            if proc.stderr and "security" in proc.stderr.lower():
                security_findings.append(proc.stderr[:500])
            
            return RepomixResult(
                source_id=manifest.source_id,
                success=proc.returncode == 0,
                command=command_str,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                output_file=output_file,
                file_size=file_size,
                token_count=token_count,
                security_findings=security_findings,
                error=None if proc.returncode == 0 else f"Exit code {proc.returncode}",
            )
            
        except subprocess.TimeoutExpired:
            return RepomixResult(
                source_id=manifest.source_id,
                success=False,
                command=command_str,
                exit_code=-1,
                stdout="",
                stderr="Command timed out after 300s",
                error="Timeout",
            )
        except Exception as e:
            return RepomixResult(
                source_id=manifest.source_id,
                success=False,
                command=command_str,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                error=str(e),
            )
