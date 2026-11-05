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




class RouteDiscoveryDenials(unittest.TestCase):
    """Host-only route validation and no-publish-before-discovery contracts."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.routes = self.root / 'src/routes/rest'
        (self.routes / 'health').mkdir(parents=True)
        (self.routes / 'health/get.ores').write_text('fixture')
        self.patch = patch.object(generator, 'ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_route_discovery_is_deterministically_sorted(self):
        (self.routes / 'alpha').mkdir()
        (self.routes / 'alpha/post.ores').write_text('fixture')
        entries = [(method, parts) for method, parts, _ in generator.discover(self.routes)]
        self.assertEqual(entries, [('POST', ('alpha',)), ('GET', ('health',))])

    def test_unsupported_http_method_cannot_replace_existing_source(self):
        generated = self.root / 'src/generated'
        generated.mkdir()
        (generated / 'routes.ores').write_text('prior valid table')
        (self.routes / 'health/trace.ores').write_text('unsupported')
        with self.assertRaises(ValueError):
            generator.generate(3000, self.root / 'data')
        self.assertEqual((generated / 'routes.ores').read_text(), 'prior valid table')
        self.assertFalse((generated / 'config.ores').exists())

    def test_nonliteral_segment_is_rejected_before_publication(self):
        directory = self.routes / 'not.valid'
        directory.mkdir()
        (directory / 'get.ores').write_text('invalid segment')
        with self.assertRaises(ValueError):
            generator.generate(3000, self.root / 'data')
        self.assertFalse((self.root / 'src/generated/routes.ores').exists())

    def test_empty_route_tree_is_rejected_without_outputs(self):
        (self.routes / 'health/get.ores').unlink()
        with self.assertRaises(ValueError):
            generator.generate(3000, self.root / 'data')
        self.assertFalse((self.root / 'src/generated/routes.ores').exists())


class RoutePortAndStableOrder(unittest.TestCase):
    """Publisher invariants at both port bounds and across ordered routes."""
    setUp = RoutePublication.setUp

    def test_minimum_valid_port_publishes_literal_config(self):
        generator.generate(1, self.root / 'data')
        config = (self.root / 'src/generated/config.ores').read_text()
        self.assertIn('return 1;', config)
        self.assertIn('GET /health', (self.root / 'src/generated/routes.ores').read_text())

    def test_maximum_valid_port_publishes_literal_config(self):
        generator.generate(65535, self.root / 'data')
        config = (self.root / 'src/generated/config.ores').read_text()
        self.assertIn('return 65535;', config)

    def test_route_emission_order_is_stable_across_creation_order(self):
        zeta = self.root / 'src/routes/rest/zeta'
        alpha = self.root / 'src/routes/rest/alpha'
        zeta.mkdir()
        (zeta / 'post.ores').write_text('fixture')
        alpha.mkdir()
        (alpha / 'get.ores').write_text('fixture')
        generator.generate(3000, self.root / 'data')
        emitted = (self.root / 'src/generated/routes.ores').read_text()
        markers = ('GET /alpha', 'GET /health', 'POST /zeta')
        offsets = [emitted.index(marker) for marker in markers]
        self.assertEqual(offsets, sorted(offsets))



class SupportedMethodsAndPublisherAtomicity(unittest.TestCase):
    """Host-only source publication checks; no guest compiler execution."""
    setUp = RoutePublication.setUp

    def test_head_and_options_have_distinct_literal_routes(self):
        routes = self.root / 'src/routes/rest/health'
        (routes / 'head.ores').write_text('fixture')
        (routes / 'options.ores').write_text('fixture')
        generator.generate(3000, self.root / 'data')
        source = (self.root / 'src/generated/routes.ores').read_text()
        for method in ('GET', 'HEAD', 'OPTIONS'):
            with self.subTest(method=method):
                self.assertIn(f'{method} /health', source)

    def test_safe_hyphen_and_underscore_segment_is_literal(self):
        nested = self.root / 'src/routes/rest/team_alpha-3'
        nested.mkdir()
        (nested / 'post.ores').write_text('fixture')
        generator.generate(3000, self.root / 'data')
        source = (self.root / 'src/generated/routes.ores').read_text()
        self.assertIn('POST /team_alpha-3', source)
        self.assertIn('http_routing.literal("team_alpha-3")', source)

    def test_invalid_later_port_keeps_both_last_good_publications(self):
        generator.generate(3000, self.root / 'data')
        generated = self.root / 'src/generated'
        before = {name: (generated / name).read_bytes()
                  for name in ('routes.ores', 'config.ores')}
        with self.assertRaises(ValueError):
            generator.generate(65536, self.root / 'different-data')
        for name, original in before.items():
            with self.subTest(name=name):
                self.assertEqual((generated / name).read_bytes(), original)


if __name__=='__main__':
    unittest.main()
