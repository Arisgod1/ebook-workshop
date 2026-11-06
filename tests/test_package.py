"""Skill 包完整性：说明文档里提到的文件和资源都要真的在包里。"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PackageTests(unittest.TestCase):
    def test_skill_and_reference_files_exist(self):
        """SKILL.md 和它引用的每份说明都在包里。"""
        skill = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
        self.assertTrue((ROOT / 'README.md').is_file())
        for name in ('references/commands.md', 'references/proofreading.md',
                     'references/reading-workflow.md', 'references/kindle-pages.md',
                     'references/agent-integration.md'):
            self.assertTrue((ROOT / name).is_file(), name)
            self.assertIn(name, skill, f'{name} 未在 SKILL.md 中提到')

    def test_assets_referenced_by_the_code_are_present(self):
        """代码里引用的词典和样式表都要在包里，否则运行时会直接报错。"""
        for name in ('assets/proofread-dictionary.json', 'assets/proofread-variants.json',
                     'assets/proofread-resources.json', 'assets/cn-novel.css'):
            self.assertTrue((ROOT / name).is_file(), name)

    def test_variants_table_maps_single_characters(self):
        """异体字表只能把单个字符对应到单个字符。"""
        data = json.loads((ROOT / 'assets/proofread-variants.json').read_text(encoding='utf-8'))
        variants = data['variants']
        self.assertGreater(len(variants), 1000)
        for wrong, right in variants.items():
            self.assertEqual(len(wrong), 1, wrong)
            self.assertEqual(len(right), 1, right)

    def test_base_dictionary_entries_are_well_formed(self):
        """通用词表的每一项都是非空字符串，豁免项没有前后空格。"""
        data = json.loads((ROOT / 'assets/proofread-dictionary.json').read_text(encoding='utf-8'))
        for key in ('words', 'protected'):
            for entry in data[key]:
                self.assertIsInstance(entry, str)
                self.assertTrue(entry)
                self.assertEqual(entry, entry.strip())
        for old, new in data['replacements'].items():
            self.assertTrue(old and new)
            self.assertNotEqual(old, new)

    def test_no_book_or_private_data_is_committed(self):
        """仓库里不夹带书籍、封面或私人校对数据。"""
        for path in ROOT.rglob('*'):
            if '.git' in path.parts or '.venv' in path.parts or 'work' in path.parts:
                continue
            self.assertNotIn(path.suffix, ('.epub', '.mobi', '.azw3', '.azw', '.kfx'),
                             f'{path} 不应入库')

