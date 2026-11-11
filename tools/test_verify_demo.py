import subprocess
import tempfile
import unittest
from pathlib import Path
from verify_demo import manifest, safe_new_work, verify


class DemoSafetyTest(unittest.TestCase):
    def test_rejects_existing_overlap_and_symlink_ancestor(self):
        # The production guard intentionally rejects work inside any checkout,
        # so this positive fixture must not inherit a TMPDIR inside one.
        with tempfile.TemporaryDirectory(prefix='scientific-mlops-demo-test-', dir='/var/tmp') as tmp:
            root = Path(tmp)
            checkout = root / 'checkout'
            checkout.mkdir()
            with self.assertRaises(ValueError):
                safe_new_work(checkout, [checkout])
            with self.assertRaises(ValueError):
                safe_new_work(checkout / 'work', [checkout])
            link = root / 'link'
            link.symlink_to(checkout, target_is_directory=True)
            with self.assertRaises(ValueError):
                safe_new_work(link / 'work', [])
            self.assertEqual(safe_new_work(root / 'new', [checkout]), root / 'new')

    def test_rejects_work_inside_another_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(['git', 'init', '-q', tmp], check=True)
            with self.assertRaises(ValueError):
                safe_new_work(Path(tmp) / 'work', [])

    def test_manifest_detects_changed_content_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'nested').mkdir()
            item = root / 'nested' / 'object'
            item.write_bytes(b'one')
            before = manifest(root)
            item.write_bytes(b'two')
            self.assertNotEqual(before, manifest(root))
            (root / 'link').symlink_to(item)
            with self.assertRaises(ValueError):
                manifest(root)

    def test_invalid_ref_fails_before_creating_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'
            with self.assertRaises(ValueError):
                verify(work, 'main')
            self.assertFalse(work.exists())


if __name__ == '__main__':
    unittest.main()
