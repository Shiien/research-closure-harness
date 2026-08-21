"""Repository-level auto-loading contract for Codex, Claude Code, and dsh."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestHarnessAutoLoad(unittest.TestCase):
    def test_codex_and_claude_autoload_ab_commands(self):
        for name in ("AGENTS.md", "CLAUDE.md"):
            text = (ROOT / name).read_text()
            self.assertIn("ab-status", text)
            self.assertIn("ab-next", text)
            self.assertIn("retro next", text)
            self.assertIn("auto-research-fast", text)
            self.assertIn("auto-research-slow", text)

    def test_fast_skill_is_retrospective_first_and_can_patch_files(self):
        for root in (".dsh", ".agents", ".claude"):
            text = (ROOT / root / "skills" / "auto-research-fast" / "SKILL.md").read_text()
            self.assertIn("retro next", text)
            self.assertIn("patch-make", text)
            self.assertIn("patch_file", text)

    def test_slow_skill_documents_sandbox_verify_and_file_rollback(self):
        for root in (".dsh", ".agents", ".claude"):
            text = (ROOT / root / "skills" / "auto-research-slow" / "SKILL.md").read_text()
            self.assertIn("isolated", text)
            self.assertIn("file backups", text)

    def test_dsh_uses_agents_md_not_deepseek_md(self):
        text = (ROOT / "AGENTS.md").read_text()
        self.assertIn("ab-status", text)
        self.assertFalse((ROOT / "DEEPSEEK.md").exists())
        self.assertFalse((ROOT / ".deepseek").exists())

    def test_dsh_project_skill_root_exists(self):
        for role in ("fast", "slow"):
            path = ROOT / ".dsh" / "skills" / f"auto-research-{role}" / "SKILL.md"
            self.assertTrue(path.exists(), path)
            text = path.read_text()
            self.assertIn(f"name: auto-research-{role}", text)
            self.assertIn("disable-model-invocation: false", text)
            self.assertIn("user-invocable: true", text)

    def test_shared_agents_skill_root_remains_dsh_compatible(self):
        for role in ("fast", "slow"):
            path = ROOT / ".agents" / "skills" / f"auto-research-{role}" / "SKILL.md"
            self.assertTrue(path.exists(), path)

    def test_installer_targets_dsh_home(self):
        text = (ROOT / "install_research_closure_global.sh").read_text()
        self.assertIn("${DSH_HOME:-$HOME/.dsh}", text)
        self.assertIn("$DSH_AGENTS", text)

    def test_auto_research_minimal_preset_template(self):
        path = ROOT / "dsh" / "agent-presets" / "auto-research-minimal" / "agent.cordis.yml"
        self.assertTrue(path.exists(), path)
        text = path.read_text()
        self.assertIn("@deepseek-ai/dsh-agent-instructions", text)
        self.assertIn("@deepseek-ai/dsh-skill-filesystem", text)
        self.assertIn("@deepseek-ai/dsh-tool-skill", text)
        self.assertIn("str_replace_editor", text)
        self.assertIn("complete: false", text)

    def test_installer_installs_auto_research_preset(self):
        text = (ROOT / "install_research_closure_global.sh").read_text()
        self.assertIn("$DSH_AR_PRESET_DIR", text)
        self.assertIn("auto-research-minimal", text)


if __name__ == "__main__":
    unittest.main()
