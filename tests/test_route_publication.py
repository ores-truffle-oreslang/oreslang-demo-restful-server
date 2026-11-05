"""Host generator publication contracts; no guest compilation is implied."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('route_generator', Path(__file__).resolve().parents[1]/'scripts/generate-routes.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class RoutePublication(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        routes = self.root/'src/routes/rest/health'
        routes.mkdir(parents=True)
        (routes/'get.ores').write_text('route fixture')
        self.patch = patch.object(generator, 'ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_leaf_symlink_is_replaced_without_modifying_target(self):
        generated = self.root/'src/generated'
        generated.mkdir()
        target = self.root/'important.txt'
        target.write_text('preserve me')
        for name in ('routes.ores','config.ores'):
            (generated/name).symlink_to(target)
        generator.generate(3000, self.root/'data')
        self.assertEqual(target.read_text(),'preserve me')
        self.assertFalse((generated/'routes.ores').is_symlink())
        self.assertIn('GET /health',(generated/'routes.ores').read_text())
        self.assertIn('3000',(generated/'config.ores').read_text())

    def test_failed_replace_preserves_previous_output_and_cleans_temporary(self):
        destination = self.root/'routes.ores'
        destination.write_text('previous valid source')
        with patch.object(generator.os,'replace',side_effect=OSError('fixture disk failure')):
            with self.assertRaises(OSError):
                generator.publish(destination,'new source')
        self.assertEqual(destination.read_text(),'previous valid source')
        self.assertEqual(list(self.root.glob('.routes-*')),[])

    def test_symlinked_route_root_rejected(self):
        route_root = self.root/'src/routes/rest'
        external = self.root/'external-routes'
        route_root.rename(external)
        route_root.symlink_to(external, target_is_directory=True)
        with self.assertRaises(ValueError):
            generator.generate(3000, self.root/'data')
        self.assertFalse((self.root/'src/generated/routes.ores').exists())

    def test_symlinked_output_directory_rejected(self):
        target = self.root/'elsewhere'
        target.mkdir()
        (self.root/'src/generated').symlink_to(target,target_is_directory=True)
        with self.assertRaises(ValueError):
            generator.generate(3000,self.root/'data')
        self.assertEqual(list(target.iterdir()),[])

    def test_invalid_ports_do_not_publish_any_source(self):
        for bad in (0, -1, 65536, 2**63):
            with self.subTest(port=bad), self.assertRaises(ValueError):
                generator.generate(bad, self.root/'data')
            self.assertFalse((self.root/'src/generated/routes.ores').exists())
            self.assertFalse((self.root/'src/generated/config.ores').exists())

    def test_unsupported_method_is_not_registered_or_published(self):
        trace = self.root/'src/routes/rest/health/trace.ores'
        trace.write_text('route fixture')
        with self.assertRaisesRegex(ValueError, 'unsupported method filename'):
            generator.generate(3000, self.root/'data')
        self.assertFalse((self.root/'src/generated/routes.ores').exists())

    def test_nonliteral_route_segment_is_not_published(self):
        path = self.root/'src/routes/rest/bad segment'
        path.mkdir()
        (path/'get.ores').write_text('route fixture')
        with self.assertRaisesRegex(ValueError, 'literal URL segments'):
            generator.generate(3000, self.root/'data')
        self.assertFalse((self.root/'src/generated/routes.ores').exists())

    def test_symlinked_route_leaf_is_not_traversed(self):
        route = self.root/'src/routes/rest/health/post.ores'
        route.symlink_to(self.root/'src/routes/rest/health/get.ores')
        with self.assertRaises(ValueError):
            generator.generate(3000, self.root/'data')
        self.assertFalse((self.root/'src/generated/routes.ores').exists())



if __name__=='__main__':
    unittest.main()
